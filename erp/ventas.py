"""Sincronización de ventas: trae pedidos de Tiendanube y MercadoLibre al ERP
y descuenta stock. Idempotente por (canal, id de pedido).
"""
import os

import requests

from .services import registrar_pedido

TN_BASE = "https://api.tiendanube.com/v1"
ML_BASE = "https://api.mercadolibre.com"


# --------------------------------------------------------------- Tiendanube

def importar_ventas_tn(store_id=None, token=None, max_paginas=20,
                       deposito_codigo="CENTRAL"):
    from sync import tn_headers  # helpers de auth ya existentes
    from .seguridad import requerir_conexiones
    requerir_conexiones("Tiendanube")

    store_id = store_id or os.environ.get("TN_STORE_ID", "")
    token = token or os.environ.get("TN_TOKEN", "")
    if not store_id or not token:
        raise ValueError("Faltan store_id o token de Tiendanube")

    stats = {"pedidos_nuevos": 0, "pedidos_existentes": 0, "paginas": 0}
    for page in range(1, max_paginas + 1):
        r = requests.get(
            f"{TN_BASE}/{store_id}/orders", headers=tn_headers(token),
            params={"page": page, "per_page": 50,
                    "fields": "id,number,contact_name,total,created_at,products"},
            timeout=40)
        if r.status_code == 404:
            break
        r.raise_for_status()
        pedidos = r.json()
        if not pedidos:
            break
        stats["paginas"] += 1
        for p in pedidos:
            items = [{
                "sku": (it.get("sku") or "").strip(),
                "descripcion": it.get("name"),
                "cantidad": it.get("quantity", 1),
                "precio_unitario": it.get("price"),
            } for it in (p.get("products") or [])]
            _, creado = registrar_pedido(
                canal="tiendanube", canal_pedido_id=p.get("id"), items=items,
                total=_num(p.get("total")),
                cliente_nombre=p.get("contact_name"),
                deposito_codigo=deposito_codigo)
            stats["pedidos_nuevos" if creado else "pedidos_existentes"] += 1
    return stats


# --------------------------------------------------------------- MercadoLibre

def _ml_sku(item):
    """SKU del ítem de ML: seller_sku o seller_custom_field."""
    it = item.get("item", {})
    sku = it.get("seller_sku") or it.get("seller_custom_field")
    return (str(sku).strip() if sku else None)


def importar_ventas_ml(user_id=None, token=None, limite=50,
                       deposito_codigo="CENTRAL"):
    from .seguridad import requerir_conexiones
    requerir_conexiones("MercadoLibre")

    user_id = user_id or os.environ.get("ML_USER_ID", "")
    token = token or os.environ.get("ML_TOKEN", "")
    if not user_id or not token:
        raise ValueError("Faltan user_id o token de MercadoLibre")

    stats = {"pedidos_nuevos": 0, "pedidos_existentes": 0}
    r = requests.get(
        f"{ML_BASE}/orders/search",
        headers={"Authorization": f"Bearer {token}"},
        params={"seller": user_id, "order.status": "paid",
                "sort": "date_desc", "limit": limite}, timeout=30)
    r.raise_for_status()
    for o in r.json().get("results", []):
        items = [{
            "sku": _ml_sku(it),
            "descripcion": it.get("item", {}).get("title"),
            "cantidad": it.get("quantity", 1),
            "precio_unitario": it.get("unit_price"),
        } for it in (o.get("order_items") or [])]
        buyer = o.get("buyer") or {}
        nombre = buyer.get("nickname") or \
            (f"{buyer.get('first_name','')} {buyer.get('last_name','')}".strip() or None)
        _, creado = registrar_pedido(
            canal="mercadolibre", canal_pedido_id=o.get("id"), items=items,
            total=o.get("total_amount"), cliente_nombre=nombre,
            deposito_codigo=deposito_codigo)
        stats["pedidos_nuevos" if creado else "pedidos_existentes"] += 1
    return stats


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
