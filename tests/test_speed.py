"""Speed: pages compressed, styles/scripts kept by the browser, the timer never holds a web worker, and opening a
profile does not re-compare it with every record on the other side again and again."""
import gzip

from conftest import user


def test_pages_are_compressed_and_static_files_cached(app, login):
    c = login("admin@t.test")
    r = c.c.get("/sellers/", headers={"Accept-Encoding": "gzip, br"})
    assert r.headers.get("Content-Encoding") == "gzip" and "Accept-Encoding" in r.headers.get("Vary", "")
    assert b"Salon" in gzip.decompress(r.data) and r.headers["Cache-Control"] == "no-store"
    page = gzip.decompress(r.data).decode()
    assert "/static/app.css?v=" in page and "/static/app.js?v=" in page          # fingerprinted addresses
    css = page.split("/static/app.css?v=")[1].split('"')[0]
    s = c.c.get(f"/static/app.css?v={css}", headers={"Accept-Encoding": "gzip"})
    assert s.status_code == 200 and "immutable" in s.headers["Cache-Control"] and "max-age=31536000" in s.headers["Cache-Control"]
    assert s.headers.get("Content-Encoding") == "gzip" and b"--line" in gzip.decompress(s.data)
    # without gzip support the page comes plain
    assert c.c.get("/sellers/").headers.get("Content-Encoding") is None


def test_the_timer_answers_at_once_and_runs_jobs_after(app, db, monkeypatch):
    monkeypatch.setenv("CRON_TOKEN", "x" * 32)
    r = app.test_client().get("/internal/tick?token=" + "x" * 32)
    assert r.status_code == 200 and r.get_json()["jobs_started"] is True
    assert db.scalar("SELECT COUNT(*) FROM job_runs") >= 1


def test_a_profile_is_compared_once_not_on_every_visit(app, db, login):
    from teamos.db import Database
    from teamos.domain import buyers as B
    admin = user(db, "admin@t.test")
    bid = B.create_lead(db, admin, {"full_name": "Ravi", "phone": "9300600001", "city": "Hyderabad",
                                    "source": "meta_lead", "consent_contact": "1",
                                    "owner_id": str(admin["id"])})["opportunity_id"]
    c = login("admin@t.test")
    c.get(f"/buyers/{bid}")
    fp = db.scalar("SELECT fp FROM match_scored WHERE opportunity_id = ?", (bid,))
    assert fp                                                     # compared (and found not ready yet) once
    calls = {"n": 0}
    orig = Database.execute

    def count(self, sql, params=()):
        if "match_scored" in str(sql) and "DELETE" in str(sql):
            calls["n"] += 1
        return orig(self, sql, params)
    Database.execute = count
    try:
        c.get(f"/buyers/{bid}")
        c.get(f"/buyers/{bid}")
    finally:
        Database.execute = orig
    assert calls["n"] == 0                                        # not compared again while nothing changed


def test_link_pages_offer_whatsapp_and_copy(db, login):
    from teamos.domain import buyers as B
    admin = user(db, "admin@t.test")
    bid = B.create_lead(db, admin, {"full_name": "Vikas Test", "phone": "9300600002", "city": "Hyderabad",
                                    "source": "meta_lead", "consent_contact": "1",
                                    "owner_id": str(admin["id"])})["opportunity_id"]
    c = login("admin@t.test")
    page = c.post(f"/forms/send/{bid}", {"template_key": "buyer_deep", "channel": "copy_link"}).data.decode()
    assert 'class="link-box"' in page and "https://wa.me/919300600002?text=" in page and "Open in WhatsApp" in page
    js = c.get("/static/app.js").data.decode()
    assert "Copy link" in js and ".link-box, code.copy, textarea.msg-preview" in js
