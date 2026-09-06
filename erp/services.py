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
