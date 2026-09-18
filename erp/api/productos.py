"""Endpoints de productos y variantes."""
from flask import jsonify, request
from sqlalchemy import or_

from ..db import db
from ..models import Producto, Variante
from . import erp_bp


def _sku_raiz(sku):
    return str(sku).strip()[:6] if sku else None


@erp_bp.route("/productos", methods=["GET"])
def listar_productos():
    q = (request.args.get("q") or "").strip()
    activo = request.args.get("activo")
    limit = min(int(request.args.get("limit", 50)), 200)
    offset = int(request.args.get("offset", 0))

    query = Producto.query
    if q:
        like = f"%{q}%"
        query = query.filter(or_(
            Producto.nombre.ilike(like),
            Producto.sku_raiz.ilike(like),
            Producto.marca.ilike(like),
        ))
    if activo in ("0", "false"):
        query = query.filter_by(activo=False)
    elif activo in ("1", "true"):
        query = query.filter_by(activo=True)

    total = query.count()
    productos = query.order_by(Producto.nombre).limit(limit).offset(offset).all()
    return jsonify({
        "total": total,
        "productos": [p.to_dict(con_variantes=False) for p in productos],
    })


@erp_bp.route("/productos/<int:pid>", methods=["GET"])
def obtener_producto(pid):
    p = Producto.query.get_or_404(pid)
    return jsonify(p.to_dict())


@erp_bp.route("/productos", methods=["POST"])
def crear_producto():
    data = request.get_json(force=True) or {}
    sku_raiz = _sku_raiz(data.get("sku_raiz") or data.get("sku"))
    if not sku_raiz:
        return jsonify({"error": "Falta sku_raiz"}), 400
    if not data.get("nombre"):
        return jsonify({"error": "Falta nombre"}), 400
    if Producto.query.filter_by(sku_raiz=sku_raiz).first():
        return jsonify({"error": f"Ya existe un producto con SKU raíz {sku_raiz}"}), 409

    p = Producto(
        sku_raiz=sku_raiz,
        nombre=data["nombre"],
        marca=data.get("marca"),
        categoria=data.get("categoria"),
        descripcion=data.get("descripcion"),
        costo=data.get("costo"),
        activo=data.get("activo", True),
    )
    db.session.add(p)
    db.session.commit()
    return jsonify(p.to_dict()), 201


@erp_bp.route("/productos/<int:pid>", methods=["PUT", "PATCH"])
def actualizar_producto(pid):
    p = Producto.query.get_or_404(pid)
    data = request.get_json(force=True) or {}
    for campo in ("nombre", "marca", "categoria", "descripcion", "costo", "activo"):
        if campo in data:
            setattr(p, campo, data[campo])
    db.session.commit()
    return jsonify(p.to_dict())


@erp_bp.route("/productos/<int:pid>/variantes", methods=["POST"])
def crear_variante(pid):
    Producto.query.get_or_404(pid)
    data = request.get_json(force=True) or {}
    if not data.get("sku"):
        return jsonify({"error": "Falta sku"}), 400
    if Variante.query.filter_by(sku=data["sku"]).first():
        return jsonify({"error": f"Ya existe la variante {data['sku']}"}), 409

    v = Variante(
        producto_id=pid,
        sku=data["sku"].strip(),
        color=data.get("color"),
        talle=data.get("talle"),
        codigo_barras=data.get("codigo_barras"),
    )
    db.session.add(v)
    db.session.commit()
    return jsonify(v.to_dict()), 201
