"""Modo seguro del ERP: por defecto NO se conecta a ninguna plataforma externa.

Mientras el sistema está en construcción y validación, ninguna operación puede
leer ni escribir en Tiendanube, MercadoLibre o AFIP a menos que se habilite
explícitamente la variable de entorno `ERP_PERMITIR_CONEXIONES`.

Esto es un candado de seguridad: aunque alguien dispare un endpoint o un botón,
si el modo seguro está activo (default), la operación se rechaza sin tocar nada
afuera. La base de datos local del ERP no se ve afectada.
"""
import os

# Valores que se consideran "habilitado".
_ON = {"1", "true", "si", "sí", "yes", "on"}


class ConexionesDeshabilitadas(RuntimeError):
    """Se intentó una operación con una plataforma externa en modo seguro."""


def conexiones_habilitadas():
    return os.environ.get("ERP_PERMITIR_CONEXIONES", "").strip().lower() in _ON


def requerir_conexiones(plataforma=""):
    """Lanza ConexionesDeshabilitadas si el modo seguro está activo."""
    if not conexiones_habilitadas():
        destino = f" ({plataforma})" if plataforma else ""
        raise ConexionesDeshabilitadas(
            f"Modo seguro activo: las conexiones externas{destino} están "
            "deshabilitadas. No se toca ninguna plataforma hasta habilitar "
            "ERP_PERMITIR_CONEXIONES=1 de forma explícita.")


def estado():
    return {"conexiones_habilitadas": conexiones_habilitadas(),
            "modo": "conectado" if conexiones_habilitadas() else "seguro (sin conexiones)"}
