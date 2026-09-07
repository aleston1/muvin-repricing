"""ERP Muvin — módulo fundacional (Productos, Stock, Clientes, Precios).

Se integra a la app Flask existente llamando a `init_erp(app)` desde app.py.
Convive con el repricing de MercadoLibre y la sincronización de Tiendanube:
comparte el mismo proceso pero agrega su propia base de datos (la fuente de
verdad del negocio), servida bajo /api/erp y con UI en /erp.
"""
from .db import init_db


def init_erp(app):
    init_db(app)
    from .api import erp_bp
    app.register_blueprint(erp_bp)
    return app
