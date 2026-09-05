"""WSGI entry point for production (waitress / gunicorn)."""

from app import create_app

app = create_app()
