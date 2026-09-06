"""Endpoints de importación de datos externos al ERP."""
from flask import jsonify, request

from ..importar_tn import importar_catalogo
from . import erp_bp


@erp_bp.route("/importar/tiendanube", methods=["POST"])
def importar_tiendanube():
    """Importa/sincroniza el catálogo de Tiendanube (productos, variantes,
    stock y precios). Usa las credenciales del body o las variables de entorno
    TN_STORE_ID / TN_TOKEN."""
    data = request.get_json(silent=True) or {}
    try:
        stats = importar_catalogo(
            store_id=data.get("store_id"),
            token=data.get("token"),
            deposito_codigo=data.get("deposito", "CENTRAL"),
            lista_nombre=data.get("lista", "Tiendanube"),
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:  # error de red / API TN
        return jsonify({"error": f"Error importando de Tiendanube: {e}"}), 502
    return jsonify({"ok": True, "stats": stats})
