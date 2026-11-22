"""Production entry point: gunicorn wsgi:app"""
from teamos import create_app

app = create_app()
