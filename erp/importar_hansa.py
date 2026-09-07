"""Importador/migrador de datos desde Hansa hacia el ERP.

Hansa exporta sus registros a archivos de **texto delimitado** (habitualmente
tabulado). Este importador es **genérico**: se le indica un *mapeo* de qué
columna del archivo corresponde a cada campo del ERP, así se adapta al formato
exacto de cada export sin reescribir código.

Flujo de migración (el ERP reemplaza a Hansa como fuente de verdad):
    Hansa  --export .txt-->  este importador  -->  ERP (master)
    ERP  -->  publica a Tiendanube / MercadoLibre
    Ventas de los canales  -->  entran al ERP y descuentan stock

Uso programático:
    filas = leer_filas("clientes.txt")
    importar_clientes(filas, {"razon_social": "Nombre", "nro_doc": "CUIT", ...})

El `mapeo` acepta como valor el **nombre de columna** (si el archivo tiene
encabezado) o el **índice** entero de la columna (si no lo tiene).
"""
import csv
import io

from .db import db
from .models import (Cliente, Deposito, ListaPrecios, PrecioLista, Producto,
                     Variante)
from .services import registrar_movimiento

# Normalización de la condición de IVA de Hansa -> códigos del ERP.
# Ajustable según cómo venga en el export.
IVA_DEFAULT = {
    "responsable inscripto": "RI", "resp. inscripto": "RI",
    "resp. insc.": "RI", "resp insc": "RI", "ri": "RI",
    "monotributo": "MONOTRIBUTO", "monotributista": "MONOTRIBUTO",
    "resp. monotributo": "MONOTRIBUTO", "resp monotributo": "MONOTRIBUTO",
    "exento": "EXENTO", "iva exento": "EXENTO",
    "consumidor final": "CF", "consum. final": "CF", "cf": "CF", "final": "CF",
}


def _detectar_sep(muestra):
    for sep in ("\t", ";", ","):
        if sep in muestra:
            return sep
    return "\t"


def _decodificar(datos):
    """Los export de Hansa suelen venir en Latin-1/Windows-1252 (Argentina),
    no en UTF-8. Se prueba UTF-8 y se cae a cp1252 para conservar acentos y ñ."""
    if isinstance(datos, str):
        return datos
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return datos.decode(enc)
        except UnicodeDecodeError:
            continue
    return datos.decode("latin-1", errors="replace")


def leer_filas(path=None, contenido=None, sep=None, con_encabezado=True):
    """Lee un archivo delimitado y devuelve una lista de filas.

    Con encabezado -> lista de dicts {columna: valor}.
    Sin encabezado -> lista de listas (se accede por índice).
    """
    if contenido is None:
        with open(path, "rb") as f:
            contenido = _decodificar(f.read())
    else:
        contenido = _decodificar(contenido)
    # Normalizar fin de línea de Windows/Mac (evita romper csv con QUOTE_NONE).
    contenido = contenido.replace("\r\n", "\n").replace("\r", "\n")
    if sep is None:
        primera = contenido.splitlines()[0] if contenido.strip() else ""
        sep = _detectar_sep(primera)

    # QUOTE_NONE: los export de Hansa no usan comillas para delimitar; las
    # comillas que aparecen (p. ej. RK 2000 "Bikes") son parte del texto.
    buf = io.StringIO(contenido)
    if con_encabezado:
        return list(csv.DictReader(buf, delimiter=sep, quoting=csv.QUOTE_NONE))
    return [fila for fila in csv.reader(buf, delimiter=sep, quoting=csv.QUOTE_NONE)]


def _val(fila, mapeo, campo, default=None):
    """Obtiene el valor de `campo` según el mapeo (por nombre o índice)."""
    col = mapeo.get(campo)
    if col is None:
        return default
    try:
        if isinstance(col, int):
            return fila[col]
        return fila.get(col, default)
    except (KeyError, IndexError, TypeError):
        return default


def _limpiar(v):
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _num(v):
    if v in (None, ""):
        return None
    s = str(v).strip().replace(".", "").replace(",", ".") if str(v).count(",") == 1 \
        else str(v).strip()
    try:
        return float(s)
    except ValueError:
        return None


def _sku_raiz(sku):
    return str(sku).strip()[:6] if sku else None


# --------------------------------------------------------------- Clientes

def _detectar_col_iva(filas, iva_map, muestra=300):
    """Devuelve el nombre de la columna que contiene la condición de IVA,
    detectándola por los valores conocidos ("Resp. Insc.", "Consum. Final",
    etc.). Robusto ante cambios de posición entre exports."""
    from collections import Counter
    cont = Counter()
    for fila in filas[:muestra]:
        if not isinstance(fila, dict):
            return None
        for col, val in fila.items():
            if (str(val).strip().lower() if val else "") in iva_map:
                cont[col] += 1
    return cont.most_common(1)[0][0] if cont else None


def importar_clientes(filas, mapeo, iva_map=None):
    """Migra clientes. Campos posibles del mapeo: razon_social,
    nombre_fantasia, tipo_doc, nro_doc, condicion_iva, email, telefono,
    direccion, localidad, provincia, codigo_postal.

    Si no se mapea 'condicion_iva' (o su columna no tiene valores válidos), se
    detecta automáticamente la columna que contiene la condición de IVA."""
    iva_map = {**IVA_DEFAULT, **(iva_map or {})}
    mapeo = dict(mapeo)
    # Auto-detección de la columna de condición de IVA.
    col_cond = mapeo.get("condicion_iva")
    valida = col_cond and any(
        (str(_val(f, mapeo, "condicion_iva")).strip().lower() if _val(f, mapeo, "condicion_iva") else "") in iva_map
        for f in filas[:300])
    if not valida:
        detectada = _detectar_col_iva(filas, iva_map)
        if detectada:
            mapeo["condicion_iva"] = detectada

    stats = {"creados": 0, "actualizados": 0, "omitidos": 0}
    for fila in filas:
        razon = _limpiar(_val(fila, mapeo, "razon_social"))
        if not razon:
            stats["omitidos"] += 1
            continue
        codigo = _limpiar(_val(fila, mapeo, "codigo_externo"))
        tipo_doc = (_limpiar(_val(fila, mapeo, "tipo_doc")) or "CUIT").upper()
        nro_doc = _limpiar(_val(fila, mapeo, "nro_doc"))
        cond_raw = (_limpiar(_val(fila, mapeo, "condicion_iva")) or "").lower()
        condicion = iva_map.get(cond_raw, "CF")

        # Deduplicación: por código de Hansa si está; si no, por documento.
        existente = None
        if codigo:
            existente = Cliente.query.filter_by(codigo_externo=codigo).first()
        elif nro_doc:
            existente = Cliente.query.filter_by(tipo_doc=tipo_doc, nro_doc=nro_doc).first()

        c = existente or Cliente(razon_social=razon)
        c.codigo_externo = codigo or c.codigo_externo
        c.razon_social = razon
        c.tipo_doc = tipo_doc
        c.nro_doc = nro_doc
        c.condicion_iva = condicion
        for campo in ("nombre_fantasia", "email", "telefono", "direccion",
                      "localidad", "provincia", "codigo_postal"):
            val = _limpiar(_val(fila, mapeo, campo))
            if val:
                setattr(c, campo, val)
        if existente:
            stats["actualizados"] += 1
        else:
            db.session.add(c)
            stats["creados"] += 1
    db.session.commit()
    return stats


# --------------------------------------------------------------- Productos

def importar_productos(filas, mapeo):
    """Migra artículos de Hansa como variantes del ERP, agrupadas por SKU raíz.
    Campos: sku, nombre, marca, categoria, costo, descripcion, color, talle."""
    stats = {"productos_creados": 0, "variantes_creadas": 0,
             "variantes_actualizadas": 0, "omitidos": 0}
    for i, fila in enumerate(filas):
        sku = _limpiar(_val(fila, mapeo, "sku"))
        if not sku:
            stats["omitidos"] += 1
            continue
        raiz = _sku_raiz(sku)
        nombre = _limpiar(_val(fila, mapeo, "nombre")) or f"Producto {raiz}"
        prod = Producto.query.filter_by(sku_raiz=raiz).first()
        if not prod:
            prod = Producto(sku_raiz=raiz, nombre=nombre)
            db.session.add(prod)
            stats["productos_creados"] += 1
            db.session.flush()
        # Campos a nivel producto (se completan con lo que venga).
        for campo in ("marca", "categoria", "descripcion"):
            val = _limpiar(_val(fila, mapeo, campo))
            if val:
                setattr(prod, campo, val)
        costo = _num(_val(fila, mapeo, "costo"))
        if costo is not None:
            prod.costo = costo

        var = Variante.query.filter_by(sku=sku).first()
        color = _limpiar(_val(fila, mapeo, "color"))
        talle = _limpiar(_val(fila, mapeo, "talle"))
        barras = _limpiar(_val(fila, mapeo, "codigo_barras"))
        if var:
            if color:
                var.color = color
            if talle:
                var.talle = talle
            if barras:
                var.codigo_barras = barras
            stats["variantes_actualizadas"] += 1
        else:
            db.session.add(Variante(producto_id=prod.id, sku=sku, color=color,
                                    talle=talle, codigo_barras=barras))
            stats["variantes_creadas"] += 1
        if i % 500 == 0:  # commit por lotes (evita 10k+ commits sueltos)
            db.session.commit()
    db.session.commit()
    return stats


# --------------------------------------------------------------- Stock

def importar_stock(filas, mapeo, deposito_codigo="CENTRAL"):
    """Carga el stock inicial desde Hansa. Concilia con un movimiento 'ajuste'
    (idempotente). Campos: sku, cantidad, (opcional) deposito."""
    dep = Deposito.query.filter_by(codigo=deposito_codigo).first()
    if not dep:
        dep = Deposito(codigo=deposito_codigo, nombre=f"Depósito {deposito_codigo}")
        db.session.add(dep)
        db.session.commit()

    from .models import MovimientoStock, StockDeposito

    stats = {"ajustados": 0, "sin_variante": 0, "omitidos": 0}
    for i, fila in enumerate(filas):
        sku = _limpiar(_val(fila, mapeo, "sku"))
        cantidad = _num(_val(fila, mapeo, "cantidad"))
        if not sku or cantidad is None:
            stats["omitidos"] += 1
            continue
        var = Variante.query.filter_by(sku=sku).first()
        if not var:
            stats["sin_variante"] += 1
            continue
        saldo = StockDeposito.query.filter_by(
            variante_id=var.id, deposito_id=dep.id).first()
        if saldo is None:
            saldo = StockDeposito(variante_id=var.id, deposito_id=dep.id, cantidad=0)
            db.session.add(saldo)
        delta = cantidad - saldo.cantidad
        if abs(delta) > 1e-9:
            # Movimiento de ajuste (mantiene el ledger) + saldo, en lote.
            db.session.add(MovimientoStock(
                variante_id=var.id, deposito_id=dep.id, tipo="ajuste",
                cantidad=delta, motivo="Migración inicial desde Hansa"))
            saldo.cantidad = cantidad
            stats["ajustados"] += 1
        if i % 500 == 0:
            db.session.commit()
    db.session.commit()
    return stats
