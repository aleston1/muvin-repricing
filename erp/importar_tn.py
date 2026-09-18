"""Importador de catálogo desde Tiendanube hacia el ERP.

Trae productos, variantes, stock y precios reales de la tienda y los vuelca al
ERP. Es idempotente: se puede correr las veces que haga falta.

  - Producto: agrupado por SKU raíz (6 caracteres), igual que el resto de la app.
  - Stock: se concilia con un movimiento 'ajuste' (delta) contra el depósito
    indicado, así el ledger queda consistente y re-importar no duplica.
  - Precios: se guardan en una lista de precios (default "Tiendanube").

Reutiliza los helpers de autenticación de sync.py (tn_headers, tn_nombre).
"""
import os

import requests

from .db import db
from .models import (Deposito, ListaPrecios, PrecioLista, Producto, Variante)
from .services import registrar_movimiento

TN_BASE = "https://api.tiendanube.com/v1"


def _sku_raiz(sku):
    return str(sku).strip()[:6] if sku else None


def _primer_valor(values, idx):
    """Los 'values' de una variante TN son [{'es': 'Rojo'}, {'es': '42'}]."""
    try:
        v = values[idx]
    except (IndexError, TypeError):
        return None
    if isinstance(v, dict):
        return v.get("es") or next(iter(v.values()), None)
    return v


def _deposito(codigo):
    dep = Deposito.query.filter_by(codigo=codigo).first()
    if not dep:
        dep = Deposito(codigo=codigo, nombre=f"Depósito {codigo}")
        db.session.add(dep)
        db.session.commit()
    return dep


def _lista(nombre):
    lista = ListaPrecios.query.filter_by(nombre=nombre).first()
    if not lista:
        lista = ListaPrecios(nombre=nombre, moneda="ARS", incluye_iva=True)
        db.session.add(lista)
        db.session.commit()
    return lista


def importar_catalogo(store_id=None, token=None, deposito_codigo="CENTRAL",
                      lista_nombre="Tiendanube", max_paginas=100):
    """Importa/actualiza el catálogo completo. Devuelve estadísticas."""
    from sync import tn_headers, tn_nombre  # import diferido (evita ciclos)
    from .seguridad import requerir_conexiones
    requerir_conexiones("Tiendanube")

    store_id = store_id or os.environ.get("TN_STORE_ID", "")
    token = token or os.environ.get("TN_TOKEN", "")
    if not store_id or not token:
        raise ValueError("Faltan store_id o token de Tiendanube")

    dep = _deposito(deposito_codigo)
    lista = _lista(lista_nombre)

    stats = {"productos_creados": 0, "productos_actualizados": 0,
             "variantes_creadas": 0, "variantes_actualizadas": 0,
             "stock_ajustado": 0, "precios_seteados": 0, "paginas": 0,
             "omitidos_sin_sku": 0}

    for page in range(1, max_paginas + 1):
        r = requests.get(
            f"{TN_BASE}/{store_id}/products", headers=tn_headers(token),
            params={"page": page, "per_page": 200,
                    "fields": "id,name,brand,variants"}, timeout=40)
        if r.status_code == 404:  # después de la última página
            break
        r.raise_for_status()
        productos = r.json()
        if not productos:
            break
        stats["paginas"] += 1

        for p in productos:
            variantes = p.get("variants") or []
            # SKU raíz del producto: el de la primera variante con SKU.
            raiz = next((_sku_raiz(v.get("sku")) for v in variantes if v.get("sku")), None)
            if not raiz:
                stats["omitidos_sin_sku"] += 1
                continue

            prod = Producto.query.filter_by(sku_raiz=raiz).first()
            nombre = tn_nombre(p.get("name")) or f"Producto {raiz}"
            marca = p.get("brand") if isinstance(p.get("brand"), str) else None
            if prod:
                prod.nombre = nombre or prod.nombre
                if marca:
                    prod.marca = marca
                stats["productos_actualizados"] += 1
            else:
                prod = Producto(sku_raiz=raiz, nombre=nombre, marca=marca)
                db.session.add(prod)
                stats["productos_creados"] += 1
            db.session.flush()

            for v in variantes:
                sku = (v.get("sku") or "").strip()
                if not sku:
                    continue
                var = Variante.query.filter_by(sku=sku).first()
                color = _primer_valor(v.get("values"), 0)
                talle = _primer_valor(v.get("values"), 1)
                if var:
                    var.color = color or var.color
                    var.talle = talle or var.talle
                    stats["variantes_actualizadas"] += 1
                else:
                    var = Variante(producto_id=prod.id, sku=sku,
                                   color=color, talle=talle)
                    db.session.add(var)
                    stats["variantes_creadas"] += 1
                db.session.flush()

                # Stock: conciliar contra el valor de TN con un ajuste.
                tn_stock = v.get("stock")
                if tn_stock is not None:
                    try:
                        objetivo = float(tn_stock)
                    except (TypeError, ValueError):
                        objetivo = None
                    if objetivo is not None:
                        actual = var.stock_total()
                        delta = objetivo - actual
                        if abs(delta) > 1e-9:
                            registrar_movimiento(
                                var.id, dep.id, "ajuste", delta,
                                motivo="Sincronización Tiendanube",
                                referencia=f"TN prod {p.get('id')}",
                                permitir_negativo=True)
                            stats["stock_ajustado"] += 1

                # Precio.
                precio = v.get("promotional_price") or v.get("price")
                try:
                    precio = float(precio) if precio not in (None, "") else None
                except (TypeError, ValueError):
                    precio = None
                if precio is not None:
                    item = PrecioLista.query.filter_by(
                        lista_id=lista.id, variante_id=var.id).first()
                    if item:
                        item.precio = precio
                    else:
                        db.session.add(PrecioLista(
                            lista_id=lista.id, variante_id=var.id, precio=precio))
                    stats["precios_seteados"] += 1

            db.session.commit()

    return stats


def _main():
    from app import app
    with app.app_context():
        print(importar_catalogo())


if __name__ == "__main__":
    _main()
