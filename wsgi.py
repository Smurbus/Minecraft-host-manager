"""Point d'entrée WSGI pour la production (waitress / gunicorn)."""

from app import create_app

app = create_app()
