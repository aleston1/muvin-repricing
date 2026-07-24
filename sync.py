"""Sincronización multi-plataforma Muvin.

Compara el stock del ERP (Hansa, espejado en Google Sheets) contra las
publicaciones de Mercado Libre y Tiendanube por SKU raíz, y permite
publicar los productos faltantes con descripción estilo Muvin y fotos
aprobadas manualmente.
"""
from flask import Blueprint, jsonify, request
import requests
import base64
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


# ------------------------------------------------------------ fotos (re-hosting)
# Muchos sitios bloquean que terceros descarguen sus imágenes (hotlink).
# Bajamos la imagen nosotros con UA de navegador y la subimos como archivo
# propio a cada plataforma.

BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/125.0 Safari/537.36")


def descargar_imagen(url):
    r = requests.get(url, headers={"User-Agent": BROWSER_UA, "Accept": "image/*,*/*;q=0.8",
                                   "Referer": url},
                     timeout=25, allow_redirects=True)
    r.raise_for_status()
    ct = (r.headers.get("content-type") or "").split(";")[0].strip().lower()
    if not ct.startswith("image/") or len(r.content) < 1000:
        raise RuntimeError(f"la URL no devuelve una imagen ({ct or 'sin tipo'})")
    if len(r.content) > 10 * 1024 * 1024:
        raise RuntimeError("imagen de más de 10 MB")
    ext = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp",
           "image/gif": "gif"}.get(ct, "jpg")
    return r.content, ct, ext


def ml_subir_foto(token, url):
    """Descarga la imagen y la sube al hosting de fotos de ML. Devuelve el
    picture id, o None si no se pudo (se usará la URL original)."""
    try:
        contenido, ct, ext = descargar_imagen(url)
        r = requests.post(ML_BASE + "/pictures/items/upload",
                          headers={"Authorization": f"Bearer {token}"},
                          files={"file": (f"foto.{ext}", contenido, ct)}, timeout=60)
        if r.status_code in (200, 201):
            return r.json().get("id")
    except Exception:
        pass
    return None


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
    paginación por offset (tope 1000) si scan no funciona o no avanza."""
    ids, vistos, scroll = [], set(), None
    scan_completo = False
    try:
        for _ in range(300):
            params = {"status": status, "limit": 100, "search_type": "scan"}
            if scroll:
                params["scroll_id"] = scroll
            data = ml_get(f"/users/{user_id}/items/search", token, params)
            res = data.get("results", [])
            total = data.get("paging", {}).get("total", 0)
            if not res:
                scan_completo = True
                break
            nuevos = [x for x in res if x not in vistos]
            ids += nuevos
            vistos.update(nuevos)
            if total and len(ids) >= total:
                scan_completo = True
                break
            nuevo_scroll = data.get("scroll_id")
            if not nuevo_scroll or not nuevos:
                # scan sin cursor o repitiendo la misma página: no sirve
                break
            scroll = nuevo_scroll
    except requests.HTTPError:
        pass
    if scan_completo:
        return ids
    # Fallback: offset clásico (tope 1000 por estado)
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


def limpiar_codigo(v):
    """'2027.0' -> '2027' (números que la planilla formatea como float)."""
    s = str(v or "").strip()
    if s.endswith(".0") and s[:-2].replace(".", "", 1).isdigit():
        s = s[:-2]
    return s


def parse_maestro_alt(rows):
    """Solapa 'Maestro Items' -> {SKU: {alt, grupos, ml_ids, tn_id}}.

    alt = Código Alternativo (código del fabricante); grupos = códigos de
    clasificación (Grupos Display); ml_ids / tn_id = vínculos que Hansa ya
    tiene con las publicaciones de cada plataforma.
    """
    maestro, idx = {}, None
    for row in rows:
        vals = [str(c).strip() if c is not None else "" for c in row]
        if idx is None:
            if "Código Alternativo" in vals:
                idx = {
                    "alt": vals.index("Código Alternativo"),
                    "grupos": vals.index("Grupos Display") if "Grupos Display" in vals else None,
                    "ml": vals.index("ML IDs") if "ML IDs" in vals else None,
                    "tn": vals.index("TN ID") if "TN ID" in vals else None,
                    # Lista de precios de venta (ej: "Precio Lista 1") si la
                    # planilla la incorpora; el Precio Costo no cuenta
                    "precio": next((i for i, v in enumerate(vals)
                                    if v.lower().startswith("precio")
                                    and "costo" not in v.lower()), None),
                }
            continue
        cod = vals[0] if vals else ""
        if not cod:
            continue

        def celda(i):
            return vals[i] if i is not None and len(vals) > i else ""

        ml_ids = [x.strip() for x in celda(idx["ml"]).split(",")
                  if x.strip().upper().startswith("MLA")]
        tn_raw = celda(idx["tn"])
        tn_id = ""
        if tn_raw.upper().startswith("OK"):
            digitos = re.sub(r"\D", "", tn_raw)
            tn_id = digitos or "ok"
        maestro[cod.upper()] = {
            "alt": limpiar_codigo(celda(idx["alt"])),
            "grupos": [g.strip().upper() for g in celda(idx["grupos"]).split(",") if g.strip()],
            "ml_ids": ml_ids,
            "tn_id": tn_id,
            "precio": parse_num(celda(idx["precio"])),
        }
    return maestro


def parse_precios_retail(rows):
    """Solapa 'Precios retail' (Item | Nombre | Unidad | IVA Incl.) ->
    {SKU raíz: precio de venta con IVA}."""
    precios, idx_precio = {}, None
    for row in rows:
        vals = [str(c).strip() if c is not None else "" for c in row]
        if idx_precio is None:
            if any("iva" in v.lower() for v in vals):
                idx_precio = next(i for i, v in enumerate(vals) if "iva" in v.lower())
            continue
        cod = vals[0] if vals else ""
        p = parse_num(vals[idx_precio]) if len(vals) > idx_precio else 0
        if cod and p > 0:
            precios[cod.upper()] = p
    return precios


def aplicar_alt(productos, maestro, precios=None):
    """Vuelca los datos del Maestro (alt, grupos, vínculos ML/TN) en cada
    producto y variante."""
    for p in productos.values():
        grupos, ml_ids, tn_id = set(), set(), ""
        for v in p["variantes"]:
            m = maestro.get(v["sku"].upper()) or {}
            v["alt"] = m.get("alt", "")
            grupos.update(m.get("grupos") or [])
            ml_ids.update(m.get("ml_ids") or [])
            tn_id = tn_id or m.get("tn_id", "")
        raiz = maestro.get(p["sku_raiz"]) or {}
        grupos.update(raiz.get("grupos") or [])
        ml_ids.update(raiz.get("ml_ids") or [])
        p["alt"] = raiz.get("alt", "") or next(
            (v["alt"] for v in p["variantes"] if v.get("alt")), "")
        p["grupos"] = sorted(grupos)
        p["ml_ids_hansa"] = sorted(ml_ids)
        p["tn_id_hansa"] = tn_id or raiz.get("tn_id", "")
        # Precio: primero la lista retail (por SKU raíz — las variantes
        # comparten precio), después alguna columna de precio del Maestro
        p["precio"] = (precios or {}).get(p["sku_raiz"], 0) or raiz.get("precio") or max(
            (maestro.get(v["sku"].upper(), {}).get("precio", 0) for v in p["variantes"]),
            default=0)
    return productos


def fetch_stock_xlsx(sheet_id):
    """Baja el workbook completo: solapa Stock + Maestro Items (alt codes)."""
    url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=xlsx"
    r = requests.get(url, timeout=60, allow_redirects=True)
    if r.status_code != 200 or "text/html" in r.headers.get("content-type", ""):
        raise RuntimeError(
            "No se pudo leer la planilla. Verificá que esté compartida como "
            "'Cualquier persona con el enlace: Lector', o subí el archivo manualmente."
        )
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(r.content), read_only=True, data_only=True)
    stock_rows, alt_map, precios = None, {}, {}
    for ws in wb.worksheets:
        titulo = ws.title.strip().lower()
        if titulo == "stock" or (stock_rows is None and "stock" in titulo):
            stock_rows = [list(row) for row in ws.iter_rows(values_only=True)]
        elif "maestro" in titulo:
            alt_map = parse_maestro_alt(ws.iter_rows(values_only=True))
        elif "precio" in titulo:
            precios = parse_precios_retail(ws.iter_rows(values_only=True))
    if stock_rows is None:
        stock_rows = [list(row) for row in wb.worksheets[0].iter_rows(values_only=True)]
    return stock_rows, alt_map, precios


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
        try:
            rows, alt_map, precios = fetch_stock_xlsx(sheet_id)
        except Exception:
            rows, alt_map, precios = fetch_stock_sheet(sheet_id, gid), {}, {}
        productos = aplicar_alt(parse_stock_rows(rows), alt_map, precios)
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
        alt_map, precios = {}, {}
        if name.endswith(".xlsx"):
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(f.read()), read_only=True, data_only=True)
            rows = [list(r) for r in wb.worksheets[0].iter_rows(values_only=True)]
            for ws in wb.worksheets:
                titulo = ws.title.strip().lower()
                if "maestro" in titulo:
                    alt_map = parse_maestro_alt(ws.iter_rows(values_only=True))
                elif "precio" in titulo:
                    precios = parse_precios_retail(ws.iter_rows(values_only=True))
        else:
            text = f.read().decode("utf-8-sig", errors="replace")
            rows = list(csv.reader(io.StringIO(text)))
        productos = aplicar_alt(parse_stock_rows(rows), alt_map, precios)
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

        # Clasificaciones Hansa (código -> nombre y tipo; tipo MAR = marca)
        data["clasificaciones"] = {}
        ws = hoja("clasificaciones")
        if ws:
            for row in list(ws.iter_rows(values_only=True))[1:]:
                cod = _celda(row, 0)
                if cod:
                    data["clasificaciones"][cod.upper()] = {
                        "nombre": _celda(row, 1) or cod, "tipo": _celda(row, 2).upper(),
                    }

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
        for status in ("active", "paused", "under_review", "inactive"):
            try:
                ids += ml_listar_ids(token, user_id, status)
            except requests.HTTPError:
                continue
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
        # under_review e inactive también cuentan como "ya publicado": si no
        # se leen, esos items figuran como faltantes aunque existan
        for status in ("active", "paused", "under_review", "inactive"):
            try:
                ids += ml_listar_ids(token, user_id, status)
            except requests.HTTPError:
                continue  # algún estado puede no estar habilitado para la cuenta
        return jsonify({"ids": ids})
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


@sync_bp.route("/ml/fotos")
def ml_fotos():
    """Fotos de una publicación existente de ML (para reutilizarlas al
    publicar el mismo producto en Tiendanube)."""
    token   = request.args.get("token", os.environ.get("ML_TOKEN", ""))
    item_id = request.args.get("item_id", "")
    if not token or not item_id:
        return jsonify({"fotos": [], "error": "Faltan parámetros"})
    try:
        item = ml_get(f"/items/{item_id}", token)
        return jsonify({"fotos": [p.get("secure_url") or p.get("url")
                                  for p in item.get("pictures") or []
                                  if p.get("secure_url") or p.get("url")]})
    except Exception as e:
        return jsonify({"fotos": [], "error": str(e)})


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
        user_id = request.args.get("user_id", os.environ.get("ML_USER_ID", "246901020"))
        return jsonify({
            "id": item.get("id"),
            "title": item.get("title"),
            "status": item.get("status"),
            "seller_id": item.get("seller_id"),
            "es_de_esta_cuenta": str(item.get("seller_id")) == str(user_id),
            "cuenta_configurada": user_id,
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


# Atributos que la app ya completa sola (marca, impuestos, paquete, SKU, MPN):
# no hace falta pedírselos al usuario aunque la categoría los marque required.
_ATTR_AUTOCOMPLETADOS = {
    "BRAND", "SELLER_SKU", "MPN", "MODEL", "PART_NUMBER", "GTIN", "EMPTY_GTIN_REASON",
    "VALUE_ADDED_TAX", "IMPORT_DUTY", "SELLER_PACKAGE_WEIGHT", "SELLER_PACKAGE_LENGTH",
    "SELLER_PACKAGE_WIDTH", "SELLER_PACKAGE_HEIGHT", "COLOR", "SIZE",
}


@sync_bp.route("/ml/atributos")
def ml_atributos():
    """Atributos obligatorios de una categoría de ML que la app no completa
    sola, para que el usuario los cargue en el wizard."""
    token = request.args.get("token", os.environ.get("ML_TOKEN", ""))
    cat   = request.args.get("category_id", "")
    tiene_alt = request.args.get("has_alt", "1") != "0"
    # Si el producto no tiene código alternativo, PART_NUMBER/MPN/MODEL no se
    # autocompletan: hay que pedírselos al usuario
    auto = set(_ATTR_AUTOCOMPLETADOS)
    if not tiene_alt:
        auto -= {"PART_NUMBER", "MPN", "MODEL"}
    if not cat:
        return jsonify({"atributos": []})
    try:
        data = ml_get(f"/categories/{cat}/attributes", token)
        req = []
        for a in data:
            tags = a.get("tags") or {}
            obligatorio = tags.get("required") or tags.get("catalog_required")
            if not obligatorio or a.get("id") in auto:
                continue
            valores = [v.get("name") for v in (a.get("values") or []) if v.get("name")]
            req.append({
                "id": a.get("id"),
                "nombre": a.get("name"),
                "valores": valores[:60],           # lista para el desplegable
                "permite_otro": tags.get("allow_variations") is None,
                "sugerido": _sugerir_valor(a.get("id"), valores),
            })
        return jsonify({"atributos": req})
    except Exception as e:
        return jsonify({"atributos": [], "error": str(e)})


def _sugerir_valor(attr_id, valores):
    """Valor por defecto razonable para Muvin (tienda de bicis)."""
    if attr_id == "VEHICLE_TYPE":
        for v in valores:
            if v.lower() == "bicicleta":
                return v
    return ""


@sync_bp.route("/fotos")
def buscar_fotos():
    """Candidatas de fotos: catálogo oficial de ML + publicaciones existentes
    del mismo producto. El humano elige cuáles usar en el wizard."""
    token = request.args.get("token", os.environ.get("ML_TOKEN", ""))
    q     = request.args.get("q", "")
    alt   = request.args.get("alt", "").strip()  # código del fabricante
    if not q:
        return jsonify({"error": "Falta q"}), 400
    fotos, vistos = [], set()
    try:
        results = []
        ids_vistos = set()
        # El código del fabricante primero: suele dar el producto exacto
        for consulta in ([alt] if alt else []) + [q]:
            data = ml_get("/sites/MLA/search", token, {"q": consulta, "limit": 6})
            for r in data.get("results", []):
                if r.get("id") not in ids_vistos:
                    ids_vistos.add(r.get("id"))
                    results.append(r)
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

    # ML exige family_name (agrupador de productos del vendedor) en varias
    # categorías: si no vino, usamos el título
    if not item.get("family_name") and item.get("title"):
        item["family_name"] = str(item["title"])[:60]

    # Re-alojar fotos en el hosting de ML para evitar rechazos por hotlink
    mapa_fotos = {}
    for pic in item.get("pictures") or []:
        u = pic.get("source")
        if u:
            pid = ml_subir_foto(token, u)
            if pid:
                mapa_fotos[u] = pid
                pic.clear()
                pic["id"] = pid
    for v in item.get("variations") or []:
        if v.get("picture_ids"):
            v["picture_ids"] = [mapa_fotos.get(u, u) for u in v["picture_ids"]]

    try:
        r = ml_post("/items", token, item)
        # En el flujo de "familias" de ML, algunas categorías generan el
        # título automáticamente y rechazan el campo title: reintentar sin él.
        # Si el reintento también falla, se informa SU error (es el real).
        if r.status_code == 400 and "title" in r.text and item.get("family_name"):
            reintento = dict(item)
            reintento.pop("title", None)
            r = ml_post("/items", token, reintento)
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


@sync_bp.route("/tn/fotos")
def tn_fotos():
    """Fotos de un producto ya publicado en Tiendanube (para reutilizarlas
    al publicar el mismo producto en ML)."""
    store_id   = request.args.get("store_id", os.environ.get("TN_STORE_ID", ""))
    token      = request.args.get("token", os.environ.get("TN_TOKEN", ""))
    product_id = request.args.get("product_id", "")
    if not store_id or not token or not product_id:
        return jsonify({"fotos": [], "error": "Faltan parámetros"})
    try:
        r = requests.get(f"{TN_BASE}/{store_id}/products/{product_id}",
                         headers=tn_headers(token), params={"fields": "id,images"},
                         timeout=20)
        r.raise_for_status()
        p = r.json()
        return jsonify({"fotos": [im.get("src") for im in p.get("images") or [] if im.get("src")]})
    except Exception as e:
        return jsonify({"fotos": [], "error": str(e)})


@sync_bp.route("/publish/tn", methods=["POST"])
def publish_tn():
    body     = request.json or {}
    store_id = body.get("store_id", os.environ.get("TN_STORE_ID", ""))
    token    = body.get("token", os.environ.get("TN_TOKEN", ""))
    product  = body.get("product")
    if not store_id or not token or not product:
        return jsonify({"error": "Faltan store_id, token o product"}), 400

    # Re-alojar fotos: se descargan acá y van a TN como adjunto base64, para
    # evitar "Remote image not found" en sitios que bloquean hotlink
    nuevas = []
    for im in product.get("images") or []:
        u = im.get("src")
        if u:
            try:
                contenido, ct, ext = descargar_imagen(u)
                nuevas.append({"attachment": base64.b64encode(contenido).decode(),
                               "filename": f"foto-{len(nuevas)+1}.{ext}"})
                continue
            except Exception:
                pass  # se deja la URL original y que TN intente
        nuevas.append(im)
    if nuevas:
        product["images"] = nuevas

    try:
        r = requests.post(f"{TN_BASE}/{store_id}/products",
                          headers=tn_headers(token), json=product, timeout=60)
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
    alt    = (body.get("alt") or "").strip()  # código del fabricante (Hansa)
    titulo = titulo_desde_nombre(nombre)
    q = urllib.parse.quote_plus(((titulo or nombre) + (" " + alt if alt else "")).strip())
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


def con_codigo(texto, codigo):
    """Asegura la línea final 'Código: <alternativo>'. Si el item no tiene
    código alternativo no se agrega nada."""
    if codigo and not re.search(r"C[óo]digo:\s*\S", texto, re.I):
        texto = texto.rstrip() + f"\n\nCódigo: {codigo}"
    return texto


@sync_bp.route("/describe", methods=["POST"])
def describe():
    body    = request.json or {}
    nombre  = body.get("nombre", "")
    titulo  = body.get("titulo") or titulo_desde_nombre(nombre)
    datos   = body.get("datos", "")  # specs/notas extra que cargue el usuario
    alt     = (body.get("alt") or "").strip()  # código del fabricante
    sku     = (body.get("codigo") or "").strip()  # vacío => sin línea Código
    api_key = body.get("api_key") or os.environ.get("ANTHROPIC_API_KEY", "")

    if not api_key:
        texto = con_codigo(descripcion_plantilla(nombre, titulo), sku)
        return jsonify({"texto": texto, "html": texto_a_html(texto),
                        "generado_con_ia": False,
                        "warning": "Sin API key de Anthropic: se usó una plantilla básica. "
                                   "Cargá una key en Configuración para generar con IA."})
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        contenido = f"Nombre interno del producto (ERP): {nombre}\nTítulo de la publicación: {titulo}"
        if alt:
            contenido += f"\nCódigo del fabricante (MPN): {alt} — te ayuda a identificar el modelo exacto, pero no inventes specs que no conozcas con certeza."
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
        texto = con_codigo(texto, sku)
        return jsonify({"texto": texto, "html": texto_a_html(texto), "generado_con_ia": True})
    except Exception as e:
        texto = con_codigo(descripcion_plantilla(nombre, titulo), sku)
        return jsonify({"texto": texto, "html": texto_a_html(texto),
                        "generado_con_ia": False,
                        "warning": f"Falló la generación con IA ({e}); se usó la plantilla."})
