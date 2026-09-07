"""Importadores para poblar el ERP con los datos que la operación ya tiene.

Uso:
    python -m erp.importar costos        # costos.json -> Producto.costo
    python -m erp.importar stock          # stock_cache.json -> stock inicial
    python -m erp.importar deposito-base  # crea el depósito 'CENTRAL'

Pensado como puente durante la convivencia con Hansa: se puede correr las
veces que haga falta (es idempotente por SKU raíz / SKU).
"""
import json
import os
import sys

from .db import db
from .models import Deposito, Producto, Variante
from .services import registrar_movimiento

RAIZ = os.path.dirname(os.path.dirname(__file__))


def _cargar_json(nombre):
    ruta = os.path.join(RAIZ, nombre)
    if not os.path.exists(ruta):
        print(f"  (no existe {nombre}, se omite)")
        return None
    with open(ruta) as f:
        return json.load(f)


def importar_costos():
    """costos.json = { sku_raiz: costo }. Crea/actualiza productos."""
    data = _cargar_json("costos.json") or {}
    creados = actualizados = 0
    for sku_raiz, costo in data.items():
        sku_raiz = str(sku_raiz).strip()[:6]
        p = Producto.query.filter_by(sku_raiz=sku_raiz).first()
        if p:
            p.costo = costo
            actualizados += 1
        else:
            db.session.add(Producto(
                sku_raiz=sku_raiz,
                nombre=f"Producto {sku_raiz}",  # placeholder hasta traer nombre real
                costo=costo,
            ))
            creados += 1
    db.session.commit()
    print(f"Costos: {creados} productos creados, {actualizados} actualizados.")


def crear_deposito_base():
    if not Deposito.query.filter_by(codigo="CENTRAL").first():
        db.session.add(Deposito(codigo="CENTRAL", nombre="Depósito Central"))
        db.session.commit()
        print("Depósito CENTRAL creado.")
    else:
        print("Depósito CENTRAL ya existe.")


def importar_stock():
    """stock_cache.json: estructura variable según sync.py. Intenta mapear
    { sku: cantidad } o { sku_raiz: {variantes...} }. Carga como movimiento
    'inicial' en el depósito CENTRAL."""
    data = _cargar_json("stock_cache.json")
    if not data:
        return
    crear_deposito_base()
    dep = Deposito.query.filter_by(codigo="CENTRAL").first()

    def _upsert_variante(sku):
        sku = str(sku).strip()
        v = Variante.query.filter_by(sku=sku).first()
        if v:
            return v
        sku_raiz = sku[:6]
        p = Producto.query.filter_by(sku_raiz=sku_raiz).first()
        if not p:
            p = Producto(sku_raiz=sku_raiz, nombre=f"Producto {sku_raiz}")
            db.session.add(p)
            db.session.flush()
        v = Variante(producto_id=p.id, sku=sku)
        db.session.add(v)
        db.session.flush()
        return v

    cargados = 0
    items = data.items() if isinstance(data, dict) else []
    for sku, valor in items:
        try:
            cantidad = float(valor) if not isinstance(valor, dict) else \
                float(valor.get("stock", 0) or 0)
        except (TypeError, ValueError):
            continue
        if cantidad <= 0:
            continue
        v = _upsert_variante(sku)
        registrar_movimiento(v.id, dep.id, "inicial", cantidad,
                             motivo="Carga inicial desde stock_cache.json")
        cargados += 1
    print(f"Stock inicial: {cargados} variantes cargadas en CENTRAL.")


def _main():
    # Import diferido para no crear dependencia circular con app.
    from app import app
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    with app.app_context():
        if cmd == "costos":
            importar_costos()
        elif cmd == "stock":
            importar_stock()
        elif cmd == "deposito-base":
            crear_deposito_base()
        else:
            print(__doc__)
            sys.exit(1)


if __name__ == "__main__":
    _main()
