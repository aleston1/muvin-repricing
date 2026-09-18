"""Configuración de la base de datos del ERP.

Usa PostgreSQL en producción (variable de entorno DATABASE_URL) y cae a un
archivo SQLite local para desarrollo. A diferencia de los .json en disco que
usa el resto de la app (que Render pierde al reiniciar), la base del ERP está
pensada para ser la *fuente de verdad*: en producción debe apuntar a un
PostgreSQL administrado con backups.
"""
import os

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


def _normalizar_url(url: str) -> str:
    """Render/Heroku entregan la URL como 'postgres://...', pero SQLAlchemy
    espera el driver explícito 'postgresql://'. También forzamos psycopg2."""
    if not url:
        return url
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg2://" + url[len("postgresql://"):]
    return url


def database_url() -> str:
    url = _normalizar_url(os.environ.get("DATABASE_URL", ""))
    if url:
        return url
    # Desarrollo: SQLite en la raíz del proyecto.
    ruta = os.path.join(os.path.dirname(os.path.dirname(__file__)), "erp.db")
    return f"sqlite:///{ruta}"


def init_db(app):
    """Configura SQLAlchemy sobre la app Flask y crea las tablas si faltan."""
    app.config.setdefault("SQLALCHEMY_DATABASE_URI", database_url())
    app.config.setdefault("SQLALCHEMY_TRACK_MODIFICATIONS", False)
    app.config.setdefault("SQLALCHEMY_ENGINE_OPTIONS", {"pool_pre_ping": True})
    db.init_app(app)

    with app.app_context():
        # Importa los modelos para que estén registrados en el metadata.
        from . import models  # noqa: F401
        db.create_all()
