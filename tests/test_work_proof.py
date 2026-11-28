"""Daily work screenshots, the admin's view of the team's day, the 7:30 PM and hourly reminders."""
import io
from datetime import datetime, timedelta

from conftest import user
from PIL import Image

from teamos.domain import work_proof as W
from teamos.timeutil import IST, to_iso


def _png(w=1080, h=2400, color=(200, 230, 255)) -> bytes:
    b = io.BytesIO()
    Image.new("RGB", (w, h), color).save(b, "PNG")
    return b.getvalue()


def test_team_uploads_screenshots_and_admin_sees_them(app, db, login):
    c = login("seller@t.test")
    page = c.get("/work/").data.decode()
    assert "Upload screenshots" in page and "Phone calls" in page and "WhatsApp chats" in page
    r = c.c.post("/work/", data={"csrf_token": c.csrf, "kind": "calls", "note": "38 calls",
                                 "files": [(io.BytesIO(_png()), "calls1.png"), (io.BytesIO(_png()), "calls2.png")]},
                 content_type="multipart/form-data", follow_redirects=True)
    assert "Uploaded 2 screenshots" in r.data.decode()
    rows = db.all("SELECT * FROM work_proofs")
    assert len(rows) == 2 and all(p["content_type"] == "image/jpeg" and p["size_bytes"] < 200_000 for p in rows)
    assert db.scalar("SELECT COUNT(*) FROM document_blobs WHERE stored_name LIKE 'wp-%'") == 2   # stored, encrypted
    admin = user(db, "admin@t.test")
    n = db.one("SELECT * FROM notifications WHERE user_id = ? AND kind = 'work_proof'", (admin["id"],))
    assert n and "Shaheen Seller uploaded 2 screenshots of phone calls" in n["title"] and "/work/team?day=" in n["link"]
    # the admin's day view, and who may open a screenshot
    a = login("admin@t.test")
    team = a.get("/work/team").data.decode()
    assert "Shaheen Seller" in team and "Phone calls: 2" in team and "Harika Buyer" in team and "No screenshots yet" in team
    pid = rows[0]["id"]
    img = a.get(f"/work/img/{pid}")
    assert img.status_code == 200 and img.mimetype == "image/jpeg" and Image.open(io.BytesIO(img.data)).size[1] <= 1600
    assert c.get(f"/work/img/{pid}").status_code == 200                       # their own
    assert login("buyer@t.test").get(f"/work/img/{pid}").status_code == 403   # a colleague
    assert login("buyer@t.test").get("/work/team").status_code == 403
    # a wrong picture can be removed the same day
    c.post(f"/work/{pid}/delete")
    assert db.scalar("SELECT COUNT(*) FROM work_proofs") == 1


def test_bad_files_are_refused(db, login):
    c = login("buyer@t.test")
    r = c.c.post("/work/", data={"csrf_token": c.csrf, "kind": "whatsapp",
                                 "files": [(io.BytesIO(b"%PDF-1.4 not an image"), "x.pdf")]},
                 content_type="multipart/form-data", follow_redirects=True)
    assert "Only photos or screenshots" in r.data.decode() and db.scalar("SELECT COUNT(*) FROM work_proofs") == 0


def _clock(monkeypatch, when):
    from teamos.domain import tasks as T
    monkeypatch.setattr(T, "now_iso", lambda: to_iso(when))
    monkeypatch.setattr(W, "now_iso", lambda: to_iso(when))


def _at(monkeypatch, db, when):
    _clock(monkeypatch, when)
    return W.send_reminders(db, when)


def test_evening_and_hourly_reminders(app, db, monkeypatch):
    shaheen, harika = user(db, "seller@t.test"), user(db, "buyer@t.test")
    monday_11 = datetime(2026, 11, 30, 11, 0, tzinfo=IST)
    s = _at(monkeypatch, db, monday_11)
    assert s["status"] >= 2 and s["evening"] == 0                            # both working: status check
    assert db.scalar("SELECT link FROM notifications WHERE user_id = ? AND kind = 'status_check'",
                     (shaheen["id"],)) == "/work/status"
    assert _at(monkeypatch, db, monday_11 + timedelta(minutes=20))["status"] == 0          # not again within the hour
    db.execute("UPDATE users SET work_status = 'busy', work_status_at = ? WHERE id = ?",
               (to_iso(monday_11 + timedelta(minutes=50)), harika["id"]))
    s = _at(monkeypatch, db, monday_11 + timedelta(minutes=61))
    assert s["status"] == 2                                       # Shaheen and Ravi; Harika updated recently: skipped
    assert db.scalar("SELECT COUNT(*) FROM notifications WHERE user_id = ? AND kind = 'status_check' AND read_at IS NULL",
                     (shaheen["id"],)) == 1                                   # only the latest stays unread
    # 7:30 PM: screenshots — only for whoever has not uploaded, and once
    with app.app_context():
        W.save(db, shaheen, "calls", [(_png(), "a.png")], day="2026-11-30")
    s = _at(monkeypatch, db, datetime(2026, 11, 30, 19, 35, tzinfo=IST))
    assert s["evening"] == 2                                                   # Harika and Ravi (Shaheen uploaded)
    assert db.scalar("SELECT COUNT(*) FROM notifications WHERE kind = 'proof_reminder' AND user_id = ?", (harika["id"],)) == 1
    assert _at(monkeypatch, db, datetime(2026, 11, 30, 19, 50, tzinfo=IST))["evening"] == 0
    assert _at(monkeypatch, db, datetime(2026, 11, 30, 21, 0, tzinfo=IST))["status"] == 0   # off duty at night


def test_old_screenshots_are_removed(app, db):
    with app.app_context():
        W.save(db, user(db, "seller@t.test"), "calls", [(_png(200, 300), "a.png")], day="2026-01-01")
    assert W.cleanup(db) == 1
    assert db.scalar("SELECT COUNT(*) FROM work_proofs") == 0
    assert db.scalar("SELECT COUNT(*) FROM document_blobs WHERE stored_name LIKE 'wp-%'") == 0


def test_alerts_have_their_own_push_choices(db, login):
    from teamos.domain import push as PU
    assert PU.KIND_CATEGORY["status_check"] == "reminders" and PU.KIND_CATEGORY["work_proof"] == "team_work"
    page = login("seller@t.test").get("/me/notifications").data.decode()
    assert "Hourly status check" in page and "Team screenshots uploaded" not in page    # admin-only choice
    assert "Team screenshots uploaded" in login("admin@t.test").get("/me/notifications").data.decode()
