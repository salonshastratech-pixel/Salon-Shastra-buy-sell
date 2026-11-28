"""Admin review: adding people safely, renaming, temporary passwords, disabling someone with work, Team page size."""
from conftest import user


def test_admin_and_finance_accounts_get_the_same_checks(db, login):
    c = login("admin@t.test")
    for role in ("super_admin", "finance", "team_member"):
        c.post("/admin/users", {"full_name": "New Person", "new_member_email": "new@t.test", "role": role,
                                "password": "short"})
        assert db.scalar("SELECT COUNT(*) FROM users WHERE email = 'new@t.test'") == 0, role
        r = c.post("/admin/users", {"full_name": "Copy", "new_member_email": "seller@t.test", "role": role,
                                    "password": "Strong2026Pass"})
        assert r.status_code == 302, role                               # explained, never a crash
    assert db.scalar("SELECT COUNT(*) FROM users WHERE email = 'seller@t.test'") == 1


def test_rename_a_person(db, login):
    c = login("admin@t.test")
    uid = user(db, "seller@t.test")["id"]
    c.post(f"/admin/users/{uid}/name", {"full_name": "  Vikas   Rachakonda "})
    assert db.scalar("SELECT full_name FROM users WHERE id = ?", (uid,)) == "Vikas Rachakonda"
    assert login("seller@t.test").get(f"/admin/users").status_code in (302, 403)        # team member cannot
    assert login("seller@t.test").post(f"/admin/users/{uid}/name", {"full_name": "X"}).status_code == 403


def test_temporary_password_must_be_replaced_before_anything_else(db, login):
    db.execute("UPDATE users SET must_change_password = 1 WHERE email = 'seller@t.test'")
    c = login("seller@t.test")
    r = c.get("/sellers/")
    assert r.status_code == 302 and "/me/password" in r.headers["Location"]
    assert c.get("/me/password").status_code == 200
    assert c.post("/sellers/new", {"full_name": "x"}).status_code == 403


def test_disabling_someone_with_work_says_what_to_hand_over(db, login):
    from teamos.domain import sellers as S
    admin = user(db, "admin@t.test")
    s = user(db, "seller@t.test")
    S.create_lead(db, admin, {"full_name": "Lakshmi", "phone": "9300880001", "salon_name": "Glow", "city": "Hyderabad",
                              "locality": "Kondapur", "source": "manual_call", "consent_contact": "1",
                              "owner_id": str(s["id"])})
    c = login("admin@t.test")
    r = c.post(f"/admin/users/{s['id']}/active", {}, follow_redirects=True)
    page = r.data.decode()
    assert "still hold 1 open lead(s)" in page and "Hand over" in page
