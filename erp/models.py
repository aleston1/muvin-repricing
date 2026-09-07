"""Modelo de datos del ERP Muvin.

Módulo fundacional (Fase 1): Productos, Variantes, Depósitos, Stock,
Movimientos de stock, Clientes y Listas de precios.

Diseño pensado para que los módulos siguientes (Facturación electrónica
AFIP/ARCA, Compras, Cuenta corriente) se enganchen sin reescribir esto:

  - El SKU raíz (6 primeros caracteres) sigue siendo la clave de negocio que
    ya usan MercadoLibre y Tiendanube en el resto de la app.
  - El stock se lleva por *movimientos* (ledger): la existencia en cada
    depósito es la suma de sus movimientos. Nunca se pisa un número "a mano"
    sin dejar el movimiento que lo justifica -> auditable.
"""
from datetime import datetime

from sqlalchemy import (
    Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text,
    UniqueConstraint, func,
)
from sqlalchemy.orm import relationship

from .db import db


def _ahora():
    return datetime.utcnow()


class TimestampMixin:
    creado = Column(DateTime, default=_ahora, nullable=False)
    actualizado = Column(DateTime, default=_ahora, onupdate=_ahora, nullable=False)


# --------------------------------------------------------------- Productos

class Producto(db.Model, TimestampMixin):
    """Producto base, identificado por SKU raíz (los primeros 6 caracteres
    del SKU, igual que en el repricing y la sincronización de canales)."""
    __tablename__ = "productos"

    id = Column(Integer, primary_key=True)
    sku_raiz = Column(String(6), unique=True, nullable=False, index=True)
    nombre = Column(String(255), nullable=False)
    marca = Column(String(120), index=True)
    categoria = Column(String(120), index=True)
    descripcion = Column(Text)
    # Costo de reposición (sin IVA). Fuente inicial: costos.json por SKU raíz.
    costo = Column(Float)
    activo = Column(Boolean, default=True, nullable=False)

    variantes = relationship(
        "Variante", back_populates="producto",
        cascade="all, delete-orphan", lazy="selectin",
    )

    def stock_total(self):
        return sum(v.stock_total() for v in self.variantes)

    def to_dict(self, con_variantes=True):
        d = {
            "id": self.id,
            "sku_raiz": self.sku_raiz,
            "nombre": self.nombre,
            "marca": self.marca,
            "categoria": self.categoria,
            "descripcion": self.descripcion,
            "costo": self.costo,
            "activo": self.activo,
            "stock_total": self.stock_total(),
        }
        if con_variantes:
            d["variantes"] = [v.to_dict() for v in self.variantes]
        return d


class Variante(db.Model, TimestampMixin):
    """Variante concreta de un producto (color/talle). Es lo que se factura
    y sobre lo que se lleva stock."""
    __tablename__ = "variantes"

    id = Column(Integer, primary_key=True)
    producto_id = Column(Integer, ForeignKey("productos.id"), nullable=False, index=True)
    sku = Column(String(64), unique=True, nullable=False, index=True)
    color = Column(String(80))
    talle = Column(String(40))
    codigo_barras = Column(String(64), index=True)

    producto = relationship("Producto", back_populates="variantes")
    stock = relationship(
        "StockDeposito", back_populates="variante",
        cascade="all, delete-orphan", lazy="selectin",
    )

    def stock_total(self):
        return sum(s.cantidad for s in self.stock)

    def to_dict(self):
        return {
            "id": self.id,
            "producto_id": self.producto_id,
            "sku": self.sku,
            "color": self.color,
            "talle": self.talle,
            "codigo_barras": self.codigo_barras,
            "stock_total": self.stock_total(),
            "stock": [s.to_dict() for s in self.stock],
        }


# --------------------------------------------------------------- Depósitos y stock

class Deposito(db.Model, TimestampMixin):
    __tablename__ = "depositos"

    id = Column(Integer, primary_key=True)
    codigo = Column(String(20), unique=True, nullable=False)
    nombre = Column(String(120), nullable=False)
    activo = Column(Boolean, default=True, nullable=False)

    def to_dict(self):
        return {"id": self.id, "codigo": self.codigo,
                "nombre": self.nombre, "activo": self.activo}


class StockDeposito(db.Model):
    """Existencia actual de una variante en un depósito. Es un *cache* de la
    suma de movimientos, para consultar rápido sin recorrer el ledger."""
    __tablename__ = "stock_deposito"
    __table_args__ = (UniqueConstraint("variante_id", "deposito_id",
                                       name="uq_stock_variante_deposito"),)

    id = Column(Integer, primary_key=True)
    variante_id = Column(Integer, ForeignKey("variantes.id"), nullable=False, index=True)
    deposito_id = Column(Integer, ForeignKey("depositos.id"), nullable=False, index=True)
    cantidad = Column(Float, default=0, nullable=False)

    variante = relationship("Variante", back_populates="stock")
    deposito = relationship("Deposito")

    def to_dict(self):
        return {
            "deposito_id": self.deposito_id,
            "deposito": self.deposito.codigo if self.deposito else None,
            "cantidad": self.cantidad,
        }


class MovimientoStock(db.Model):
    """Ledger de stock. Cada alta/baja/ajuste queda registrada acá; el saldo
    en StockDeposito se deriva de estos movimientos."""
    __tablename__ = "movimientos_stock"

    id = Column(Integer, primary_key=True)
    fecha = Column(DateTime, default=_ahora, nullable=False, index=True)
    variante_id = Column(Integer, ForeignKey("variantes.id"), nullable=False, index=True)
    deposito_id = Column(Integer, ForeignKey("depositos.id"), nullable=False, index=True)
    # ingreso | egreso | ajuste | inicial
    tipo = Column(String(20), nullable=False)
    cantidad = Column(Float, nullable=False)  # +/- según tipo
    motivo = Column(String(255))
    referencia = Column(String(120))  # nro de factura, remito, orden, etc.
    usuario = Column(String(120))

    variante = relationship("Variante")
    deposito = relationship("Deposito")

    def to_dict(self):
        return {
            "id": self.id,
            "fecha": self.fecha.isoformat() if self.fecha else None,
            "variante_id": self.variante_id,
            "deposito_id": self.deposito_id,
            "tipo": self.tipo,
            "cantidad": self.cantidad,
            "motivo": self.motivo,
            "referencia": self.referencia,
            "usuario": self.usuario,
        }


# --------------------------------------------------------------- Clientes

class Cliente(db.Model, TimestampMixin):
    """Base de clientes con datos fiscales necesarios para facturar en AR."""
    __tablename__ = "clientes"

    id = Column(Integer, primary_key=True)
    # Código de Hansa (u otro sistema origen). Es el identificador estable para
    # deduplicar en la migración: el CUIT no sirve como clave porque muchos
    # consumidores finales comparten un CUIT ficticio (11111111).
    codigo_externo = Column(String(20), unique=True, index=True)
    razon_social = Column(String(255), nullable=False, index=True)
    nombre_fantasia = Column(String(255))
    # CUIT | CUIL | DNI | CDI | PASAPORTE
    tipo_doc = Column(String(20), default="CUIT")
    nro_doc = Column(String(30), index=True)
    # RI (Resp. Inscripto) | MONOTRIBUTO | EXENTO | CF (Consumidor Final)
    condicion_iva = Column(String(30), default="CF")
    email = Column(String(180))
    telefono = Column(String(60))
    direccion = Column(String(255))
    localidad = Column(String(120))
    provincia = Column(String(120))
    codigo_postal = Column(String(20))
    notas = Column(Text)
    activo = Column(Boolean, default=True, nullable=False)

    def to_dict(self):
        return {
            "id": self.id,
            "codigo_externo": self.codigo_externo,
            "razon_social": self.razon_social,
            "nombre_fantasia": self.nombre_fantasia,
            "tipo_doc": self.tipo_doc,
            "nro_doc": self.nro_doc,
            "condicion_iva": self.condicion_iva,
            "email": self.email,
            "telefono": self.telefono,
            "direccion": self.direccion,
            "localidad": self.localidad,
            "provincia": self.provincia,
            "codigo_postal": self.codigo_postal,
            "notas": self.notas,
            "activo": self.activo,
        }


# --------------------------------------------------------------- Listas de precios
# Base para el futuro módulo de Precios: hoy guarda listas por variante; luego
# se le suman reglas (markup por marca, por canal, redondeo, etc.).

class ListaPrecios(db.Model, TimestampMixin):
    __tablename__ = "listas_precios"

    id = Column(Integer, primary_key=True)
    nombre = Column(String(120), unique=True, nullable=False)
    moneda = Column(String(10), default="ARS")
    # Si aplica IVA sobre el precio de lista o el precio ya lo incluye.
    incluye_iva = Column(Boolean, default=True, nullable=False)
    activo = Column(Boolean, default=True, nullable=False)

    items = relationship(
        "PrecioLista", back_populates="lista",
        cascade="all, delete-orphan", lazy="selectin",
    )

    def to_dict(self):
        return {"id": self.id, "nombre": self.nombre, "moneda": self.moneda,
                "incluye_iva": self.incluye_iva, "activo": self.activo}


class PrecioLista(db.Model, TimestampMixin):
    __tablename__ = "precios_lista"
    __table_args__ = (UniqueConstraint("lista_id", "variante_id",
                                       name="uq_precio_lista_variante"),)

    id = Column(Integer, primary_key=True)
    lista_id = Column(Integer, ForeignKey("listas_precios.id"), nullable=False, index=True)
    variante_id = Column(Integer, ForeignKey("variantes.id"), nullable=False, index=True)
    precio = Column(Float, nullable=False)

    lista = relationship("ListaPrecios", back_populates="items")
    variante = relationship("Variante")

    def to_dict(self):
        return {"id": self.id, "lista_id": self.lista_id,
                "variante_id": self.variante_id, "precio": self.precio}


# --------------------------------------------------------------- Facturación (AFIP)

class PuntoVenta(db.Model, TimestampMixin):
    """Punto de venta habilitado en AFIP. La numeración de comprobantes es
    por (punto de venta, tipo de comprobante)."""
    __tablename__ = "puntos_venta"

    id = Column(Integer, primary_key=True)
    numero = Column(Integer, unique=True, nullable=False)  # p. ej. 1, 2, 3
    descripcion = Column(String(120))
    activo = Column(Boolean, default=True, nullable=False)

    def to_dict(self):
        return {"id": self.id, "numero": self.numero,
                "descripcion": self.descripcion, "activo": self.activo}


class Comprobante(db.Model, TimestampMixin):
    """Comprobante electrónico emitido (o pendiente). Una vez con CAE es
    inmutable: no se edita ni se borra, se anula con nota de crédito."""
    __tablename__ = "comprobantes"
    __table_args__ = (UniqueConstraint("punto_venta", "cbte_tipo", "numero",
                                       name="uq_comprobante_pv_tipo_nro"),)

    id = Column(Integer, primary_key=True)
    fecha = Column(DateTime, default=_ahora, nullable=False, index=True)
    cliente_id = Column(Integer, ForeignKey("clientes.id"), index=True)
    punto_venta = Column(Integer, nullable=False)
    cbte_tipo = Column(Integer, nullable=False)  # CbteTipo AFIP (1,6,11,...)
    numero = Column(Integer)                      # asignado al obtener el CAE
    concepto = Column(Integer, default=1)         # 1=Productos 2=Servicios 3=Ambos
    neto = Column(Float, default=0)
    iva = Column(Float, default=0)
    total = Column(Float, default=0)
    # borrador | autorizado | rechazado | anulado
    estado = Column(String(20), default="borrador", nullable=False, index=True)
    cae = Column(String(20))
    cae_vencimiento = Column(String(10))
    # JSON serializado de los ítems (snapshot inmutable de lo facturado).
    items_json = Column(Text)
    observaciones = Column(Text)

    cliente = relationship("Cliente")

    def to_dict(self):
        return {
            "id": self.id,
            "fecha": self.fecha.isoformat() if self.fecha else None,
            "cliente_id": self.cliente_id,
            "punto_venta": self.punto_venta,
            "cbte_tipo": self.cbte_tipo,
            "numero": self.numero,
            "concepto": self.concepto,
            "neto": self.neto,
            "iva": self.iva,
            "total": self.total,
            "estado": self.estado,
            "cae": self.cae,
            "cae_vencimiento": self.cae_vencimiento,
            "observaciones": self.observaciones,
        }


# --------------------------------------------------------------- Ventas / Pedidos

class Pedido(db.Model, TimestampMixin):
    """Venta que entra desde un canal (Tiendanube / MercadoLibre) al ERP.

    Al registrarse descuenta stock una única vez (`stock_descontado`) y puede
    facturarse (enlazado a un Comprobante) y entregarse. Es idempotente por
    (canal, canal_pedido_id): re-sincronizar no duplica."""
    __tablename__ = "pedidos"
    __table_args__ = (UniqueConstraint("canal", "canal_pedido_id",
                                       name="uq_pedido_canal_id"),)

    id = Column(Integer, primary_key=True)
    canal = Column(String(20), nullable=False, index=True)  # tiendanube | mercadolibre | manual
    canal_pedido_id = Column(String(60), nullable=False, index=True)  # id/nro en el canal
    fecha = Column(DateTime, default=_ahora, nullable=False, index=True)
    cliente_id = Column(Integer, ForeignKey("clientes.id"), index=True)
    cliente_nombre = Column(String(255))  # nombre tal cual vino del canal
    total = Column(Float, default=0)
    # pendiente | stock_descontado | facturado | entregado | cancelado
    estado = Column(String(30), default="pendiente", nullable=False, index=True)
    stock_descontado = Column(Boolean, default=False, nullable=False)
    comprobante_id = Column(Integer, ForeignKey("comprobantes.id"))
    entregado = Column(Boolean, default=False, nullable=False)
    observaciones = Column(Text)

    cliente = relationship("Cliente")
    comprobante = relationship("Comprobante")
    items = relationship("PedidoItem", back_populates="pedido",
                         cascade="all, delete-orphan", lazy="selectin")

    def to_dict(self, con_items=True):
        d = {
            "id": self.id,
            "canal": self.canal,
            "canal_pedido_id": self.canal_pedido_id,
            "fecha": self.fecha.isoformat() if self.fecha else None,
            "cliente_id": self.cliente_id,
            "cliente_nombre": self.cliente_nombre,
            "total": self.total,
            "estado": self.estado,
            "stock_descontado": self.stock_descontado,
            "entregado": self.entregado,
            "comprobante_id": self.comprobante_id,
        }
        if con_items:
            d["items"] = [i.to_dict() for i in self.items]
        return d


class PedidoItem(db.Model):
    __tablename__ = "pedido_items"

    id = Column(Integer, primary_key=True)
    pedido_id = Column(Integer, ForeignKey("pedidos.id"), nullable=False, index=True)
    sku = Column(String(64), index=True)
    descripcion = Column(String(255))
    cantidad = Column(Float, nullable=False, default=1)
    precio_unitario = Column(Float, default=0)

    pedido = relationship("Pedido", back_populates="items")

    def to_dict(self):
        return {"sku": self.sku, "descripcion": self.descripcion,
                "cantidad": self.cantidad, "precio_unitario": self.precio_unitario}
