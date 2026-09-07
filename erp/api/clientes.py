"""Endpoints de clientes."""
from flask import jsonify, request
from sqlalchemy import or_

from ..db import db
from ..models import Cliente
from . import erp_bp

CONDICIONES_IVA = {"RI", "MONOTRIBUTO", "EXENTO", "CF"}
TIPOS_DOC = {"CUIT", "CUIL", "DNI", "CDI", "PASAPORTE"}


@erp_bp.route("/clientes", methods=["GET"])
def listar_clientes():
    q = (request.args.get("q") or "").strip()
    limit = min(int(request.args.get("limit", 50)), 200)
    offset = int(request.args.get("offset", 0))

    query = Cliente.query
    if q:
        like = f"%{q}%"
        query = query.filter(or_(
            Cliente.razon_social.ilike(like),
            Cliente.nombre_fantasia.ilike(like),
            Cliente.nro_doc.ilike(like),
            Cliente.email.ilike(like),
        ))
    total = query.count()
    clientes = query.order_by(Cliente.razon_social).limit(limit).offset(offset).all()
    return jsonify({"total": total, "clientes": [c.to_dict() for c in clientes]})


@erp_bp.route("/clientes/<int:cid>", methods=["GET"])
def obtener_cliente(cid):
    return jsonify(Cliente.query.get_or_404(cid).to_dict())


@erp_bp.route("/clientes", methods=["POST"])
def crear_cliente():
    data = request.get_json(force=True) or {}
    if not data.get("razon_social"):
        return jsonify({"error": "Falta razon_social"}), 400

    tipo_doc = (data.get("tipo_doc") or "CUIT").upper()
    if tipo_doc not in TIPOS_DOC:
        return jsonify({"error": f"tipo_doc inválido. Válidos: {sorted(TIPOS_DOC)}"}), 400
    condicion = (data.get("condicion_iva") or "CF").upper()
    if condicion not in CONDICIONES_IVA:
        return jsonify({"error": f"condicion_iva inválida. Válidas: {sorted(CONDICIONES_IVA)}"}), 400

    nro_doc = (data.get("nro_doc") or "").strip() or None
    if nro_doc and Cliente.query.filter_by(tipo_doc=tipo_doc, nro_doc=nro_doc).first():
        return jsonify({"error": f"Ya existe un cliente con {tipo_doc} {nro_doc}"}), 409

    c = Cliente(
        razon_social=data["razon_social"].strip(),
        nombre_fantasia=data.get("nombre_fantasia"),
        tipo_doc=tipo_doc,
        nro_doc=nro_doc,
        condicion_iva=condicion,
        email=data.get("email"),
        telefono=data.get("telefono"),
        direccion=data.get("direccion"),
        localidad=data.get("localidad"),
        provincia=data.get("provincia"),
        codigo_postal=data.get("codigo_postal"),
        notas=data.get("notas"),
        activo=data.get("activo", True),
    )
    db.session.add(c)
    db.session.commit()
    return jsonify(c.to_dict()), 201


@erp_bp.route("/clientes/<int:cid>", methods=["PUT", "PATCH"])
def actualizar_cliente(cid):
    c = Cliente.query.get_or_404(cid)
    data = request.get_json(force=True) or {}
    for campo in ("razon_social", "nombre_fantasia", "email", "telefono",
                  "direccion", "localidad", "provincia", "codigo_postal",
                  "notas", "activo"):
        if campo in data:
            setattr(c, campo, data[campo])
    if "tipo_doc" in data:
        c.tipo_doc = data["tipo_doc"].upper()
    if "condicion_iva" in data:
        c.condicion_iva = data["condicion_iva"].upper()
    if "nro_doc" in data:
        c.nro_doc = (data["nro_doc"] or "").strip() or None
    db.session.commit()
    return jsonify(c.to_dict())
