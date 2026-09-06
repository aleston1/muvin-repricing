"""Endpoints de facturación electrónica.

Orquestan la lógica fiscal (erp.afip.comprobantes) con el cliente de AFIP
(erp.afip.wsfev1). Se puede armar y previsualizar un comprobante sin tocar
AFIP; la emisión real (obtener el CAE) requiere el certificado configurado.
"""
import json

from flask import jsonify, request

from ..afip import comprobantes as fisc
from ..afip import wsfev1
from ..db import db
from ..models import Cliente, Comprobante, PuntoVenta
from . import erp_bp

# Condición IVA del emisor (Muvin). Configurable a futuro; default RI.
import os
EMISOR_CONDICION = os.environ.get("AFIP_EMISOR_CONDICION", "RI")


def _armar_totales(data):
    items = [fisc.ItemComprobante(
        descripcion=i.get("descripcion", ""),
        cantidad=float(i["cantidad"]),
        precio_unitario=float(i["precio_unitario"]),
        alicuota_pct=float(i.get("alicuota_pct", 21.0)),
    ) for i in data.get("items", [])]
    if not items:
        raise ValueError("El comprobante no tiene ítems")

    cliente = Cliente.query.get(data["cliente_id"]) if data.get("cliente_id") else None
    receptor_cond = cliente.condicion_iva if cliente else data.get("receptor_condicion", "CF")

    clase = fisc.clase_comprobante(EMISOR_CONDICION, receptor_cond)
    cbte_tipo = fisc.tipo_comprobante(EMISOR_CONDICION, receptor_cond,
                                      data.get("documento", "factura"))
    incluye_iva = bool(data.get("incluye_iva", False))
    totales = fisc.calcular_totales(items, clase=clase, incluye_iva=incluye_iva)
    iva_arr = fisc.iva_array_wsfev1(totales, clase=clase)
    return cliente, clase, cbte_tipo, totales, iva_arr, items


@erp_bp.route("/facturacion/estado", methods=["GET"])
def afip_estado():
    """¿Está lista la configuración de AFIP? (modo, CUIT, certificado)."""
    return jsonify(wsfev1.estado_configuracion())


@erp_bp.route("/facturacion/preview", methods=["POST"])
def factura_preview():
    """Calcula tipo de comprobante y totales SIN emitir. Útil para la UI."""
    data = request.get_json(force=True) or {}
    try:
        cliente, clase, cbte_tipo, totales, iva_arr, _ = _armar_totales(data)
    except (KeyError, ValueError) as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({
        "clase": clase,
        "cbte_tipo": cbte_tipo,
        "neto": totales.neto,
        "iva": totales.iva,
        "total": totales.total,
        "iva_detalle": iva_arr,
        "receptor": cliente.razon_social if cliente else data.get("receptor_condicion", "CF"),
    })


@erp_bp.route("/puntos-venta", methods=["GET", "POST"])
def puntos_venta():
    if request.method == "GET":
        pvs = PuntoVenta.query.order_by(PuntoVenta.numero).all()
        return jsonify({"puntos_venta": [p.to_dict() for p in pvs]})
    data = request.get_json(force=True) or {}
    if data.get("numero") is None:
        return jsonify({"error": "Falta numero"}), 400
    if PuntoVenta.query.filter_by(numero=data["numero"]).first():
        return jsonify({"error": "Ya existe ese punto de venta"}), 409
    pv = PuntoVenta(numero=int(data["numero"]), descripcion=data.get("descripcion"))
    db.session.add(pv)
    db.session.commit()
    return jsonify(pv.to_dict()), 201


@erp_bp.route("/facturacion/emitir", methods=["POST"])
def factura_emitir():
    """Emite un comprobante contra AFIP y guarda el CAE.

    Body: { cliente_id, punto_venta, documento, incluye_iva, concepto,
            items:[{descripcion,cantidad,precio_unitario,alicuota_pct}] }
    """
    data = request.get_json(force=True) or {}
    if data.get("punto_venta") is None:
        return jsonify({"error": "Falta punto_venta"}), 400
    try:
        cliente, clase, cbte_tipo, totales, iva_arr, items = _armar_totales(data)
    except (KeyError, ValueError) as e:
        return jsonify({"error": str(e)}), 400

    # Persistir como borrador antes de llamar a AFIP (trazabilidad).
    comp = Comprobante(
        cliente_id=cliente.id if cliente else None,
        punto_venta=int(data["punto_venta"]),
        cbte_tipo=cbte_tipo,
        concepto=int(data.get("concepto", 1)),
        neto=totales.neto, iva=totales.iva, total=totales.total,
        estado="borrador",
        items_json=json.dumps([i.__dict__ for i in items], ensure_ascii=False),
    )
    db.session.add(comp)
    db.session.commit()

    doc_tipo = fisc.doc_tipo_receptor(
        cliente.tipo_doc if cliente else None,
        cliente.condicion_iva if cliente else "CF")
    doc_nro = (cliente.nro_doc if cliente else None) or 0

    try:
        res = wsfev1.emitir(
            punto_venta=comp.punto_venta, cbte_tipo=cbte_tipo,
            doc_tipo=doc_tipo, doc_nro=doc_nro,
            totales=totales, iva_array=iva_arr, concepto=comp.concepto)
    except wsfev1.AfipConfigError as e:
        comp.estado = "borrador"
        comp.observaciones = f"Sin emitir (config AFIP): {e}"
        db.session.commit()
        return jsonify({"error": str(e), "comprobante": comp.to_dict(),
                        "detalle": "Comprobante guardado como borrador; "
                                   "falta configurar el certificado de AFIP."}), 503
    except wsfev1.AfipError as e:
        comp.estado = "rechazado"
        comp.observaciones = str(e)
        db.session.commit()
        return jsonify({"error": str(e), "comprobante": comp.to_dict()}), 502

    comp.estado = "autorizado"
    comp.numero = res["numero"]
    comp.cae = res["cae"]
    comp.cae_vencimiento = res["cae_vencimiento"]
    db.session.commit()
    return jsonify({"ok": True, "comprobante": comp.to_dict()}), 201


@erp_bp.route("/comprobantes", methods=["GET"])
def listar_comprobantes():
    limit = min(int(request.args.get("limit", 50)), 200)
    comps = Comprobante.query.order_by(Comprobante.fecha.desc()).limit(limit).all()
    return jsonify({"comprobantes": [c.to_dict() for c in comps]})
