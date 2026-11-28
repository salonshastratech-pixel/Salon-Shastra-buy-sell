"""My Day by side (salon owners first for Shaheen, buyers first for Harika), passing a lead to a colleague from the
dashboard, and giving anyone a task - admin to team, team to team, team to admin."""
import re

from conftest import user

from teamos.domain import buyers as B
from teamos.domain import sellers as S


def _seller(db, owner, phone="9300660001", name="Glow"):
    admin = user(db, "admin@t.test")
    return S.create_lead(db, admin, {"full_name": "Lakshmi", "phone": phone, "salon_name": name, "city": "Hyderabad",
                                     "locality": "Kondapur", "source": "manual_call", "consent_contact": "1",
                                     "owner_id": str(user(db, owner)["id"])})["opportunity_id"]


def _buyer(db, owner, phone="9300660002"):
    admin = user(db, "admin@t.test")
    return B.create_lead(db, admin, {"full_name": "Ravi Investor", "phone": phone, "city": "Hyderabad",
                                     "source": "meta_lead", "consent_contact": "1",
                                     "owner_id": str(user(db, owner)["id"])})["opportunity_id"]


def _overdue(db, oid):
    db.execute("UPDATE tasks SET due_at = '2020-01-01T05:00:00Z' WHERE entity_id = ? AND status = 'open'", (oid,))


def test_own_side_first_other_side_folded_but_there(db, login):
    s, b = _seller(db, "seller@t.test"), _buyer(db, "seller@t.test")
    _overdue(db, s)
    _overdue(db, b)
    page = login("seller@t.test").get("/").data.decode()
    assert "your priority: <strong>Salon owners</strong>" in page
    card = page.split('id="overdue"', 1)[1].split('id="today"', 1)[0]
    main, folded = card.split('class="other-side"', 1)
    assert "Glow" in main and "Ravi Investor" not in main
    assert "Buyers &amp; investors — also yours (1)" in folded and "Ravi Investor" in folded
    page = login("buyer@t.test").get("/").data.decode()
    assert "your priority: <strong>Buyers &amp; investors</strong>" in page


def test_pass_a_lead_from_my_day(db, login):
    b = _buyer(db, "seller@t.test")
    harika = user(db, "buyer@t.test")
    c = login("seller@t.test")
    page = c.get("/").data.decode()
    assert f'action="/records/{b}/pass"' in page
    pick = re.search(rf'action="/records/{b}/pass".*?<select name="to_id">(.*?)</select>', page, re.S).group(1)
    assert re.search(rf'<option value="{harika["id"]}" selected', pick)      # the buyers person is suggested
    r = c.post(f"/records/{b}/pass", {"to_id": str(harika["id"]), "note": "investor, wants Kukatpally", "next": "/"})
    assert r.status_code == 302 and r.headers["Location"].endswith("/")
    assert db.scalar("SELECT primary_owner_id FROM opportunities WHERE id = ?", (b,)) == harika["id"]
    assert {t["assignee_id"] for t in db.all("SELECT assignee_id FROM tasks WHERE entity_id = ? AND status = 'open'",
                                             (b,))} == {harika["id"]}
    assert db.scalar("SELECT COUNT(*) FROM notifications WHERE user_id = ? AND title LIKE 'You now own%'",
                     (harika["id"],)) == 1
    hp = login("buyer@t.test").get("/").data.decode()
    assert "Passed to you" in hp and "Shaheen Seller" in hp and "investor, wants Kukatpally" in hp
    r = login("fin@t.test").post(f"/records/{b}/pass", {"to_id": str(user(db, 'seller@t.test')['id'])})
    assert r.status_code in (302, 403) and db.scalar("SELECT primary_owner_id FROM opportunities WHERE id = ?",
                                                     (b,)) == harika["id"]


def test_admin_and_team_give_each_other_tasks(db, login):
    s = _seller(db, "seller@t.test")
    ref = db.scalar("SELECT ref_code FROM opportunities WHERE id = ?", (s,))
    shaheen, admin = user(db, "seller@t.test"), user(db, "admin@t.test")
    a = login("admin@t.test")
    assert "Give a task" in a.get("/team").data.decode()
    r = a.post("/tasks/new", {"title": "Visit the salon with photos", "assignee_id": str(shaheen["id"]),
                              "due_at": "2030-01-05T11:00", "ref": ref.lower().replace("-", "").replace("000", ""),
                              "next": "/team#give-task"})
    assert r.status_code == 302 and r.headers["Location"].endswith("/team#give-task")
    t = db.one("SELECT * FROM tasks WHERE title = 'Visit the salon with photos'")
    assert t["assignee_id"] == shaheen["id"] and t["entity_id"] == s and t["creator_id"] == admin["id"]
    assert db.scalar("SELECT COUNT(*) FROM notifications WHERE user_id = ? AND title LIKE '%assigned you: Visit%'",
                     (shaheen["id"],)) == 1
    n = db.scalar("SELECT COUNT(*) FROM tasks")
    a.post("/tasks/new", {"title": "x", "assignee_id": str(shaheen["id"]), "ref": "SL-99999", "next": "/"})
    assert db.scalar("SELECT COUNT(*) FROM tasks") == n                       # wrong lead number: nothing made
    c = login("seller@t.test")
    assert "Give a task" in c.get("/").data.decode()
    c.post("/tasks/new", {"title": "Approve the Glow teaser", "assignee_id": str(admin["id"]), "due_at": "2030-01-05T11:00", "next": "/"})
    c.post("/tasks/new", {"title": "Call Ravi back", "assignee_id": str(user(db, "buyer@t.test")["id"]), "due_at": "2030-01-05T12:00", "next": "/"})
    assert db.scalar("SELECT assignee_id FROM tasks WHERE title = 'Approve the Glow teaser'") == admin["id"]
    assert db.scalar("SELECT assignee_id FROM tasks WHERE title = 'Call Ravi back'") == user(db, "buyer@t.test")["id"]
    assert "2 you gave, still open" in c.get("/").data.decode()
