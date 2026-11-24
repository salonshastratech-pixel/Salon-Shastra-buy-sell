"""Free-hosting support: /internal/tick for an outside scheduler, first-start admin, proxy-aware addresses."""
import os

from teamos import create_app
from teamos.db import open_standalone


def test_tick_needs_the_secret(app, db, monkeypatch):
    c = app.test_client()
    monkeypatch.delenv("CRON_TOKEN", raising=False)
    assert c.get("/internal/tick?token=anything").status_code == 404          # no token configured → nothing
    monkeypatch.setenv("CRON_TOKEN", "x" * 32)
    assert c.get("/internal/tick?token=wrong").status_code == 404
    r = c.get("/internal/tick?token=" + "x" * 32)
    assert r.status_code == 200 and r.get_json()["ok"] is True
    assert db.scalar("SELECT COUNT(*) FROM jobs WHERE kind = 'sla_sweep'") >= 1     # periodic jobs scheduled
    assert db.scalar("SELECT COUNT(*) FROM job_runs") >= 1                          # and run
    assert c.post("/internal/tick", headers={"X-Cron-Token": "x" * 32}).status_code == 200  # no CSRF needed


def test_bootstrap_admin_only_on_an_empty_database(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path}/boot.db"
    monkeypatch.setenv("BOOTSTRAP_ADMIN_EMAIL", "Founder@Example.com")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_PASSWORD", "Temp-Password-123")
    create_app({"DATABASE_URL": url, "TESTING": True, "SECRET_KEY": "t"})
    db = open_standalone(url)
    u = db.one("SELECT * FROM users WHERE email = 'founder@example.com'")
    assert u["role"] == "super_admin" and u["must_change_password"] == 1
    monkeypatch.setenv("BOOTSTRAP_ADMIN_EMAIL", "someone.else@example.com")
    create_app({"DATABASE_URL": url, "TESTING": True, "SECRET_KEY": "t"})
    assert db.scalar("SELECT COUNT(*) FROM users") == 1                              # never a second time
    db.close()


def test_proxy_fix_uses_the_real_visitor_address(tmp_path, monkeypatch):
    monkeypatch.setenv("TRUST_PROXY", "1")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/p.db")
    import importlib

    import wsgi
    importlib.reload(wsgi)
    seen = {}

    @wsgi.app.route("/_ip")
    def _ip():
        from flask import request
        seen["ip"], seen["scheme"] = request.remote_addr, request.scheme
        return "ok"
    wsgi.app.test_client().get("/_ip", headers={"X-Forwarded-For": "203.0.113.9", "X-Forwarded-Proto": "https"})
    assert seen == {"ip": "203.0.113.9", "scheme": "https"}
    os.environ.pop("TRUST_PROXY", None)
