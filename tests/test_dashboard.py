"""Today's CRM numbers on My Day and the Founder desk: new leads (and when they came in), calls to make, calls made,
productive calls, follow-ups due, missed, and leads waiting for their first call."""
from datetime import timedelta

from conftest import user

from teamos.domain import dashboard as DB
from teamos.domain import sellers as S
from teamos.timeutil import now_utc, to_iso


def _lead(db, phone, owner="seller@t.test", hours_ago=0):
    admin = user(db, "admin@t.test")
    oid = S.create_lead(db, admin, {"full_name": f"Owner {phone}", "phone": phone, "salon_name": f"Salon {phone}",
                                    "city": "Hyderabad", "locality": "Kondapur", "source": "meta_lead",
                                    "consent_contact": "1", "owner_id": str(user(db, owner)["id"])})["opportunity_id"]
    if hours_ago:
        db.execute("UPDATE opportunities SET lead_created_at = ? WHERE id = ?",
                   (to_iso(now_utc() - timedelta(hours=hours_ago)), oid))
    return oid


def test_numbers_count_each_bucket_for_a_person(db, login):
    me = user(db, "seller@t.test")
    a = _lead(db, "9000012001")
    b = _lead(db, "9000012002")
    _lead(db, "9000012003", hours_ago=60)                     # came in days ago, still not called
    n = DB.numbers(db, [me["id"]])
    assert n["new_leads"] == 2 and n["waiting"] == 3 and n["calls_made"] == 0 and n["productive_pct"] == 0
    assert n["calls_to_do"] >= 2                               # each new lead gets a first-call task due today
    c = login("seller@t.test")
    due = (now_utc() + timedelta(days=2)).strftime("%Y-%m-%dT10:00")
    c.post(f"/sellers/{a}/call", {"outcome": "connected", "next_title": "Follow up", "next_due": due})
    c.post(f"/sellers/{b}/call", {"outcome": "no_answer", "next_title": "Follow up", "next_due": due})
    db.execute("UPDATE tasks SET due_at = ? WHERE title = 'Follow up'",
               (to_iso(now_utc() - timedelta(minutes=30)),))         # both next steps have slipped
    assert db.scalar("SELECT task_type FROM tasks WHERE title = 'Follow up' AND entity_id = ?", (a,)) == "follow_up"
    assert db.scalar("SELECT task_type FROM tasks WHERE title = 'Follow up' AND entity_id = ?", (b,)) == "call"
    n = DB.numbers(db, [me["id"]])
    assert n["calls_made"] == 2 and n["productive"] == 1 and n["productive_pct"] == 50
    assert n["waiting"] == 1 and n["followups_due"] == 1 and n["missed"] >= 2
    leads = DB.new_leads(db, [me["id"]])
    assert leads[0]["lead_at"] >= leads[-1]["lead_at"]
    old = next(r for r in leads if r["ref_code"] and r["first_contact_at"] is None)
    assert "Not contacted yet" in old["speed"]["label"] and old["speed"]["tone"] == "bad"
    called = next(r for r in leads if r["id"] == a)
    assert called["speed"]["label"].startswith("First contact after")
    assert any(f["call_outcome"] == "connected" for f in DB.feed(db, [me["id"]]))


def test_my_day_and_founder_desk_show_the_numbers(db, login):
    _lead(db, "9000012011")
    page = login("seller@t.test").get("/").data.decode()
    for label in ("New leads today", "Calls to make", "Calls made", "Productive", "Follow-ups due", "Missed",
                  "Waiting for first call", "New leads", "Came in", "My latest activity"):
        assert label in page, label
    assert "Whole team today" not in page                       # team-wide table: founder and team lead only
    admin = login("admin@t.test")
    page = admin.get("/").data.decode()
    assert "Whole team today" in page and "Team today" in page and "Salon 9000012011" in page
    desk = admin.get("/desk").data.decode()
    assert "Team today" in desk and "Waiting 1st call" in desk and "Came in" in desk


def test_task_lists_show_when_the_lead_came_in(db, login):
    _lead(db, "9000012021", hours_ago=5)
    page = login("seller@t.test").get("/tasks/").data.decode()
    assert "lead 5 hours ago" in page or "lead 5 h ago" in page or "lead 4 hours ago" in page
    assert "not contacted" in page


def test_number_fields_open_the_number_keypad_and_errors_use_field_names(db, login):
    c = login("seller@t.test")
    assert c.get("/sellers/new").status_code == 200
    oid = _lead(db, "9000012031")
    page = c.get(f"/sellers/{oid}?tab=intake").data.decode()
    assert 'inputmode="numeric"' in page
