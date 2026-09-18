"""Endpoints de depósitos, existencias y movimientos de stock."""
from flask import jsonify, request

from ..db import db
from ..models import Deposito, MovimientoStock, StockDeposito, Variante
from ..services import registrar_movimiento
from . import erp_bp


# --------------------------------------------------------------- Depósitos

@erp_bp.route("/depositos", methods=["GET"])
def listar_depositos():
    deps = Deposito.query.order_by(Deposito.codigo).all()
    return jsonify({"depositos": [d.to_dict() for d in deps]})


@erp_bp.route("/depositos", methods=["POST"])
def crear_deposito():
    data = request.get_json(force=True) or {}
    if not data.get("codigo") or not data.get("nombre"):
        return jsonify({"error": "Faltan codigo y/o nombre"}), 400
    if Deposito.query.filter_by(codigo=data["codigo"]).first():
        return jsonify({"error": "Ya existe un depósito con ese código"}), 409
    d = Deposito(codigo=data["codigo"].strip(), nombre=data["nombre"].strip(),
                 activo=data.get("activo", True))
    db.session.add(d)
    db.session.commit()
    return jsonify(d.to_dict()), 201


# --------------------------------------------------------------- Existencias

@erp_bp.route("/stock", methods=["GET"])
def ver_stock():
    """Existencias, opcionalmente filtradas por SKU o depósito."""
    sku = request.args.get("sku")
    deposito_id = request.args.get("deposito_id", type=int)

    query = StockDeposito.query
    if sku:
        variante = Variante.query.filter_by(sku=sku.strip()).first()
        if not variante:
            return jsonify({"stock": []})
        query = query.filter_by(variante_id=variante.id)
    if deposito_id:
        query = query.filter_by(deposito_id=deposito_id)

    filas = []
    for s in query.all():
        filas.append({
            "variante_id": s.variante_id,
            "sku": s.variante.sku if s.variante else None,
            "deposito_id": s.deposito_id,
            "deposito": s.deposito.codigo if s.deposito else None,
            "cantidad": s.cantidad,
        })
    return jsonify({"stock": filas})


# --------------------------------------------------------------- Movimientos

@erp_bp.route("/stock/movimientos", methods=["POST"])
def crear_movimiento():
    data = request.get_json(force=True) or {}
    variante_id = data.get("variante_id")
    if not variante_id and data.get("sku"):
        v = Variante.query.filter_by(sku=data["sku"].strip()).first()
        variante_id = v.id if v else None
    if not variante_id:
        return jsonify({"error": "Falta variante_id o sku válido"}), 400
    if not data.get("deposito_id"):
        return jsonify({"error": "Falta deposito_id"}), 400
    if data.get("cantidad") is None:
        return jsonify({"error": "Falta cantidad"}), 400

    try:
        mov, saldo = registrar_movimiento(
            variante_id=variante_id,
            deposito_id=data["deposito_id"],
            tipo=data.get("tipo", "ingreso"),
            cantidad=float(data["cantidad"]),
            motivo=data.get("motivo"),
            referencia=data.get("referencia"),
            usuario=data.get("usuario"),
            permitir_negativo=bool(data.get("permitir_negativo")),
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    return jsonify({"movimiento": mov.to_dict(),
                    "saldo": saldo.cantidad}), 201


@erp_bp.route("/stock/movimientos", methods=["GET"])
def listar_movimientos():
    sku = request.args.get("sku")
    limit = min(int(request.args.get("limit", 100)), 500)
    query = MovimientoStock.query
    if sku:
        v = Variante.query.filter_by(sku=sku.strip()).first()
        if not v:
            return jsonify({"movimientos": []})
        query = query.filter_by(variante_id=v.id)
    movs = query.order_by(MovimientoStock.fecha.desc()).limit(limit).all()
    return jsonify({"movimientos": [m.to_dict() for m in movs]})
