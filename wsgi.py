"""Production entry point: gunicorn wsgi:app"""
import os

from teamos import create_app

app = create_app()

if os.environ.get("TRUST_PROXY") == "1":
    # Behind one trusted proxy (Render, Caddy): use the visitor's real address and https scheme, so login lockouts
    # and audit entries are per person instead of all showing the proxy's address.
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
