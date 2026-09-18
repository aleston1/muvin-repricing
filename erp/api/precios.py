"""Endpoints de listas de precios (base del futuro módulo de Precios)."""
from flask import jsonify, request

from ..db import db
from ..models import ListaPrecios, PrecioLista, Variante
from . import erp_bp


@erp_bp.route("/listas-precios", methods=["GET"])
def listar_listas():
    listas = ListaPrecios.query.order_by(ListaPrecios.nombre).all()
    return jsonify({"listas": [l.to_dict() for l in listas]})


@erp_bp.route("/listas-precios", methods=["POST"])
def crear_lista():
    data = request.get_json(force=True) or {}
    if not data.get("nombre"):
        return jsonify({"error": "Falta nombre"}), 400
    if ListaPrecios.query.filter_by(nombre=data["nombre"]).first():
        return jsonify({"error": "Ya existe una lista con ese nombre"}), 409
    l = ListaPrecios(
        nombre=data["nombre"].strip(),
        moneda=data.get("moneda", "ARS"),
        incluye_iva=data.get("incluye_iva", True),
        activo=data.get("activo", True),
    )
    db.session.add(l)
    db.session.commit()
    return jsonify(l.to_dict()), 201


@erp_bp.route("/listas-precios/<int:lid>/precios", methods=["GET"])
def ver_precios(lid):
    ListaPrecios.query.get_or_404(lid)
    items = PrecioLista.query.filter_by(lista_id=lid).all()
    return jsonify({"precios": [i.to_dict() for i in items]})


@erp_bp.route("/listas-precios/<int:lid>/precios", methods=["POST", "PUT"])
def set_precio(lid):
    """Alta o actualización del precio de una variante en la lista (upsert)."""
    ListaPrecios.query.get_or_404(lid)
    data = request.get_json(force=True) or {}

    variante_id = data.get("variante_id")
    if not variante_id and data.get("sku"):
        v = Variante.query.filter_by(sku=data["sku"].strip()).first()
        variante_id = v.id if v else None
    if not variante_id:
        return jsonify({"error": "Falta variante_id o sku válido"}), 400
    if data.get("precio") is None:
        return jsonify({"error": "Falta precio"}), 400

    item = PrecioLista.query.filter_by(lista_id=lid, variante_id=variante_id).first()
    if item:
        item.precio = float(data["precio"])
    else:
        item = PrecioLista(lista_id=lid, variante_id=variante_id,
                           precio=float(data["precio"]))
        db.session.add(item)
    db.session.commit()
    return jsonify(item.to_dict()), 201
