"""Milestone 7.3 — people change their own password; temporary passwords must be replaced."""
from conftest import PW, user


def test_temporary_password_leads_to_change_page_and_banner(app, db, login):
    db.execute("UPDATE users SET must_change_password = 1 WHERE email = 'seller@t.test'")
    c = app.test_client()
    r = c.post("/login", data={"email": "seller@t.test", "password": PW})
    assert r.status_code == 302 and "/me/password" in r.headers["Location"]
    r = c.get("/")                                   # nothing else opens until it is replaced
    assert r.status_code == 302 and "/me/password" in r.headers["Location"]


def test_change_password_rules_and_other_devices_signed_out(app, db, login):
    c = login("seller@t.test")
    other = login("seller@t.test")  # second device
    uid = user(db, "seller@t.test")["id"]
    assert db.scalar("SELECT COUNT(*) FROM sessions WHERE user_id = ?", (uid,)) == 2
    r = c.post("/me/password", {"current": "wrong", "new": "NewPassword123", "again": "NewPassword123"})
    assert r.status_code == 400 and b"current password is not correct" in r.data
    r = c.post("/me/password", {"current": PW, "new": "NewPassword123", "again": "Different123"})
    assert b"different" in r.data
    r = c.post("/me/password", {"current": PW, "new": "short1A", "again": "short1A"})
    assert b"at least 10" in r.data
    r = c.post("/me/password", {"current": PW, "new": "NewPassword123", "again": "NewPassword123"})
    assert r.status_code == 302
    u = user(db, "seller@t.test")
    assert u["must_change_password"] == 0 and u["password_changed_at"]
    assert db.scalar("SELECT COUNT(*) FROM sessions WHERE user_id = ?", (uid,)) == 1   # other device signed out
    assert other.get("/").status_code == 302
    assert app.test_client().post("/login", data={"email": "seller@t.test", "password": "NewPassword123"}).status_code == 302
    assert db.scalar("SELECT COUNT(*) FROM audit_events WHERE action = 'auth.password_change' AND outcome = 'success'") \
        or db.scalar("SELECT COUNT(*) FROM audit_events WHERE action = 'auth.password_change'") >= 1
    # needs to be signed in
    assert app.test_client().get("/me/password").status_code == 302


def test_admin_resets_a_forgotten_password_once(app, db, login):
    import re
    uid = user(db, "seller@t.test")["id"]
    other = login("seller@t.test")
    r = login("admin@t.test").post(f"/admin/users/{uid}/reset-password", follow_redirects=True)
    temp = re.search(r"Temporary password for [^:]+: (\S+) ", r.data.decode()).group(1)
    assert user(db, "seller@t.test")["must_change_password"] == 1
    assert other.get("/").status_code == 302                          # old sessions ended
    c = app.test_client()
    assert "/me/password" in c.post("/login", data={"email": "seller@t.test", "password": temp}).headers["Location"]
    assert temp not in " ".join(r["detail"] or "" for r in db.all("SELECT detail FROM audit_events"))
    assert login("lead@t.test").post(f"/admin/users/{uid}/reset-password").status_code == 403
    assert b"Forgot your password?" in app.test_client().get("/login").data
