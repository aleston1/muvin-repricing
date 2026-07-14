"""Sincronización multi-plataforma Muvin.

Compara el stock del ERP (Hansa, espejado en Google Sheets) contra las
publicaciones de Mercado Libre y Tiendanube por SKU raíz, y permite
publicar los productos faltantes con descripción estilo Muvin y fotos
aprobadas manualmente.
"""
from flask import Blueprint, jsonify, request
import requests
import csv
import io
import json
import os
import re
import urllib.parse

sync_bp = Blueprint("sync", __name__, url_prefix="/api/sync")

ML_BASE   = "https://api.mercadolibre.com"
TN_BASE   = "https://api.tiendanube.com/v1"
TN_UA     = os.environ.get("TN_USER_AGENT", "MuvinSync (aleston@muvin.com.ar)")

SHEET_ID  = os.environ.get("STOCK_SHEET_ID", "1ReMzkvfBbPYNmIcJLGQaCivvsf2-IZ2CadtRlcUgkZE")
SHEET_GID = os.environ.get("STOCK_SHEET_GID", "1134850759")

# Planilla "Productos pendientes de publicar": equivalencias de variantes,
# categorías de Tiendanube, marcas y URLs del fabricante.
EQUIV_SHEET_ID   = os.environ.get("EQUIV_SHEET_ID", "1eFOSU_uXME4AzqZs_-hJkB16xtEY82qiPO5KO2uCXRo")
EQUIV_CACHE_PATH = os.path.join(os.path.dirname(__file__), "equiv_cache.json")

# Cache en disco del último stock parseado (Heroku lo pierde al reiniciar,
# igual que costos.json — la fuente de verdad sigue siendo la planilla).
STOCK_CACHE_PATH = os.path.join(os.path.dirname(__file__), "stock_cache.json")


# ---------------------------------------------------------------- helpers ML

def ml_get(path, token, params=None):
    r = requests.get(ML_BASE + path, headers={"Authorization": f"Bearer {token}"},
                     params=params, timeout=20)
    r.raise_for_status()
    return r.json()


def ml_post(path, token, body):
    r = requests.post(ML_BASE + path, headers={"Authorization": f"Bearer {token}"},
                      json=body, timeout=30)
    return r


def sku_raiz(sku):
    if not sku:
        return None
    return str(sku).strip().split(".")[0].upper() or None


def gtins_de_item_ml(item):
    """Códigos de barras (GTIN/EAN) del item y sus variaciones, para cruzar
    con el ERP cuando la publicación no tiene SKU cargado."""
    gtins = set()
    for a in item.get("attributes") or []:
        if a.get("id") in ("GTIN", "EAN") and a.get("value_name"):
            gtins.add(str(a["value_name"]).strip())
    for v in item.get("variations") or []:
        for a in v.get("attributes") or []:
            if a.get("id") in ("GTIN", "EAN") and a.get("value_name"):
                gtins.add(str(a["value_name"]).strip())
        for a in v.get("attribute_combinations") or []:
            if a.get("id") in ("GTIN", "EAN") and a.get("value_name"):
                gtins.add(str(a["value_name"]).strip())
    return gtins


def ml_listar_ids(token, user_id, status):
    """Todos los IDs de items del vendedor. Usa scan (sin tope) y cae a
    paginación por offset (tope 1000) si scan no está disponible."""
    ids, scroll, vueltas = [], None, 0
    try:
        while vueltas < 300:
            vueltas += 1
            params = {"status": status, "limit": 100, "search_type": "scan"}
            if scroll:
                params["scroll_id"] = scroll
            data = ml_get(f"/users/{user_id}/items/search", token, params)
            res = data.get("results", [])
            if not res:
                break
            ids += res
            scroll = data.get("scroll_id") or scroll
            total = data.get("paging", {}).get("total", 0)
            if total and len(ids) >= total:
                break
        return ids
    except requests.HTTPError:
        ids, offset = [], 0
        while True:
            data = ml_get(f"/users/{user_id}/items/search", token,
                          {"status": status, "limit": 100, "offset": offset})
            res = data.get("results", [])
            ids += res
            total = data.get("paging", {}).get("total", 0)
            offset += 100
            if not res or offset >= min(total, 1000):
                break
        return ids


def skus_de_item_ml(item):
    """Junta los SKUs del item y de sus variaciones."""
    skus = set()
    if item.get("seller_custom_field"):
        skus.add(str(item["seller_custom_field"]).strip())
    for a in item.get("attributes") or []:
        if a.get("id") == "SELLER_SKU" and a.get("value_name"):
            skus.add(str(a["value_name"]).strip())
    for v in item.get("variations") or []:
        if v.get("seller_custom_field"):
            skus.add(str(v["seller_custom_field"]).strip())
        for a in v.get("attributes") or []:
            if a.get("id") == "SELLER_SKU" and a.get("value_name"):
                skus.add(str(a["value_name"]).strip())
    return skus


# ---------------------------------------------------------------- helpers TN

def tn_headers(token):
    return {"Authentication": f"bearer {token}",
            "User-Agent": TN_UA,
            "Content-Type": "application/json"}


def tn_nombre(name):
    """El name de Tiendanube es un dict por idioma."""
    if isinstance(name, dict):
        return name.get("es") or next(iter(name.values()), "")
    return name or ""


# ------------------------------------------------------------- stock (Hansa)

def parse_num(v):
    if v is None:
        return 0.0
    s = str(v).strip()
    if not s:
        return 0.0
    try:
        return float(s)
    except ValueError:
        try:
            return float(s.replace(".", "").replace(",", "."))
        except ValueError:
            return 0.0


def parse_stock_rows(rows):
    """Filas crudas de la planilla -> productos agrupados por SKU raíz.

    Columnas: código, descripción, código de barras, 3 columnas de stock
    vendible y una columna de notas (NO-VENTA, TALLER, etc. — se ignora
    para el stock vendible).
    """
    productos = {}
    for row in rows:
        codigo = (row[0] if len(row) > 0 else "") or ""
        codigo = str(codigo).strip()
        if not codigo or "sincronización" in codigo.lower() or codigo.lower().startswith("código"):
            continue
        nombre  = str((row[1] if len(row) > 1 else "") or "").strip()
        barcode = str((row[2] if len(row) > 2 else "") or "").strip()
        stock   = sum(parse_num(row[i]) for i in (3, 4, 5) if len(row) > i)
        raiz, _, sufijo = codigo.partition(".")
        raiz = raiz.strip().upper()
        p = productos.setdefault(raiz, {
            "sku_raiz": raiz, "nombre": "", "variantes": [], "stock_total": 0.0,
        })
        if nombre and not p["nombre"]:
            p["nombre"] = nombre
        p["variantes"].append({
            "sku": codigo, "sufijo": sufijo.strip(), "barcode": barcode,
            "stock": stock, "nombre": nombre,
        })
        p["stock_total"] += stock
    for p in productos.values():
        p["tiene_variantes"] = any(v["sufijo"] for v in p["variantes"])
    return productos


def fetch_stock_sheet(sheet_id, gid):
    url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid}"
    r = requests.get(url, timeout=30, allow_redirects=True)
    ct = r.headers.get("content-type", "")
    if r.status_code != 200 or "text/html" in ct:
        raise RuntimeError(
            "No se pudo leer la planilla. Verificá que esté compartida como "
            "'Cualquier persona con el enlace: Lector', o subí el CSV manualmente."
        )
    text = r.content.decode("utf-8-sig", errors="replace")
    return list(csv.reader(io.StringIO(text)))


@sync_bp.route("/stock")
def get_stock():
    sheet_id = request.args.get("sheet_id", SHEET_ID)
    gid      = request.args.get("gid", SHEET_GID)
    try:
        rows = fetch_stock_sheet(sheet_id, gid)
        productos = parse_stock_rows(rows)
        try:
            with open(STOCK_CACHE_PATH, "w") as f:
                json.dump(productos, f)
        except OSError:
            pass
        return jsonify({"productos": productos, "total_skus": len(productos), "fuente": "sheet"})
    except Exception as e:
        # Fallback al último parse guardado
        if os.path.exists(STOCK_CACHE_PATH):
            with open(STOCK_CACHE_PATH) as f:
                productos = json.load(f)
            return jsonify({"productos": productos, "total_skus": len(productos),
                            "fuente": "cache", "warning": str(e)})
        return jsonify({"error": str(e)}), 502


@sync_bp.route("/stock", methods=["POST"])
def upload_stock():
    """Fallback: subir el CSV exportado de la planilla a mano."""
    if "file" not in request.files:
        return jsonify({"error": "No se encontró el archivo"}), 400
    f = request.files["file"]
    try:
        name = (f.filename or "").lower()
        if name.endswith(".xlsx"):
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(f.read()), read_only=True)
            rows = [list(r) for r in wb.active.iter_rows(values_only=True)]
        else:
            text = f.read().decode("utf-8-sig", errors="replace")
            rows = list(csv.reader(io.StringIO(text)))
        productos = parse_stock_rows(rows)
        try:
            with open(STOCK_CACHE_PATH, "w") as cf:
                json.dump(productos, cf)
        except OSError:
            pass
        return jsonify({"productos": productos, "total_skus": len(productos), "fuente": "upload"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ------------------------------------------------------ equivalencias Hansa

# La planilla de equivalencias tiene celdas con mojibake (UTF-8 leído mal al
# importar desde Hansa): '√±' es 'ñ', '√©' es 'é', etc. Se corrige al leer.
_MOJIBAKE = {"√±": "ñ", "√ë": "Ñ", "√°": "á", "√©": "é", "√≠": "í",
             "√≥": "ó", "√∫": "ú", "√º": "ü", "¬∞": "°"}


def _celda(row, i):
    if len(row) <= i or row[i] is None:
        return ""
    s = str(row[i]).strip()
    for feo, bien in _MOJIBAKE.items():
        if feo in s:
            s = s.replace(feo, bien)
    return s


@sync_bp.route("/equivalencias")
def equivalencias():
    """Lee la planilla de equivalencias: variantes (código -> nombre y tipo),
    categorías TN por tipo de producto, marca por SKU madre y URL del
    fabricante (solapa SLUGS)."""
    sheet_id = request.args.get("sheet_id", EQUIV_SHEET_ID)
    try:
        url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=xlsx"
        r = requests.get(url, timeout=30, allow_redirects=True)
        if r.status_code != 200 or "text/html" in r.headers.get("content-type", ""):
            raise RuntimeError(
                "No se pudo leer la planilla de equivalencias. Verificá que esté "
                "compartida como 'Cualquier persona con el enlace: Lector'."
            )
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(r.content), read_only=True, data_only=True)

        def hoja(nombre):
            for ws in wb.worksheets:
                if ws.title.strip().lower() == nombre:
                    return ws
            return None

        data = {"variantes": {}, "categorias_tn": {}, "marcas": {}, "slugs": {}}

        ws = hoja("variantes")
        if ws:
            for row in list(ws.iter_rows(values_only=True))[1:]:
                cod = _celda(row, 0)
                if cod:
                    data["variantes"][cod.upper()] = {
                        "nombre": _celda(row, 1) or cod,
                        "tipo": _celda(row, 2).upper() or "COLOR",
                    }

        ws = hoja("categorias tn")
        if ws:
            for row in list(ws.iter_rows(values_only=True))[1:]:
                nombre = _celda(row, 1)
                cats = []
                for i in (2, 4, 6):
                    cid = _celda(row, i)
                    if cid:
                        try:
                            cats.append({"id": int(float(cid)), "nombre": _celda(row, i + 1)})
                        except ValueError:
                            pass
                if nombre and cats:
                    data["categorias_tn"][nombre.lower()] = cats
                    cod = _celda(row, 0)
                    if cod:
                        data["categorias_tn"][cod.upper()] = cats

        ws = hoja("publicar")
        if ws:
            for row in list(ws.iter_rows(values_only=True))[1:]:
                sku_madre, marca = _celda(row, 1), _celda(row, 7)
                if sku_madre and marca:
                    data["marcas"][sku_madre.upper()] = marca

        ws = hoja("slugs")
        if ws:
            for row in list(ws.iter_rows(values_only=True))[1:]:
                sku, slug, base = _celda(row, 0), _celda(row, 2), _celda(row, 3)
                if sku and base:
                    full = base + slug if base.endswith("/") else (base.rstrip("/") + "/" + slug if slug else base)
                    data["slugs"][sku.upper()] = {"url": full, "notas": _celda(row, 4)}

        try:
            with open(EQUIV_CACHE_PATH, "w") as f:
                json.dump(data, f)
        except OSError:
            pass
        return jsonify(data)
    except Exception as e:
        if os.path.exists(EQUIV_CACHE_PATH):
            with open(EQUIV_CACHE_PATH) as f:
                data = json.load(f)
            data["warning"] = str(e)
            return jsonify(data)
        return jsonify({"error": str(e)}), 502


# ------------------------------------------------------------- Mercado Libre

@sync_bp.route("/ml")
def get_ml():
    token   = request.args.get("token", os.environ.get("ML_TOKEN", ""))
    user_id = request.args.get("user_id", os.environ.get("ML_USER_ID", "246901020"))
    if not token:
        return jsonify({"error": "Falta el token de Mercado Libre"}), 400
    try:
        ids = []
        for status in ("active", "paused"):
            ids += ml_listar_ids(token, user_id, status)
        items_por_raiz = {}
        sin_sku = []
        for i in range(0, len(ids), 20):
            chunk = ids[i:i + 20]
            # include_attributes=all: sin esto ML recorta atributos (incluido
            # el SELLER_SKU de las variaciones) en las consultas multiget
            details = ml_get("/items", token, {"ids": ",".join(chunk), "include_attributes": "all"})
            for x in details:
                if x.get("code") != 200:
                    continue
                item = x["body"]
                resumen = {"id": item.get("id"), "title": item.get("title"),
                           "status": item.get("status"), "permalink": item.get("permalink"),
                           "price": item.get("price"),
                           "gtins": sorted(gtins_de_item_ml(item))}
                raices = {sku_raiz(s) for s in skus_de_item_ml(item)} - {None}
                if not raices:
                    sin_sku.append(resumen)
                for r in raices:
                    items_por_raiz.setdefault(r, []).append(resumen)
        return jsonify({"items_por_raiz": items_por_raiz, "sin_sku": sin_sku,
                        "total_items": len(ids)})
    except requests.HTTPError as e:
        return jsonify({"error": f"Mercado Libre: {e.response.status_code} {e.response.text[:300]}"}), 502
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@sync_bp.route("/ml/ids")
def ml_ids():
    """Solo los IDs de publicaciones (rápido). El detalle se pide por tandas
    con /ml/detalles para no exceder el timeout del servidor."""
    token   = request.args.get("token", os.environ.get("ML_TOKEN", ""))
    user_id = request.args.get("user_id", os.environ.get("ML_USER_ID", "246901020"))
    if not token:
        return jsonify({"error": "Falta el token de Mercado Libre"}), 400
    try:
        ids = []
        for status in ("active", "paused"):
            ids += ml_listar_ids(token, user_id, status)
        return jsonify({"ids": ids})
    except requests.HTTPError as e:
        return jsonify({"error": f"Mercado Libre: {e.response.status_code} {e.response.text[:300]}"}), 502
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@sync_bp.route("/ml/detalles", methods=["POST"])
def ml_detalles():
    """Detalle (SKUs, GTINs, estado) de hasta 500 publicaciones por llamada."""
    body  = request.json or {}
    token = body.get("token", os.environ.get("ML_TOKEN", ""))
    ids   = body.get("ids") or []
    if not token or not ids:
        return jsonify({"error": "Faltan token o ids"}), 400
    ids = ids[:500]
    try:
        out = []
        for i in range(0, len(ids), 20):
            chunk = ids[i:i + 20]
            # include_attributes=all: sin esto ML recorta atributos (incluido
            # el SELLER_SKU de las variaciones) en las consultas multiget
            details = ml_get("/items", token, {"ids": ",".join(chunk), "include_attributes": "all"})
            for x in details:
                if x.get("code") != 200:
                    continue
                item = x["body"]
                resumen = {"id": item.get("id"), "title": item.get("title"),
                           "status": item.get("status"), "permalink": item.get("permalink"),
                           "price": item.get("price"),
                           "gtins": sorted(gtins_de_item_ml(item))}
                raices = sorted({sku_raiz(s) for s in skus_de_item_ml(item)} - {None})
                out.append({"resumen": resumen, "raices": raices})
        return jsonify({"items": out})
    except requests.HTTPError as e:
        return jsonify({"error": f"Mercado Libre: {e.response.status_code} {e.response.text[:300]}"}), 502
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@sync_bp.route("/ml/debug")
def ml_debug():
    """Item completo de ML (con todos los atributos) para diagnosticar por
    qué una publicación no cruza con el ERP."""
    token   = request.args.get("token", os.environ.get("ML_TOKEN", ""))
    item_id = request.args.get("item_id", "")
    if not token or not item_id:
        return jsonify({"error": "Faltan token o item_id"}), 400
    try:
        item = ml_get(f"/items/{item_id}", token, {"include_attributes": "all"})
        return jsonify({
            "id": item.get("id"),
            "title": item.get("title"),
            "seller_custom_field": item.get("seller_custom_field"),
            "skus_detectados": sorted(skus_de_item_ml(item)),
            "gtins_detectados": sorted(gtins_de_item_ml(item)),
            "attributes": [{"id": a.get("id"), "value_name": a.get("value_name")}
                           for a in item.get("attributes") or []],
            "variations": [{
                "id": v.get("id"),
                "seller_custom_field": v.get("seller_custom_field"),
                "attributes": [{"id": a.get("id"), "value_name": a.get("value_name")}
                               for a in v.get("attributes") or []],
                "attribute_combinations": [{"id": a.get("id"), "value_name": a.get("value_name")}
                                           for a in v.get("attribute_combinations") or []],
            } for v in item.get("variations") or []],
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@sync_bp.route("/ml/token", methods=["POST"])
def ml_token():
    """Canjea el code de OAuth (o un refresh_token) por un access token de ML."""
    body   = request.json or {}
    app_id = body.get("app_id", "").strip()
    secret = body.get("secret", "").strip()
    if not app_id or not secret:
        return jsonify({"error": "Faltan App ID o Client Secret de Mercado Libre"}), 400
    payload = {"client_id": app_id, "client_secret": secret}
    if body.get("code"):
        payload.update({"grant_type": "authorization_code", "code": body["code"],
                        "redirect_uri": body.get("redirect_uri", "")})
    elif body.get("refresh_token"):
        payload.update({"grant_type": "refresh_token", "refresh_token": body["refresh_token"]})
    else:
        return jsonify({"error": "Falta el code o el refresh_token"}), 400
    try:
        r = requests.post(ML_BASE + "/oauth/token", data=payload,
                          headers={"Accept": "application/json"}, timeout=20)
    except requests.RequestException as e:
        return jsonify({"error": f"No se pudo contactar a Mercado Libre: {e}"}), 502
    if r.status_code != 200:
        try:
            detail = r.json()
        except ValueError:
            detail = r.text[:300]
        return jsonify({"error": "Mercado Libre rechazó la autorización", "detalle": detail}), 502
    d = r.json()
    return jsonify({"access_token": d.get("access_token"),
                    "refresh_token": d.get("refresh_token"),
                    "expires_in": d.get("expires_in"),
                    "user_id": d.get("user_id")})


@sync_bp.route("/ml/categoria")
def ml_categoria():
    """Predicción de categoría de ML para un título."""
    token = request.args.get("token", os.environ.get("ML_TOKEN", ""))
    q = request.args.get("q", "")
    if not q:
        return jsonify({"error": "Falta q"}), 400
    try:
        data = ml_get("/sites/MLA/domain_discovery/search", token, {"q": q, "limit": 3})
        return jsonify({"predicciones": [
            {"category_id": d.get("category_id"), "category_name": d.get("category_name"),
             "domain_name": d.get("domain_name")}
            for d in data
        ]})
    except Exception as e:
        return jsonify({"predicciones": [], "error": str(e)})


@sync_bp.route("/fotos")
def buscar_fotos():
    """Candidatas de fotos: catálogo oficial de ML + publicaciones existentes
    del mismo producto. El humano elige cuáles usar en el wizard."""
    token = request.args.get("token", os.environ.get("ML_TOKEN", ""))
    q     = request.args.get("q", "")
    if not q:
        return jsonify({"error": "Falta q"}), 400
    fotos, vistos = [], set()
    try:
        data    = ml_get("/sites/MLA/search", token, {"q": q, "limit": 6})
        results = data.get("results", [])
        # Primero las fotos del catálogo oficial de ML (las más seguras de usar)
        for r in results:
            cpid = r.get("catalog_product_id")
            if not cpid or cpid in vistos:
                continue
            vistos.add(cpid)
            try:
                prod = ml_get(f"/products/{cpid}", token)
                for p in (prod.get("pictures") or [])[:4]:
                    u = p.get("url")
                    if u and u not in vistos:
                        vistos.add(u)
                        fotos.append({"url": u, "fuente": "Catálogo ML — " + (prod.get("name") or "")})
            except Exception:
                pass
        # Después las de publicaciones existentes
        ids = [r["id"] for r in results if r.get("id")][:6]
        if ids:
            details = ml_get("/items", token, {"ids": ",".join(ids), "attributes": "id,title,pictures"})
            for x in details:
                if x.get("code") != 200:
                    continue
                it = x["body"]
                for p in (it.get("pictures") or [])[:3]:
                    u = p.get("secure_url") or p.get("url")
                    if u and u not in vistos:
                        vistos.add(u)
                        fotos.append({"url": u, "fuente": it.get("title") or ""})
        return jsonify({"fotos": fotos[:16]})
    except Exception as e:
        return jsonify({"fotos": [], "error": str(e)})


@sync_bp.route("/publish/ml", methods=["POST"])
def publish_ml():
    body  = request.json or {}
    token = body.get("token", os.environ.get("ML_TOKEN", ""))
    item  = body.get("item")
    descripcion = body.get("descripcion", "")
    if not token or not item:
        return jsonify({"error": "Faltan token o item"}), 400
    try:
        r = ml_post("/items", token, item)
    except requests.RequestException as e:
        return jsonify({"error": f"No se pudo contactar a Mercado Libre: {e}"}), 502
    if r.status_code not in (200, 201):
        try:
            detail = r.json()
        except ValueError:
            detail = r.text[:500]
        return jsonify({"error": "Mercado Libre rechazó la publicación", "detalle": detail}), 502
    creado = r.json()
    item_id = creado.get("id")
    desc_warning = None
    if descripcion and item_id:
        rd = ml_post(f"/items/{item_id}/description", token, {"plain_text": descripcion})
        if rd.status_code not in (200, 201):
            desc_warning = f"El item se creó pero la descripción falló: {rd.text[:300]}"
    return jsonify({"ok": True, "id": item_id, "permalink": creado.get("permalink"),
                    "warning": desc_warning})


# ---------------------------------------------------------------- Tiendanube

@sync_bp.route("/tn/token", methods=["POST"])
def tn_token():
    """Canjea el code de OAuth de Tiendanube por un access token (no vence)."""
    body   = request.json or {}
    app_id = body.get("app_id", "").strip()
    secret = body.get("secret", "").strip()
    code   = body.get("code", "").strip()
    if not app_id or not secret or not code:
        return jsonify({"error": "Faltan App ID, Client Secret o code de Tiendanube"}), 400
    try:
        r = requests.post("https://www.tiendanube.com/apps/authorize/token",
                          data={"client_id": app_id, "client_secret": secret,
                                "grant_type": "authorization_code", "code": code},
                          headers={"Accept": "application/json"}, timeout=20)
    except requests.RequestException as e:
        return jsonify({"error": f"No se pudo contactar a Tiendanube: {e}"}), 502
    try:
        d = r.json()
    except ValueError:
        d = {}
    if r.status_code != 200 or not d.get("access_token"):
        detail = d.get("error_description") or d.get("error") or r.text[:300]
        return jsonify({"error": "Tiendanube rechazó la autorización", "detalle": detail}), 502
    return jsonify({"access_token": d["access_token"], "store_id": d.get("user_id")})


@sync_bp.route("/tn")
def get_tn():
    """Una página de productos de Tiendanube por llamada (la UI itera con
    progreso, para no exceder el timeout del servidor)."""
    store_id = request.args.get("store_id", os.environ.get("TN_STORE_ID", ""))
    token    = request.args.get("token", os.environ.get("TN_TOKEN", ""))
    page     = int(request.args.get("page", 1))
    if not store_id or not token:
        return jsonify({"error": "Faltan store_id o token de Tiendanube"}), 400
    try:
        r = requests.get(f"{TN_BASE}/{store_id}/products",
                         headers=tn_headers(token),
                         params={"page": page, "per_page": 200,
                                 "fields": "id,name,canonical_url,published,variants"},
                         timeout=30)
        if r.status_code == 404:  # última página
            return jsonify({"items": [], "has_more": False, "page": page})
        r.raise_for_status()
        products = r.json()
        items = []
        for p in products:
            resumen = {"id": p.get("id"), "name": tn_nombre(p.get("name")),
                       "published": p.get("published"), "url": p.get("canonical_url")}
            raices = sorted({sku_raiz(v.get("sku")) for v in p.get("variants") or []} - {None})
            items.append({"resumen": resumen, "raices": raices})
        return jsonify({"items": items, "has_more": len(products) == 200, "page": page})
    except requests.HTTPError as e:
        return jsonify({"error": f"Tiendanube: {e.response.status_code} {e.response.text[:300]}"}), 502
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@sync_bp.route("/publish/tn", methods=["POST"])
def publish_tn():
    body     = request.json or {}
    store_id = body.get("store_id", os.environ.get("TN_STORE_ID", ""))
    token    = body.get("token", os.environ.get("TN_TOKEN", ""))
    product  = body.get("product")
    if not store_id or not token or not product:
        return jsonify({"error": "Faltan store_id, token o product"}), 400
    try:
        r = requests.post(f"{TN_BASE}/{store_id}/products",
                          headers=tn_headers(token), json=product, timeout=30)
    except requests.RequestException as e:
        return jsonify({"error": f"No se pudo contactar a Tiendanube: {e}"}), 502
    if r.status_code not in (200, 201):
        try:
            detail = r.json()
        except ValueError:
            detail = r.text[:500]
        return jsonify({"error": "Tiendanube rechazó la publicación", "detalle": detail}), 502
    creado = r.json()
    return jsonify({"ok": True, "id": creado.get("id"),
                    "url": creado.get("canonical_url")})


# ------------------------------------------------- borrador y descripciones

def titulo_desde_nombre(nombre):
    """'Candado, U-Lock - Kryptonite Evolution LS - Anaranjado de 12' ->
    'Candado U-Lock Kryptonite Evolution LS Anaranjado de 12'"""
    partes = [p.strip() for p in nombre.split(" - ") if p.strip()]
    if partes:
        partes[0] = partes[0].replace(",", "")
    titulo = " ".join(partes)
    titulo = re.sub(r"\s+", " ", titulo).strip()
    return titulo[:60].strip()


@sync_bp.route("/draft", methods=["POST"])
def draft():
    body   = request.json or {}
    nombre = body.get("nombre", "")
    titulo = titulo_desde_nombre(nombre)
    q = urllib.parse.quote_plus(titulo or nombre)
    return jsonify({
        "titulo": titulo,
        "photo_search": {
            "google": f"https://www.google.com/search?tbm=isch&q={q}",
            "bing": f"https://www.bing.com/images/search?q={q}",
            "duckduckgo": f"https://duckduckgo.com/?iax=images&ia=images&q={q}",
        },
    })


ESTILO_MUVIN = """\
¿Desplazamiento diario entre semana o aventuras de fin de semana? ¿Una aventura \
de tres días o una vuelta al mundo de 12 meses? La Four Corners 1 ahora viene con \
una nueva transmisión 2x9 MicroShift Sword que ofrece mayor fiabilidad y una gama \
más amplia de marchas. El cuadro y la horquilla de acero CroMo 4130 conificados \
están diseñados para ser cómodos en terrenos difíciles, pero también para viajes \
con carga completa. Hemos incluido seis soportes para botellas, ojales para \
portabultos y guardabarros, soportes para horquilla lowrider, amplio espacio libre \
para neumáticos y frenos de disco para que puedas afrontar cualquier condición y \
terreno.

La vida se trata del viaje, no del destino, y para eso está la Four Corners.

- Cuadro: CrMo Serie 1, geometría biométrica, soportes para guardabarros y portapaquetes
- Horquilla: Serie 1 CrMo, ojales para portabotellas, montaje de disco IS
- Frenos: Disco mecánico de carretera Tektro Spyre-C, rotor de 160 mm
- Cadena: KMC X9"""

PROMPT_SISTEMA = """Sos el redactor de Muvin (muvin.com.ar), una tienda argentina \
de bicicletas urbanas y plegables, movilidad eléctrica y accesorios de ciclismo.

Escribís descripciones de producto en español rioplatense con esta estructura:
1. Uno o dos párrafos de apertura narrativa que conectan el producto con el uso \
real del ciclista urbano o viajero (preguntas retóricas, escenarios de uso, \
beneficios concretos). Cálido pero sin exagerar, sin emojis y sin superlativos vacíos.
2. Opcionalmente una frase de cierre con personalidad.
3. Una lista de especificaciones, cada línea con el formato "- Componente: detalle".

Ejemplo del estilo (bicicleta Marin Four Corners):
---
{ejemplo}
---

Reglas estrictas:
- NO inventes especificaciones técnicas que no estén en los datos provistos. \
Si solo tenés el nombre, la marca y el color, la lista lleva solo eso.
- No menciones precio, stock ni envío.
- Respondé SOLO con el texto de la descripción, sin encabezados ni comentarios."""


def descripcion_plantilla(nombre, titulo):
    partes = [p.strip() for p in (nombre or "").split(" - ") if p.strip()]
    categoria = re.sub(r"\s+", " ", partes[0].replace(",", " ")).strip() if partes else "Producto"
    detalle = partes[2] if len(partes) > 2 else ""
    marca_modelo = partes[1] if len(partes) > 1 else titulo
    lineas = [
        f"{titulo}.",
        "",
        f"Sumá a tu bici un {categoria.lower()} pensado para el uso urbano de todos los días.",
        "",
        f"- Producto: {categoria}",
        f"- Marca y modelo: {marca_modelo}",
    ]
    if detalle:
        lineas.append(f"- Detalle: {detalle}")
    return "\n".join(lineas)


def texto_a_html(texto):
    html_partes = []
    bullets = []
    for linea in texto.splitlines():
        s = linea.strip()
        if s.startswith("- "):
            bullets.append(f"<li>{s[2:].strip()}</li>")
            continue
        if bullets:
            html_partes.append("<ul>" + "".join(bullets) + "</ul>")
            bullets = []
        if s:
            html_partes.append(f"<p>{s}</p>")
    if bullets:
        html_partes.append("<ul>" + "".join(bullets) + "</ul>")
    return "".join(html_partes)


@sync_bp.route("/describe", methods=["POST"])
def describe():
    body    = request.json or {}
    nombre  = body.get("nombre", "")
    titulo  = body.get("titulo") or titulo_desde_nombre(nombre)
    datos   = body.get("datos", "")  # specs/notas extra que cargue el usuario
    api_key = body.get("api_key") or os.environ.get("ANTHROPIC_API_KEY", "")

    if not api_key:
        texto = descripcion_plantilla(nombre, titulo)
        return jsonify({"texto": texto, "html": texto_a_html(texto),
                        "generado_con_ia": False,
                        "warning": "Sin API key de Anthropic: se usó una plantilla básica. "
                                   "Cargá una key en Configuración para generar con IA."})
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        contenido = f"Nombre interno del producto (ERP): {nombre}\nTítulo de la publicación: {titulo}"
        if datos:
            contenido += f"\nDatos y especificaciones adicionales:\n{datos}"
        resp = client.messages.create(
            model=os.environ.get("ANTHROPIC_MODEL", "claude-opus-4-8"),
            max_tokens=2048,
            system=PROMPT_SISTEMA.format(ejemplo=ESTILO_MUVIN),
            messages=[{"role": "user", "content": contenido}],
        )
        texto = next((b.text for b in resp.content if b.type == "text"), "").strip()
        if not texto:
            raise RuntimeError("La API no devolvió texto")
        return jsonify({"texto": texto, "html": texto_a_html(texto), "generado_con_ia": True})
    except Exception as e:
        texto = descripcion_plantilla(nombre, titulo)
        return jsonify({"texto": texto, "html": texto_a_html(texto),
                        "generado_con_ia": False,
                        "warning": f"Falló la generación con IA ({e}); se usó la plantilla."})
