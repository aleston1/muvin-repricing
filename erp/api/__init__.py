"""Blueprint de la API del ERP. Los módulos de rutas se registran sobre él."""
from flask import Blueprint

erp_bp = Blueprint("erp", __name__, url_prefix="/api/erp")

# Importar los módulos registra sus rutas sobre erp_bp.
from . import productos, stock, clientes, precios  # noqa: E402,F401
