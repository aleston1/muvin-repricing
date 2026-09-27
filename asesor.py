"""
Asesor de compra Muvin.

Página pública (/asesor) que le hace al visitante las preguntas que le haría un
vendedor en el local (uso, distancia, terreno, altura, presupuesto, dónde la
guarda…) y le devuelve 2-3 bicicletas de Tiendanube con stock en su talle,
cada una con combos armados (casco + candado + luces, y uno completo), más un
chat con IA anclado en esos productos para resolver las dudas que queden.

El catálogo se lee de la API de Tiendanube con TN_STORE_ID / TN_TOKEN (las
mismas variables que usa la sincronización) y se cachea en memoria unos
minutos: el visitante nunca ve tokens ni llama a Tiendanube directo.
"""
from concurrent.futures import ThreadPoolExecutor
from flask import Blueprint, jsonify, request
import os
import re
import threading
import time
import requests

from sync import TN_BASE, tn_headers, tn_nombre, html_a_texto

asesor_bp = Blueprint("asesor", __name__, url_prefix="/api/asesor")

CACHE_TTL = int(os.environ.get("ASESOR_CACHE_SEG", "900"))
STORE_URL = os.environ.get("ASESOR_STORE_URL", "https://www.muvin.com.ar")

# ------------------------------------------------------ categorías de la tienda
# IDs de categorías de Tiendanube (Vehículos > Bicicletas y Accesorios). Si se
# reorganiza el árbol en TN, alcanza con actualizar estos números.
CAT_BICIS = {
    "urbana":   40259279,
    "plegable": 30153433,
    "electrica": 30153654,
    "carga":    30153580,
    "gravel":   40259233,
    "mtb":      40259337,
    "ruta":     40259424,
}
CAT_ACCESORIOS = {
    "casco":        30169776,  # Cascos > Para ciclismo
    "candado":      30169763,  # Candados y accesorios
    "luces":        30169757,  # Luces > Para bicicleta
    "inflador":     38434548,  # Infladores > De mano
    "portapaquetes": 30169786,
    "canasto":      33821153,  # Bolsos y canastos
    "caramanola":   30169771,
    "guardabarros": 33843639,
    "timbre":       30169819,
    "porta_celular": 37360471,
}
CAT_NINOS = 30153643
CAT_MOTO_CANDADOS = 35512464  # "Específicos de moto": nunca van en un combo de bici

TIPO_TEXTO = {
    "urbana": "urbana", "plegable": "plegable", "electrica": "eléctrica",
    "carga": "de carga", "gravel": "gravel", "mtb": "de montaña (MTB)",
    "ruta": "de ruta",
}

# ------------------------------------------------------------------ catálogo

_cache = {"t": 0, "data": None}
_lock = threading.Lock()


def _tn_credenciales():
    return os.environ.get("TN_STORE_ID", ""), os.environ.get("TN_TOKEN", "")


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _fetch_categoria(store_id, token, cat_id):
    productos, page = [], 1
    while True:
        r = requests.get(
            f"{TN_BASE}/{store_id}/products", headers=tn_headers(token),
            params={"category_id": cat_id, "published": "true", "page": page,
                    "per_page": 200,
                    "fields": "id,name,canonical_url,handle,variants,images,"
                              "categories,attributes,brand,description"},
            timeout=30)
        if r.status_code == 404:  # TN devuelve 404 pasada la última página
            break
        r.raise_for_status()
        lote = r.json()
        productos += lote
        if len(lote) < 200:
            break
        page += 1
    return productos


def normalizar(p):
    """Producto de TN -> dict compacto, solo con las variantes con stock."""
    attrs = [tn_nombre(a).strip().lower() for a in p.get("attributes") or []]
    i_talle = next((i for i, a in enumerate(attrs) if a in ("talle", "tamaño", "size", "medida")), None)
    i_color = next((i for i, a in enumerate(attrs) if a in ("color", "modelo")), None)
    variantes = []
    for v in p.get("variants") or []:
        stock_ok = (not v.get("stock_management")) or v.get("stock") is None or _num(v.get("stock")) > 0
        if not stock_ok:
            continue
        precio = _num(v.get("price"))
        promo = _num(v.get("promotional_price"))
        if precio <= 0:
            continue
        valores = [tn_nombre(x).strip() for x in v.get("values") or []]
        variantes.append({
            "id": v.get("id"),
            "precio": promo if 0 < promo < precio else precio,
            "precio_lista": precio,
            "talle": valores[i_talle] if i_talle is not None and i_talle < len(valores) else None,
            "color": valores[i_color] if i_color is not None and i_color < len(valores) else None,
        })
    imgs = p.get("images") or []
    return {
        "id": p.get("id"),
        "nombre": tn_nombre(p.get("name")),
        "marca": p.get("brand") or "",
        "url": p.get("canonical_url") or f"{STORE_URL}/productos/{tn_nombre(p.get('handle'))}",
        "imagen": imgs[0].get("src") if imgs else None,
        "categorias": {c.get("id") for c in p.get("categories") or []},
        "descripcion": html_a_texto(tn_nombre(p.get("description")))[:1200],
        "tiene_talles": i_talle is not None,
        "variantes": variantes,
    }


def cargar_catalogo(forzar=False):
    with _lock:
        if not forzar and _cache["data"] and time.time() - _cache["t"] < CACHE_TTL:
            return _cache["data"]
    store_id, token = _tn_credenciales()
    if not store_id or not token:
        raise RuntimeError("Faltan TN_STORE_ID / TN_TOKEN en el servidor")

    todas = {**{f"bici:{k}": v for k, v in CAT_BICIS.items()},
             **{f"acc:{k}": v for k, v in CAT_ACCESORIOS.items()}}
    with ThreadPoolExecutor(max_workers=4) as ex:  # TN permite ~2 req/s sostenidas
        crudos = dict(zip(todas, ex.map(lambda c: _fetch_categoria(store_id, token, c),
                                        todas.values())))

    bicis, accesorios = {}, {k: [] for k in CAT_ACCESORIOS}
    for clave, productos in crudos.items():
        grupo, nombre = clave.split(":")
        for p in productos:
            n = normalizar(p)
            if not n["variantes"]:
                continue
            if grupo == "bici":
                if CAT_NINOS in n["categorias"]:
                    continue
                b = bicis.setdefault(n["id"], {**n, "tipos": set()})
                b["tipos"].add(nombre)
            else:
                accesorios[nombre].append(n)
    data = {"bicis": list(bicis.values()), "accesorios": accesorios}
    with _lock:
        _cache.update(t=time.time(), data=data)
    return data


# ------------------------------------------------------------------- talles

# Rango de altura (cm) de cada talle de cuadro. Se solapan a propósito: en el
# borde entre dos talles, ambos sirven y se ofrecen los dos.
TALLES_ALTURA = {
    "XXS": (135, 152), "XS": (148, 163), "S": (158, 172), "M": (168, 181),
    "L": (177, 190), "XL": (186, 198), "XXL": (194, 210),
}
ORDEN_TALLES = list(TALLES_ALTURA)


def talle_letra(valor):
    """Normaliza el talle de una variante a letras. Acepta 'M', 'S/M',
    '17"' (MTB, pulgadas) o '54' (ruta, cm). None si no se reconoce."""
    if not valor:
        return []
    s = str(valor).upper().replace("\"", "").replace("”", "").replace("''", "").strip()
    partes = [x.strip() for x in re.split(r"[/\-–]| A ", s) if x.strip()]
    out = []
    for x in partes:
        x = x.replace("CM", "").replace("PULGADAS", "").strip()
        if x in TALLES_ALTURA:
            out.append(x)
            continue
        try:
            n = float(x.replace(",", "."))
        except ValueError:
            continue
        if 12 <= n <= 24:        # pulgadas (MTB / urbanas)
            out.append("XS" if n < 15 else "S" if n < 17 else "M" if n < 19 else "L" if n < 21 else "XL")
        elif 42 <= n <= 64:      # cm (ruta / gravel)
            out.append("XS" if n < 50 else "S" if n < 53 else "M" if n < 56 else "L" if n < 59 else "XL")
    return out


def talles_para(altura):
    """Talles que le calzan a esa altura, del más centrado al menos."""
    ok = [t for t, (lo, hi) in TALLES_ALTURA.items() if lo <= altura <= hi]
    return sorted(ok, key=lambda t: abs(sum(TALLES_ALTURA[t]) / 2 - altura))


# ------------------------------------------------------------ recomendación

PESOS_USO = {
    "ciudad":    {"urbana": 4, "plegable": 2, "electrica": 2, "gravel": 1},
    "combinar":  {"plegable": 7},
    "paseo":     {"urbana": 4, "plegable": 1, "gravel": 2, "electrica": 1},
    "deporte":   {"ruta": 5, "gravel": 3},
    "tierra":    {"mtb": 5, "gravel": 3},
    "carga":     {"carga": 7, "electrica": 2, "urbana": 1},
}

TEXTO_USO = {
    "ciudad": "moverte por la ciudad todos los días",
    "combinar": "combinarla con tren, subte, colectivo o auto",
    "paseo": "pasear y salir los fines de semana",
    "deporte": "entrenar y hacer kilómetros en asfalto",
    "tierra": "salir a la tierra, caminos y montaña",
    "carga": "llevar carga o a los chicos",
}


def _precio_min(bici):
    return min(v["precio"] for v in bici["variantes"])


def puntuar(bici, r):
    """Devuelve (puntaje, motivos, talles_ok) o None si la bici no aplica."""
    tipos = bici["tipos"]
    uso = r.get("uso", "ciudad")
    dist = r.get("distancia", "media")
    terreno = r.get("terreno", "plano")
    electrica = r.get("electrica", "abierto")
    espacio = r.get("espacio", "tengo")
    altura = r.get("altura")

    es_elec = "electrica" in tipos
    if electrica == "si" and not es_elec:
        return None
    if electrica == "no" and es_elec:
        return None

    motivos, score = [], 0.0
    pesos = PESOS_USO.get(uso, {})
    base = max((pesos.get(t, 0) for t in tipos), default=0)
    if base == 0:
        return None  # el tipo de bici no tiene nada que ver con el uso
    score += base
    tipo_principal = max(tipos, key=lambda t: pesos.get(t, 0))
    motivos.append(f"Es una bici {TIPO_TEXTO[tipo_principal]}, ideal para {TEXTO_USO.get(uso, 'lo que buscás')}.")

    if es_elec:
        if dist == "larga":
            score += 3
            motivos.append("La asistencia eléctrica hace que los trayectos largos lleguen sin cansancio ni transpiración.")
        if terreno == "subidas":
            score += 3
            motivos.append("El motor te ayuda en las subidas: las pedaleás como si fueran llano.")
        if dist == "corta" and terreno == "plano" and electrica != "si":
            score -= 2
    if "plegable" in tipos:
        if espacio == "poco":
            score += 4
            motivos.append("Se pliega en segundos: entra en el ascensor, bajo el escritorio o en el baúl.")
        if uso == "combinar":
            motivos.append("Plegada la podés subir al tren o al subte y seguir el viaje.")
        if terreno == "tierra":
            score -= 3
    if espacio == "poco" and "carga" in tipos:
        score -= 5
    if terreno == "tierra":
        if tipos & {"mtb", "gravel"}:
            score += 2
            motivos.append("Cubiertas y cuadro preparados para caminos de tierra.")
        if "ruta" in tipos:
            score -= 4
    if dist == "larga" and tipos & {"ruta", "gravel"}:
        score += 1.5
        motivos.append("Geometría eficiente para sumar kilómetros sin esfuerzo de más.")

    # Talle: una bici con talles solo sirve si hay stock del talle correcto.
    talles_ok = []
    if bici["tiene_talles"] and altura:
        buenos = talles_para(altura)
        for v in bici["variantes"]:
            letras = talle_letra(v["talle"])
            if any(t in buenos for t in letras):
                talles_ok.append(v)
        if not talles_ok and any(talle_letra(v["talle"]) for v in bici["variantes"]):
            return None  # hay talles reconocibles pero ninguno le queda
        if talles_ok:
            t = talles_ok[0]["talle"]
            motivos.append(f"Para tu altura ({altura} cm) te corresponde talle {t}, y lo tenemos en stock.")
    elif altura and not bici["tiene_talles"]:
        if "plegable" in tipos and 150 <= altura <= 195:
            motivos.append("Talle único con manubrio y asiento regulables: se adapta a tu altura.")

    return score, motivos, talles_ok or bici["variantes"]


def candidatos(items, filtro=None, excluir=None):
    """Accesorios aptos, del más barato al más caro."""
    return sorted((a for a in items or []
                   if (not filtro or filtro(a)) and not (excluir and excluir(a))), key=_precio_min)


def indice_nivel(n, nivel):
    """Posición dentro de una lista ordenada por precio según el nivel buscado:
    'economico' = el más barato, 'medio' = el del medio, 'alto' = cuartil superior."""
    if n <= 1 or nivel == "economico":
        return 0
    return int(n * 0.75) if nivel == "alto" else n // 2


RE_CASCO_NINO = re.compile(r"niñ|nino|kid|infantil|junior|crazy safety|baby|little", re.I)
RE_CANDADO_MOTO = re.compile(r"disco|disc|traba ?rueda|perno|moto", re.I)
RE_CABLE = re.compile(r"cable|kryptoflex|flex\b|espiral", re.I)
RE_SET_LUCES = re.compile(r"set|kit|par\b|\+|luces", re.I)
RE_DELANTERA = re.compile(r"delantera|front|f-\d|allty|van square|commuter", re.I)
RE_TRASERA = re.compile(r"trasera|rear|r ?\d|seemee", re.I)

POR_QUE_ACC = {
    "casco": "Lo primero: protege tu cabeza en cada salida.",
    "candado": "Para dejarla estacionada tranquilo.",
    "luces": "Para ver y, sobre todo, que te vean los autos al atardecer y de noche.",
    "inflador": "Llevar las cubiertas bien infladas evita pinchaduras y hace que ruede mejor.",
    "portapaquetes": "Para llevar la mochila o las compras sin cargar la espalda.",
    "canasto": "Para llevar tus cosas a mano.",
    "caramanola": "Hidratación a mano en las salidas largas.",
    "guardabarros": "Para no mojarte ni embarrarte los días de lluvia.",
}


def _item(rol, prod, detalle=None):
    return {"rol": rol, "id": prod["id"], "nombre": prod["nombre"], "marca": prod["marca"], "url": prod["url"],
            "imagen": prod["imagen"], "precio": _precio_min(prod),
            "precio_lista": min(v["precio_lista"] for v in prod["variantes"]),
            "por_que": detalle or POR_QUE_ACC.get(rol, "")}


def armar_combos(bici, variante, r, acc, restante):
    """Combos para una bici: 'Esencial' (lo imprescindible para salir hoy) y
    'Completo' (lo que suma comodidad según el uso).

    Cada accesorio arranca en el nivel ideal para el uso (p. ej. candado de
    alta seguridad si queda en la calle) y, si el esencial no entra en lo que
    queda del presupuesto, se baja de a un escalón el ítem más caro hasta que
    entre o todo quede en su opción más económica."""
    ya_tiene = set(r.get("ya_tengo") or [])
    uso = r.get("uso", "ciudad")
    noche = r.get("horario") == "noche"
    calle = r.get("estaciona", "calle") == "calle"

    # (rol, candidatos, índice inicial, por qué)
    slots = []
    if "casco" not in ya_tiene:
        c = candidatos(acc["casco"], excluir=lambda a: RE_CASCO_NINO.search(a["nombre"]))
        slots.append(["casco", c, indice_nivel(len(c), "medio"),
                      "Lo primero: protege tu cabeza en cada salida. Elegí el talle "
                      "midiendo el contorno de tu cabeza a la altura de la frente."])
    if "candado" not in ya_tiene:
        moto = lambda a: RE_CANDADO_MOTO.search(a["nombre"]) or CAT_MOTO_CANDADOS in a["categorias"]
        if calle:
            c = candidatos(acc["candado"], excluir=lambda a: moto(a) or RE_CABLE.search(a["nombre"]))
            slots.append(["candado", c, indice_nivel(len(c), "alto"),
                          "Vas a dejarla en la calle: un U-lock, cadena o plegable de alta "
                          "seguridad es lo que realmente frena un robo (los cables se cortan en segundos)."])
        else:
            c = candidatos(acc["candado"], excluir=moto)
            slots.append(["candado", c, 0,
                          "La guardás bajo techo: con un candado liviano para paradas cortas alcanza."])
    if "luces" not in ya_tiene:
        por_que = ("Circulás de noche: luz delantera para ver y trasera para que te vean."
                   if noche else POR_QUE_ACC["luces"])
        sets = candidatos(acc["luces"], filtro=lambda a: RE_SET_LUCES.search(a["nombre"]))
        if sets:
            slots.append(["luces", sets, indice_nivel(len(sets), "alto" if noche else "medio"),
                          por_que + " Es un set delantera + trasera."])
        else:
            for lado, rx in (("delantera", RE_DELANTERA), ("trasera", RE_TRASERA)):
                c = candidatos(acc["luces"], filtro=lambda a, rx=rx: rx.search(a["nombre"]))
                slots.append(["luces", c, indice_nivel(len(c), "alto" if noche else "medio"),
                              f"Luz {lado}. {por_que}"])
    slots = [s for s in slots if s[1]]

    precio = lambda s: _precio_min(s[1][s[2]])
    if restante is not None:
        while sum(precio(s) for s in slots) > restante:
            bajables = [s for s in slots if s[2] > 0]
            if not bajables:
                break
            max(bajables, key=precio)[2] -= 1
    esencial = [_item(rol, c[i], pq) for rol, c, i, pq in slots]

    completo = list(esencial)
    extras = ["inflador"]
    if uso in ("ciudad", "paseo", "carga", "combinar"):
        extras.append("canasto" if "plegable" in bici["tipos"] else "portapaquetes")
    if uso in ("deporte", "tierra") or r.get("distancia") == "larga":
        extras.append("caramanola")
    for rol in extras:
        c = candidatos(acc.get(rol))
        if c:
            completo.append(_item(rol, c[indice_nivel(len(c), "medio")]))

    precio_bici = variante["precio"]
    combos = []
    for nombre, items, desc in (
        ("Esencial", esencial, "Todo lo imprescindible para salir hoy mismo, seguro."),
        ("Completo", completo, "El esencial más lo que suma comodidad para tu uso."),
    ):
        if nombre == "Completo" and len(completo) == len(esencial):
            continue
        if not items:
            continue
        acc_total = sum(i["precio"] for i in items)
        combos.append({"nombre": nombre, "descripcion": desc, "items": items,
                       "total": round(precio_bici + acc_total, 2),
                       "total_accesorios": round(acc_total, 2)})
    return combos


def recomendar(r, catalogo):
    presupuesto = r.get("presupuesto")  # total, bici + accesorios; None = sin tope
    try:
        presupuesto = float(presupuesto) if presupuesto else None
    except (TypeError, ValueError):
        presupuesto = None
    try:
        r["altura"] = int(r.get("altura")) if r.get("altura") else None
    except (TypeError, ValueError):
        r["altura"] = None
    acc = catalogo["accesorios"]

    avisos = []
    if r["altura"] and r["altura"] < 145:
        avisos.append({"tipo": "ninos",
                       "texto": "Para menos de 1,45 m conviene una bici infantil o juvenil (rodado 16 a 24).",
                       "url": f"{STORE_URL}/search/?q=infantil"})

    # Lo mínimo que va a costar lo esencial: se reserva del presupuesto total.
    ya = set(r.get("ya_tengo") or [])
    reserva = sum(min(_precio_min(a) for a in acc[rol])
                  for rol in ("casco", "candado", "luces") if rol not in ya and acc.get(rol))

    candidatas = []
    for b in catalogo["bicis"]:
        res = puntuar(b, r)
        if not res:
            continue
        score, motivos, variantes = res
        var = min(variantes, key=lambda v: v["precio"])
        candidatas.append([score, b, var, variantes, motivos])

    fuera_de_presupuesto = False
    if presupuesto:
        tope_bici = max(presupuesto - reserva, 1)
        dentro = [c for c in candidatas if c[2]["precio"] <= presupuesto * 1.1]
        for c in dentro:
            p = c[2]["precio"]
            # Pasarse (hasta 10%) resta; aprovechar el presupuesto suele
            # significar mejor bici, así que suma un poco.
            c[0] += -3 if p > tope_bici else 1.5 * p / tope_bici
        if dentro:
            candidatas = dentro
        else:
            # Nada entra: mostramos las más cercanas en precio antes que nada.
            fuera_de_presupuesto = True
            candidatas.sort(key=lambda c: c[2]["precio"])
            candidatas = candidatas[:3]
            for k, c in enumerate(candidatas):
                c[0] = -k

    # Variedad: cada bici del mismo tipo que una ya elegida pierde un poco,
    # así si hay dos opciones parejas de tipos distintos se ven las dos.
    elegidas = []
    while candidatas and len(elegidas) < 3:
        vistos = [frozenset(e[1]["tipos"]) for e in elegidas]
        mejor = max(candidatas, key=lambda c: (c[0] - 1.5 * vistos.count(frozenset(c[1]["tipos"])),
                                               -c[2]["precio"]))
        candidatas.remove(mejor)
        elegidas.append(mejor)

    resultados = []
    for i, (score, b, var, variantes, motivos) in enumerate(elegidas):
        restante = max(presupuesto - var["precio"], 0) if presupuesto else None
        combos = armar_combos(b, var, r, acc, restante)
        total_esencial = next((c["total"] for c in combos if c["nombre"] == "Esencial"), var["precio"])
        excede = max(total_esencial - presupuesto, 0) if presupuesto else 0
        talles = sorted({v["talle"] for v in variantes if v["talle"]},
                        key=lambda t: (ORDEN_TALLES.index(talle_letra(t)[0]) if talle_letra(t) else 99, t))
        colores = sorted({v["color"] for v in variantes if v["color"]})
        resultados.append({
            "id": b["id"], "nombre": b["nombre"], "marca": b["marca"], "url": b["url"],
            "imagen": b["imagen"], "tipos": sorted(b["tipos"]),
            "tipo_texto": " / ".join(TIPO_TEXTO[t] for t in sorted(b["tipos"])),
            "precio": var["precio"], "precio_lista": var["precio_lista"],
            "talles": talles, "colores": colores,
            "talle_unico_regulable": not b["tiene_talles"] and "plegable" in b["tipos"],
            "motivos": motivos, "destacada": i == 0 and not fuera_de_presupuesto,
            "excede": round(excede, 2),
            "combos": combos,
        })
    if fuera_de_presupuesto and resultados:
        avisos.append({"tipo": "presupuesto",
                       "texto": "Con ese presupuesto no llegamos a una bici nueva que cumpla todo lo que buscás. "
                                "Estas son las opciones más cercanas; también podés mirar los usados seleccionados.",
                       "url": f"{STORE_URL}/search/?q=usada"})
    return {"resultados": resultados, "avisos": avisos,
            "resumen": resumen_perfil(r, presupuesto)}


def resumen_perfil(r, presupuesto):
    partes = [f"Buscás una bici para {TEXTO_USO.get(r.get('uso'), 'moverte')}"]
    d = {"corta": "trayectos cortos (menos de 5 km)", "media": "trayectos de 5 a 15 km",
         "larga": "trayectos de más de 15 km"}.get(r.get("distancia"))
    if d:
        partes.append(d)
    t = {"plano": "mayormente en llano", "subidas": "con subidas",
         "tierra": "con tramos de tierra"}.get(r.get("terreno"))
    if t:
        partes.append(t)
    s = ", ".join(partes)
    if r.get("altura"):
        s += f". Medís {r['altura']} cm"
    if presupuesto:
        s += f" y tenés un presupuesto de ${presupuesto:,.0f}".replace(",", ".")
    return s + "."


# ------------------------------------------------------------------- rutas

@asesor_bp.route("/config")
def config():
    return jsonify({"whatsapp": os.environ.get("ASESOR_WHATSAPP", ""),
                    "store_url": STORE_URL,
                    "chat_ia": bool(os.environ.get("ANTHROPIC_API_KEY"))})


@asesor_bp.route("/recomendar", methods=["POST"])
def ruta_recomendar():
    r = request.json or {}
    try:
        catalogo = cargar_catalogo()
    except requests.HTTPError as e:
        return jsonify({"error": f"No pudimos leer el catálogo ({e.response.status_code})."}), 502
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify(recomendar(r, catalogo))


@asesor_bp.route("/refrescar", methods=["POST"])
def ruta_refrescar():
    """Fuerza a releer el catálogo (p. ej. tras cambiar precios)."""
    try:
        data = cargar_catalogo(forzar=True)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"bicis": len(data["bicis"]),
                    "accesorios": {k: len(v) for k, v in data["accesorios"].items()}})


PROMPT_ASESOR = """Sos el asesor de ventas de Muvin (muvin.com.ar), una tienda argentina \
de bicicletas urbanas, plegables y eléctricas y accesorios. Hablás en español \
rioplatense, cálido, directo y breve (2 a 5 oraciones salvo que pidan detalle), sin emojis.

El cliente ya completó un cuestionario y le recomendamos productos. Tu trabajo es \
resolver sus dudas para que pueda comprar con confianza sin tener que llamar ni ir al local.

Perfil del cliente:
{perfil}

Productos recomendados (con precio y datos de la tienda):
{productos}

Reglas:
- Basate SOLO en el perfil, los productos y los datos de arriba, más conocimiento \
general de ciclismo (talles, mantenimiento, seguridad, diferencias entre tipos de bici).
- NO inventes especificaciones, precios, stock, plazos de envío, garantías, \
medios de pago, cuotas ni promociones. Si te preguntan algo de eso y no está arriba, \
decí honestamente que no tenés el dato y sugerí consultarlo en la página del producto \
o por WhatsApp.
- Si otra de las opciones recomendadas le conviene más por lo que cuenta, decíselo.
- Terminá con una recomendación concreta cuando corresponda."""


@asesor_bp.route("/preguntar", methods=["POST"])
def ruta_preguntar():
    body = request.json or {}
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return jsonify({"error": "El chat no está disponible."}), 503
    historial = body.get("mensajes") or []
    if not historial or historial[-1].get("role") != "user":
        return jsonify({"error": "Falta la pregunta"}), 400
    historial = [{"role": m["role"], "content": str(m.get("content", ""))[:1500]}
                 for m in historial[-12:] if m.get("role") in ("user", "assistant")]
    while historial and historial[0]["role"] != "user":
        historial.pop(0)

    # Datos de los productos del lado del servidor (no se confía en lo que
    # mande el navegador): descripción real de la tienda.
    ids = {str(x) for x in body.get("ids") or []}
    textos = []
    try:
        cat = cargar_catalogo()
        todos = cat["bicis"] + [a for lst in cat["accesorios"].values() for a in lst]
        for p in todos:
            if str(p["id"]) in ids:
                talles = sorted({v["talle"] for v in p["variantes"] if v["talle"]})
                textos.append(
                    f"- {p['nombre']} ({p['marca']}) — desde ${_precio_min(p):,.0f}".replace(",", ".")
                    + (f" — talles en stock: {', '.join(talles)}" if talles else "")
                    + f"\n  {p['descripcion'][:700]}")
    except Exception:
        pass
    perfil = str(body.get("perfil") or "")[:800]

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model=os.environ.get("ASESOR_MODEL", "claude-sonnet-5"),
            max_tokens=700,
            system=PROMPT_ASESOR.format(perfil=perfil or "(sin datos)",
                                        productos="\n".join(textos) or "(sin datos)"),
            messages=historial)
        texto = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
        return jsonify({"respuesta": texto})
    except Exception as e:
        return jsonify({"error": f"No pudimos responder ahora ({e.__class__.__name__})."}), 502
