"""Endpoints de pedidos/ventas y su sincronización con los canales."""
from flask import jsonify, request

from ..models import Pedido
from ..ventas import importar_ventas_ml, importar_ventas_tn
from . import erp_bp


@erp_bp.route("/pedidos", methods=["GET"])
def listar_pedidos():
    canal = request.args.get("canal")
    limit = min(int(request.args.get("limit", 50)), 200)
    query = Pedido.query
    if canal:
        query = query.filter_by(canal=canal)
    pedidos = query.order_by(Pedido.fecha.desc()).limit(limit).all()
    return jsonify({"pedidos": [p.to_dict() for p in pedidos]})


@erp_bp.route("/pedidos/<int:pid>", methods=["GET"])
def obtener_pedido(pid):
    return jsonify(Pedido.query.get_or_404(pid).to_dict())


@erp_bp.route("/pedidos/sincronizar", methods=["POST"])
def sincronizar_ventas():
    """Trae ventas de los canales indicados y descuenta stock.
    Body: { canales: ["tiendanube","mercadolibre"] } (default: ambos)."""
    data = request.get_json(silent=True) or {}
    canales = data.get("canales") or ["tiendanube", "mercadolibre"]
    resultado = {}
    for canal in canales:
        try:
            if canal == "tiendanube":
                resultado["tiendanube"] = importar_ventas_tn()
            elif canal == "mercadolibre":
                resultado["mercadolibre"] = importar_ventas_ml()
        except ValueError as e:
            resultado[canal] = {"error": str(e)}
        except Exception as e:
            resultado[canal] = {"error": f"Error sincronizando: {e}"}
    return jsonify({"resultado": resultado})


@erp_bp.route("/pedidos/<int:pid>/entregar", methods=["POST"])
def marcar_entregado(pid):
    from ..db import db
    p = Pedido.query.get_or_404(pid)
    p.entregado = True
    if p.estado in ("pendiente", "stock_descontado", "facturado"):
        p.estado = "entregado"
    db.session.commit()
    return jsonify(p.to_dict())
