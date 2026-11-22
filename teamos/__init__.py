"""Salon Shastra TeamOS — application factory."""
from __future__ import annotations

import os
import secrets
import time

from flask import Flask, g, render_template, request

import logging

from . import logging_setup, timeutil
from .db import close_db, get_db, migrate, open_standalone
from .domain import buyer_fields as BF
from .domain import form_templates as FT
from .domain import seller_fields as SF
from .domain.common import format_inr
from .domain.errors import PermissionDenied
from .permissions import CAPABILITIES, ROLE_LABELS
from .security import can, check_csrf, enforce_mfa, load_current_user, log_denied

__version__ = "0.4.0-m4"


def create_app(overrides: dict | None = None) -> Flask:
    app = Flask(__name__, instance_relative_config=True)
    app.config.update(
        SECRET_KEY=os.environ.get("SECRET_KEY") or secrets.token_hex(32),
        DATABASE_URL=os.environ.get("DATABASE_URL", "sqlite:///" + os.path.join(app.instance_path, "teamos.db")),
        SESSION_IDLE_MINUTES=int(os.environ.get("SESSION_IDLE_MINUTES", "240")),
        SESSION_ABSOLUTE_HOURS=int(os.environ.get("SESSION_ABSOLUTE_HOURS", "12")),
        COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "1") == "1",
        APP_ENV=os.environ.get("APP_ENV", "development"),
        # Whole request (a form may carry several documents); each file is still capped at 5 MB in files.py.
        MAX_CONTENT_LENGTH=16 * 1024 * 1024,
        # Fernet key for encrypting uploaded documents at rest. Without it, uploads are refused.
        FILES_KEY=os.environ.get("FILES_KEY"),
        PUBLIC_BASE_URL=os.environ.get("PUBLIC_BASE_URL", ""),
        # Roles that must use an authenticator app. Anyone may opt in; disabling for a role needs an env change.
        MFA_REQUIRED_ROLES=os.environ.get("MFA_REQUIRED_ROLES", "super_admin,finance"),
        # Brand: logo self-hosted at static/brand/logo.png when present, else the official website file.
        BRAND_LOGO_URL=os.environ.get("BRAND_LOGO_URL", "https://www.salonshastra.com/images/logo.png"),
        BRAND_FONTS_URL=os.environ.get(
            "BRAND_FONTS_URL",
            "https://fonts.googleapis.com/css2?family=Montserrat:wght@400;500;600;700&family=Open+Sans:wght@400;600&display=swap"),
    )
    if overrides:
        app.config.update(overrides)

    with app.app_context():
        db = open_standalone(app.config["DATABASE_URL"])
        migrate(db)
        db.close()

    logging_setup.configure(os.environ.get("LOG_LEVEL", "INFO"))
    access_log = logging.getLogger("teamos.http")
    app.teardown_appcontext(close_db)

    @app.before_request
    def _before():
        g.request_started = time.monotonic()
        load_current_user()
        check_csrf()
        return enforce_mfa()

    @app.after_request
    def _headers(resp):
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "same-origin"
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data: https://www.salonshastra.com; "
            "style-src 'self' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; "
            "script-src 'self'; frame-ancestors 'none'")
        if g.get("user"):
            resp.headers["Cache-Control"] = "no-store"
        if request.endpoint != "static" and not app.config.get("TESTING"):
            access_log.info("request", extra={
                "method": request.method, "path": request.path, "status": resp.status_code,
                "ms": round((time.monotonic() - g.get("request_started", time.monotonic())) * 1000),
                "user_id": g.user["id"] if g.get("user") else None})
        return resp

    @app.errorhandler(500)
    def _err500(e):
        logging.getLogger("teamos.http").exception("unhandled error", extra={"path": request.path})
        return render_template("error.html", code=500, title="Something went wrong",
                               message="The error has been logged. Please try again."), 500

    @app.errorhandler(PermissionDenied)
    def _perm(e):
        log_denied(e.capability)
        return render_template("error.html", code=403, title="Not permitted",
                               message="Your role does not include this action. It has been logged."), 403

    for code, title in ((400, "Request problem"), (403, "Not permitted"), (404, "Not found")):
        app.register_error_handler(code, _make_error_handler(code, title))

    # Jinja helpers ------------------------------------------------------------
    app.jinja_env.filters["inr"] = format_inr
    app.jinja_env.filters["ist"] = timeutil.fmt_ist
    app.jinja_env.filters["rel"] = timeutil.relative
    app.jinja_env.globals.update(
        can=can, SF=SF, BF=BF, FT=FT, opp_url=_opp_url, ROLE_LABELS=ROLE_LABELS, CAPABILITIES=CAPABILITIES, app_version=__version__,
        now_iso=timeutil.now_iso,
        # A global (not context variable) so imported macros can read it.
        csrf_value=lambda: g.session["csrf_token"] if g.get("session") else "",
    )

    @app.context_processor
    def _ctx():
        local_logo = os.path.join(app.static_folder, "brand", "logo.png")
        ctx = {"brand_logo": ("/static/brand/logo.png" if os.path.exists(local_logo) else app.config["BRAND_LOGO_URL"]),
               "brand_fonts": app.config["BRAND_FONTS_URL"],
               "current_user": g.get("user"), "csrf_token": (g.session["csrf_token"] if g.get("session") else ""),
               "app_env": app.config["APP_ENV"]}
        if g.get("user"):
            ctx["unread_count"] = get_db().scalar(
                "SELECT COUNT(*) FROM notifications WHERE user_id = ? AND read_at IS NULL", (g.user["id"],))
        return ctx

    from .web import admin, auth, buyers, forms, imports, main, public, sellers, tasks
    for bp in (auth.bp, main.bp, sellers.bp, buyers.bp, tasks.bp, admin.bp, imports.bp, forms.bp, public.bp):
        app.register_blueprint(bp)
    return app


def _make_error_handler(code, title):
    def handler(e):
        msg = getattr(e, "description", "") if code == 400 else ""
        return render_template("error.html", code=code, title=title, message=msg), code
    return handler


def _opp_url(kind: str | None, opp_id, **kw) -> str:
    """Link to an opportunity's own screen (sellers and buyers live in separate modules)."""
    from flask import url_for
    if not opp_id:
        return "#"
    return url_for("buyers.detail" if kind == "buyer" else "sellers.detail", opp_id=opp_id, **kw)
