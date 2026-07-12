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
            offset = 0
            while True:
                data = ml_get(f"/users/{user_id}/items/search", token,
                              {"status": status, "limit": 100, "offset": offset})
                res = data.get("results", [])
                ids += res
                total = data.get("paging", {}).get("total", 0)
                offset += 100
                if not res or offset >= min(total, 1000):
                    break
        items_por_raiz = {}
        sin_sku = []
        attrs = "id,title,status,permalink,price,available_quantity,thumbnail,seller_custom_field,attributes,variations"
        for i in range(0, len(ids), 20):
            chunk = ids[i:i + 20]
            details = ml_get("/items", token, {"ids": ",".join(chunk), "attributes": attrs})
            for x in details:
                if x.get("code") != 200:
                    continue
                item = x["body"]
                resumen = {"id": item.get("id"), "title": item.get("title"),
                           "status": item.get("status"), "permalink": item.get("permalink"),
                           "price": item.get("price")}
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
    r = requests.post(ML_BASE + "/oauth/token", data=payload,
                      headers={"Accept": "application/json"}, timeout=20)
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


@sync_bp.route("/publish/ml", methods=["POST"])
def publish_ml():
    body  = request.json or {}
    token = body.get("token", os.environ.get("ML_TOKEN", ""))
    item  = body.get("item")
    descripcion = body.get("descripcion", "")
    if not token or not item:
        return jsonify({"error": "Faltan token o item"}), 400
    r = ml_post("/items", token, item)
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

@sync_bp.route("/tn")
def get_tn():
    store_id = request.args.get("store_id", os.environ.get("TN_STORE_ID", ""))
    token    = request.args.get("token", os.environ.get("TN_TOKEN", ""))
    if not store_id or not token:
        return jsonify({"error": "Faltan store_id o token de Tiendanube"}), 400
    try:
        productos_por_raiz = {}
        sin_sku = []
        page = 1
        total = 0
        while True:
            r = requests.get(f"{TN_BASE}/{store_id}/products",
                             headers=tn_headers(token),
                             params={"page": page, "per_page": 200,
                                     "fields": "id,name,canonical_url,published,variants"},
                             timeout=30)
            if r.status_code == 404:  # última página
                break
            r.raise_for_status()
            products = r.json()
            if not products:
                break
            total += len(products)
            for p in products:
                resumen = {"id": p.get("id"), "name": tn_nombre(p.get("name")),
                           "published": p.get("published"), "url": p.get("canonical_url")}
                raices = {sku_raiz(v.get("sku")) for v in p.get("variants") or []} - {None}
                if not raices:
                    sin_sku.append(resumen)
                for raiz in raices:
                    productos_por_raiz.setdefault(raiz, []).append(resumen)
            if len(products) < 200:
                break
            page += 1
        return jsonify({"productos_por_raiz": productos_por_raiz, "sin_sku": sin_sku,
                        "total_productos": total})
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
    r = requests.post(f"{TN_BASE}/{store_id}/products",
                      headers=tn_headers(token), json=product, timeout=30)
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
