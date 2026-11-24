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


class _FakeConn:
    def __init__(self, status=0, broken=False):
        import psycopg
        self.closed = False
        self.broken = broken
        self.info = type("I", (), {"transaction_status": psycopg.pq.TransactionStatus(status)})()
        self.closes = 0

    def execute(self, sql):
        if self.broken:
            raise RuntimeError("server closed the connection")

    def close(self):
        self.closed = True
        self.closes += 1


def test_pg_connections_are_reused_safely(monkeypatch):
    from teamos import db as D
    url = "postgresql://u@db.example/x"
    D._POOL.clear()
    good = D.Database(url, conn=_FakeConn())
    D._give_pg(good)
    assert D._take_pg(url) is good.conn                      # reused, no new handshake
    in_tx = D.Database(url, conn=_FakeConn(status=2))       # INTRANS: never handed to another request
    D._give_pg(in_tx)
    assert in_tx.conn.closed and D._take_pg(url) is None
    stale = D.Database(url, conn=_FakeConn(broken=True))
    D._give_pg(stale)
    D._POOL[url][0] = (stale.conn, 0)                        # idle "for ages" → checked before reuse
    assert D._take_pg(url) is None and stale.conn.closed     # broken one discarded
    for _ in range(D.POOL_MAX + 3):
        D._give_pg(D.Database(url, conn=_FakeConn()))
    assert len(D._POOL[url]) == D.POOL_MAX                   # never more than the limit kept open
    D._POOL.clear()


def test_hostinger_email_uses_ssl_465_and_waits_for_the_password(monkeypatch):
    import smtplib

    from teamos import messaging as MS
    cfg = {"SMTP_HOST": "smtp.hostinger.com", "SMTP_PORT": "465", "SMTP_USER": "support@salonshastra.com",
           "SMTP_FROM": "Salon Shastra <support@salonshastra.com>"}
    monkeypatch.setattr(MS, "_cfg", lambda k, c=None: (c or {}).get(k))
    assert MS.email_provider(cfg).name != "smtp"                      # no password yet → stays in the outbox
    seen = {}

    class FakeSSL:
        def __init__(self, host, port, timeout):
            seen["ssl"] = (host, port)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def login(self, u, p):
            seen["login"] = u

        def send_message(self, m):
            seen["to"] = m["To"]
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSSL)
    p = MS.email_provider({**cfg, "SMTP_PASSWORD": "x"})
    assert p.name == "smtp" and p.send("a@example.com", "Hi", "Body")["status"] == "sent"
    assert seen == {"ssl": ("smtp.hostinger.com", 465), "login": "support@salonshastra.com", "to": "a@example.com"}


def test_one_tap_call_and_whatsapp(app, db, login):
    from test_pilot import _seller
    oid = _seller(db)
    c = login("seller@t.test")
    page = c.get("/sellers/").data.decode()
    assert 'href="tel:+919000011001"' in page and f'/inbox/wa/{oid}' in page
    r = c.post(f"/inbox/wa/{oid}")
    assert r.status_code == 302 and r.headers["Location"].startswith("https://wa.me/919000011001?text=Hello%20Kavya")
    assert db.scalar("SELECT COUNT(*) FROM activities WHERE entity_id = ? AND body LIKE 'WhatsApp opened%'", (oid,)) == 1
    db.execute("UPDATE contacts SET do_not_contact = 1")
    assert "wa.me" not in c.post(f"/inbox/wa/{oid}").headers["Location"]



def test_database_move_copies_everything_and_counts_match(app, db, login, tmp_path, monkeypatch):
    from test_pilot import _seller

    from teamos.db import open_standalone
    from teamos.dbmove import copy, counts
    _seller(db)
    target_url = f"sqlite:///{tmp_path}/new.db"
    target = open_standalone(target_url)
    res = copy(db, target)
    assert not res["mismatch"] and res["rows"] > 10
    assert counts(target) == counts(db)
    assert target.scalar("SELECT salon_name FROM (SELECT b.name AS salon_name FROM salon_businesses b)") == "Glow Studio"
    again = copy(db, target)                                  # pressing Copy twice replaces, never duplicates
    assert counts(target) == counts(db) and not again["mismatch"]
    target.close()
    page = login("admin@t.test").get("/admin/database-move").data.decode()
    assert "not set" in page and "Copy now" in page
    monkeypatch.setenv("MOVE_TARGET_URL", target_url)
    login("admin@t.test").post("/admin/database-move")
    pg = login("admin@t.test").get("/admin/database-move").data.decode()
    assert "every table matches" in pg
    assert login("seller@t.test").get("/admin/database-move").status_code in (302, 403)


def test_database_password_with_special_characters_is_encoded():
    from teamos.db import safe_pg_url
    host = "@aws-1-ap-southeast-1.pooler.supabase.com:5432/postgres"
    assert safe_pg_url("postgresql://postgres.abc:pa@ss#1/x?" + host) == "postgresql://postgres.abc:pa%40ss%231%2Fx%3F" + host
    assert safe_pg_url("postgresql://postgres.abc:Plain123" + host) == "postgresql://postgres.abc:Plain123" + host
    assert safe_pg_url("postgresql://u:pa%40ss@h/db") == "postgresql://u:pa%40ss@h/db"       # already encoded: same
    assert safe_pg_url("sqlite:///x.db") == "sqlite:///x.db"


def test_database_move_bad_address_shows_failed_not_stuck(app, db, login, monkeypatch):
    monkeypatch.setenv("MOVE_TARGET_URL", "postgresql://u:p@no-such-host.invalid:5432/x")
    a = login("admin@t.test")
    a.post("/admin/database-move")
    page = a.get("/admin/database-move").data.decode()
    assert "The copy stopped" in page and "Copy now" in page


def test_tick_token_with_plus_sign_pasted_unencoded(app, db, monkeypatch):
    monkeypatch.setenv("CRON_TOKEN", "abc+def/ghi=jkl+mnopqrstu=")
    c = app.test_client()
    assert c.get("/internal/tick?token=abc+def/ghi=jkl+mnopqrstu=").status_code == 200   # '+' arrives as a space
    assert c.get("/internal/tick?token=abc%2Bdef/ghi=jkl%2Bmnopqrstu=").status_code == 200
    assert c.get("/internal/tick?token=abc def").status_code == 404
