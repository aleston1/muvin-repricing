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
    razon_social = Column(String(255), nullable=False, index=True)
    nombre_fantasia = Column(String(255))
    # CUIT | CUIL | DNI | CDI | PASAPORTE
    tipo_doc = Column(String(20), default="CUIT")
    nro_doc = Column(String(20), index=True)
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

    __table_args__ = (UniqueConstraint("tipo_doc", "nro_doc",
                                       name="uq_cliente_documento"),)

    def to_dict(self):
        return {
            "id": self.id,
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
