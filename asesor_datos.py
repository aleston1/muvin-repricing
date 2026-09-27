"""
Datos del asesor: lee la planilla de criterios y decide qué se puede ofrecer.

Regla de Muvin: si a un producto le falta UN dato obligatorio, NO se ofrece.
Las reglas son las mismas que calcula la planilla (solapa 'Datos obligatorios'
y columna 'Qué falta'); acá se aplican del lado del servidor para que la web
nunca muestre algo incompleto aunque la planilla tenga un error de fórmula.

Fuente de la planilla, en orden:
  1. ASESOR_SHEET_ID: Google Sheet compartido como 'Cualquier persona con el
     enlace: Lector' (se baja como .xlsx, igual que la planilla de stock).
  2. docs/asesor_criterios.xlsx del repo (plantilla).
"""
import io
import os
import threading
import time

import requests

SHEET_ID = os.environ.get("ASESOR_SHEET_ID", "")
LOCAL_XLSX = os.path.join(os.path.dirname(__file__), "docs", "asesor_criterios.xlsx")
CACHE_TTL = int(os.environ.get("ASESOR_SHEET_CACHE_SEG", "300"))

_cache = {"t": 0, "data": None}
_lock = threading.Lock()


def _bajar_xlsx():
    if SHEET_ID:
        url = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=xlsx"
        r = requests.get(url, timeout=60, allow_redirects=True)
        if r.status_code != 200 or "text/html" in r.headers.get("content-type", ""):
            raise RuntimeError("No se pudo leer la planilla del asesor. Verificá que esté "
                               "compartida como 'Cualquier persona con el enlace: Lector'.")
        return io.BytesIO(r.content), f"Google Sheets ({SHEET_ID})"
    with open(LOCAL_XLSX, "rb") as f:
        return io.BytesIO(f.read()), "docs/asesor_criterios.xlsx"


def _txt(v):
    return "" if v is None else str(v).strip()


def _num(v):
    if v is None or _txt(v) == "":
        return None
    try:
        return float(str(v).replace(",", "."))
    except ValueError:
        return None


def _si(v):
    return _txt(v).lower() in ("sí", "si", "s", "yes", "true", "1")


def _filas(ws):
    rows = ws.iter_rows(values_only=True)
    enc = [_txt(c) for c in next(rows)]
    for r in rows:
        d = {enc[i]: r[i] for i in range(min(len(enc), len(r))) if enc[i]}
        if any(_txt(v) for v in d.values()):
            yield d


def _id(v):
    n = _num(v)
    return str(int(n)) if n is not None else None


def parsear(fuente):
    import openpyxl
    wb = openpyxl.load_workbook(fuente, read_only=True, data_only=True)

    alturas = {}
    for d in _filas(wb["Altura por modelo"]):
        pid = _id(d.get("ID Tiendanube"))
        if not pid:
            continue  # notas al pie
        alturas.setdefault(pid, []).append({
            "talle": _txt(d.get("Talle (como está en TN)")),
            "desde": _num(d.get("Altura desde (cm)")),
            "hasta": _num(d.get("Altura hasta (cm)")),
            "estado": _txt(d.get("Estado")),
        })

    productos = {}
    for d in _filas(wb["Productos"]):
        pid = _id(d.get("ID Tiendanube"))
        if not pid:
            continue
        p = {
            "id": pid,
            "nombre": _txt(d.get("Nombre")),
            "tipo": _txt(d.get("Tipo de vehículo")),
            "propulsion": _txt(d.get("Propulsión")),
            "usos": {k: _si(d.get(c)) for k, c in (
                ("diario", "Uso: diario"), ("paseo", "Uso: paseo"),
                ("deporte", "Uso: deporte / naturaleza"), ("chicos", "Uso: llevar chicos"),
                ("carga", "Uso: llevar carga"), ("ninos", "Uso: niños"))},
            "silla": _txt(d.get("Acepta silla de niños")),
            "aire": _txt(d.get("Neumáticos con aire")),
            "terreno": _txt(d.get("Terreno apto")),
            "autonomia": _num(d.get("Autonomía (km)")),
            "velocidad": _num(d.get("Velocidad máx (km/h)")),
            "licencia": _txt(d.get("Requiere licencia/patente")),
            "plegable": _txt(d.get("Plegable")),
            "peso": _num(d.get("Peso (kg)")),
            "carga_max": _num(d.get("Carga máx (kg)")),
            "subida": _num(d.get("Subida máx (%)")),
            "nivel": _txt(d.get("Nivel")),
            "prioridad": _num(d.get("Prioridad")) or 0,
            "activo": _txt(d.get("Activo en asesor")),
            "argumento": _txt(d.get("Argumento de venta")),
            "alturas": alturas.get(pid, []),
        }
        p["faltantes"] = faltantes(p)
        productos[pid] = p
    return productos


def faltantes(p):
    """Lista de datos obligatorios que le faltan. Vacía = se puede ofrecer.
    Mismas reglas que la columna 'Qué falta' de la planilla."""
    f = []
    motor = p["propulsion"] and p["propulsion"] != "Pedal"
    if not p["tipo"]:
        f.append("tipo")
    if not p["propulsion"]:
        f.append("propulsión")
    if not any(p["usos"].values()):
        f.append("uso")
    if p["silla"] not in ("Sí", "No"):
        f.append("silla de niños")
    if p["aire"] not in ("Sí", "No"):
        f.append("neumáticos")
    if not p["terreno"]:
        f.append("terreno")
    if motor and p["autonomia"] is None:
        f.append("autonomía")
    if motor and p["velocidad"] is None:
        f.append("velocidad")
    if p["licencia"] not in ("Sí", "No"):
        f.append("licencia")
    if p["plegable"] not in ("Sí", "No", "Parcial"):
        f.append("plegable")
    if p["peso"] is None:
        f.append("peso")
    if motor and p["carga_max"] is None:
        f.append("carga máx")
    if "bici" in p["tipo"].lower():
        if not p["alturas"]:
            f.append("altura")
        elif any(a["desde"] is None or a["hasta"] is None for a in p["alturas"]):
            f.append("altura por talle")
    if p["activo"] != "Sí":
        f.append("desactivado")
    return f


def cargar(forzar=False):
    with _lock:
        if not forzar and _cache["data"] and time.time() - _cache["t"] < CACHE_TTL:
            return _cache["data"]
    fuente, origen = _bajar_xlsx()
    data = {"productos": parsear(fuente), "origen": origen, "leido": time.time()}
    with _lock:
        _cache.update(t=time.time(), data=data)
    return data


def ofrecibles(forzar=False):
    """Solo los productos con TODOS los datos obligatorios."""
    return {pid: p for pid, p in cargar(forzar)["productos"].items() if not p["faltantes"]}


def reporte(stock_tn=None, forzar=False):
    """Resumen para la página de control.

    stock_tn: dict opcional {id: {"nombre", "url", "con_stock", "con_foto", "precio"}}
    con los vehículos de Tiendanube, para detectar los que no están en la
    planilla o que fallan del lado de la tienda."""
    data = cargar(forzar)
    prods = data["productos"]
    ok, no, sin_stock = [], [], []
    for p in prods.values():
        motivos = list(p["faltantes"])
        tn = (stock_tn or {}).get(p["id"])
        if stock_tn is not None:
            if tn is None:
                # Hoy no se vende igual; se lista aparte con lo que le falte
                # para que esté listo cuando vuelva a entrar stock.
                sin_stock.append({"id": p["id"], "nombre": p["nombre"], "tipo": p["tipo"], "motivos": motivos})
                continue
            else:
                if not tn.get("con_foto"):
                    motivos.append("sin foto en Tiendanube")
                if not tn.get("precio"):
                    motivos.append("sin precio en Tiendanube")
        (no if motivos else ok).append({"id": p["id"], "nombre": p["nombre"], "tipo": p["tipo"],
                                         "motivos": motivos})
    sin_planilla = []
    for pid, tn in (stock_tn or {}).items():
        if pid not in prods:
            sin_planilla.append({"id": pid, "nombre": tn.get("nombre"), "url": tn.get("url")})
    return {"origen": data["origen"], "leido": data["leido"],
            "ofrecidos": sorted(ok, key=lambda x: x["nombre"]),
            "no_ofrecidos": sorted(no, key=lambda x: (len(x["motivos"]), x["nombre"])),
            "sin_stock": sorted(sin_stock, key=lambda x: x["nombre"]),
            "sin_planilla": sorted(sin_planilla, key=lambda x: x["nombre"] or "")}
