"""Lógica de negocio del ERP que no debe vivir en los endpoints.

Por ahora concentra el manejo de stock por movimientos (ledger): registrar un
movimiento y mantener sincronizado el saldo por depósito de forma atómica.
"""
from .db import db
from .models import Deposito, MovimientoStock, StockDeposito, Variante

# Tipos de movimiento y el signo que aplican al saldo.
SIGNO = {"ingreso": 1, "inicial": 1, "egreso": -1, "ajuste": 1}


def _saldo(variante_id, deposito_id):
    saldo = StockDeposito.query.filter_by(
        variante_id=variante_id, deposito_id=deposito_id).first()
    if saldo is None:
        saldo = StockDeposito(variante_id=variante_id,
                              deposito_id=deposito_id, cantidad=0)
        db.session.add(saldo)
    return saldo


def registrar_movimiento(variante_id, deposito_id, tipo, cantidad,
                         motivo=None, referencia=None, usuario=None,
                         permitir_negativo=False):
    """Registra un movimiento de stock y actualiza el saldo del depósito.

    `cantidad` siempre se pasa en positivo; el signo lo determina `tipo`.
    Para 'ajuste', `cantidad` es el delta (puede venir negativo).
    Devuelve (movimiento, saldo). Lanza ValueError ante datos inválidos.
    """
    if tipo not in SIGNO:
        raise ValueError(f"Tipo de movimiento inválido: {tipo}")
    if Variante.query.get(variante_id) is None:
        raise ValueError("Variante inexistente")
    if Deposito.query.get(deposito_id) is None:
        raise ValueError("Depósito inexistente")

    delta = cantidad if tipo == "ajuste" else abs(cantidad) * SIGNO[tipo]
    saldo = _saldo(variante_id, deposito_id)
    nuevo = saldo.cantidad + delta
    if nuevo < 0 and not permitir_negativo:
        raise ValueError(
            f"Stock insuficiente: hay {saldo.cantidad}, se intenta mover {delta}")

    saldo.cantidad = nuevo
    mov = MovimientoStock(
        variante_id=variante_id, deposito_id=deposito_id, tipo=tipo,
        cantidad=delta, motivo=motivo, referencia=referencia, usuario=usuario)
    db.session.add(mov)
    db.session.commit()
    return mov, saldo


def _deposito_por_defecto(codigo="CENTRAL"):
    from .models import Deposito
    dep = Deposito.query.filter_by(codigo=codigo).first()
    if not dep:
        dep = Deposito(codigo=codigo, nombre=f"Depósito {codigo}")
        db.session.add(dep)
        db.session.commit()
    return dep


def registrar_pedido(canal, canal_pedido_id, items, fecha=None, total=None,
                     cliente_nombre=None, cliente_id=None,
                     deposito_codigo="CENTRAL", descontar_stock=True):
    """Alta idempotente de un pedido de un canal y descuento de stock (una vez).

    `items`: lista de dicts {sku, descripcion, cantidad, precio_unitario}.
    Devuelve (pedido, creado: bool). Si el pedido ya existía, no lo duplica ni
    vuelve a descontar stock.
    """
    from .models import Pedido, PedidoItem, Variante

    canal_pedido_id = str(canal_pedido_id)
    pedido = Pedido.query.filter_by(canal=canal, canal_pedido_id=canal_pedido_id).first()
    creado = pedido is None
    if creado:
        pedido = Pedido(canal=canal, canal_pedido_id=canal_pedido_id,
                        cliente_nombre=cliente_nombre, cliente_id=cliente_id,
                        total=total or 0)
        if fecha:
            pedido.fecha = fecha
        db.session.add(pedido)
        db.session.flush()
        for it in items:
            db.session.add(PedidoItem(
                pedido_id=pedido.id, sku=(it.get("sku") or "").strip() or None,
                descripcion=it.get("descripcion"),
                cantidad=float(it.get("cantidad", 1)),
                precio_unitario=float(it.get("precio_unitario", 0) or 0)))
        db.session.commit()

    if descontar_stock and not pedido.stock_descontado:
        dep = _deposito_por_defecto(deposito_codigo)
        faltantes = []
        for it in pedido.items:
            if not it.sku:
                continue
            var = Variante.query.filter_by(sku=it.sku).first()
            if not var:
                faltantes.append(it.sku)
                continue
            registrar_movimiento(
                var.id, dep.id, "egreso", it.cantidad,
                motivo=f"Venta {canal}", referencia=f"{canal}:{canal_pedido_id}",
                permitir_negativo=True)  # el canal ya vendió; se refleja aunque quede negativo
        pedido.stock_descontado = True
        pedido.estado = "stock_descontado"
        if faltantes:
            pedido.observaciones = "SKU no encontrados en el ERP: " + ", ".join(faltantes)
        db.session.commit()

    return pedido, creado
