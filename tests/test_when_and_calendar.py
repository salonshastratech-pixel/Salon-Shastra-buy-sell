"""Anything a person schedules needs a date AND a time; appointments (visit / meeting) go on the calendar with an
'Add to Google Calendar' link wherever they are listed."""
from conftest import user

from teamos.domain import sellers as S


def _seller(db, phone="9300440001"):
    admin = user(db, "admin@t.test")
    return S.create_lead(db, admin, {"full_name": "Lakshmi", "phone": phone, "salon_name": "Glow", "city": "Hyderabad",
                                     "locality": "Kondapur", "source": "manual_call", "consent_contact": "1",
                                     "owner_id": str(user(db, "seller@t.test")["id"])})["opportunity_id"]


def _n(db, title):
    return db.scalar("SELECT COUNT(*) FROM tasks WHERE title = ?", (title,))


def test_no_task_or_next_step_without_date_and_time(db, login):
    oid = _seller(db)
    me = user(db, "seller@t.test")["id"]
    c = login("seller@t.test")
    c.post("/tasks/new", {"title": "Give: no time", "assignee_id": str(me), "next": "/"})
    c.post(f"/sellers/{oid}/task", {"title": "Add: no time", "assignee_id": str(me)})
    c.post("/records/bulk", {"kind": "seller", "action": "task", "title": "Bulk: no time", "ids": [str(oid)]})
    c.post(f"/sellers/{oid}/call", {"outcome": "connected", "note": "x", "next_title": "Call: no time"})
    assert sum(_n(db, t) for t in ("Give: no time", "Add: no time", "Bulk: no time", "Call: no time")) == 0
    assert db.scalar("SELECT COUNT(*) FROM activities WHERE kind = 'call'") == 0       # the whole call waits
    tid = db.scalar("SELECT id FROM tasks WHERE entity_id = ? AND status = 'open'", (oid,))
    c.post(f"/tasks/{tid}/complete", {"outcome": "spoke", "next_title": "Complete: no time"})
    assert _n(db, "Complete: no time") == 0 and db.scalar("SELECT status FROM tasks WHERE id = ?", (tid,)) == "open"
    page = c.get(f"/sellers/{oid}").data.decode()
    assert 'name="next_due" data-required-with="next_title"' in page and 'name="due_at" required' in page
    c.post("/tasks/new", {"title": "Give: with time", "assignee_id": str(me), "due_at": "2030-01-05T11:00", "next": "/"})
    c.post(f"/sellers/{oid}/call", {"outcome": "connected", "note": "x", "next_title": "Call: with time",
                                    "next_due": "2030-01-05T16:00"})
    assert _n(db, "Give: with time") == 1 and _n(db, "Call: with time") == 1
    c.post(f"/sellers/{oid}/call", {"outcome": "no_answer"})                           # the retry plans its own time
    assert db.scalar("SELECT COUNT(*) FROM tasks WHERE title LIKE 'Try again%' AND due_at IS NOT NULL") == 1


def test_appointments_go_on_the_calendar_with_a_google_link(db, login):
    oid = _seller(db, "9300440002")
    me = user(db, "seller@t.test")["id"]
    c = login("seller@t.test")
    from datetime import timedelta

    from teamos.timeutil import IST, now_utc
    when = (now_utc().astimezone(IST) + timedelta(days=1)).strftime("%Y-%m-%dT15:00")
    c.post("/tasks/new", {"title": "Meet the owner at the salon", "assignee_id": str(me), "due_at": when,
                          "task_type": "visit", "ref": db.scalar("SELECT ref_code FROM opportunities WHERE id = ?", (oid,)),
                          "next": "/"})
    t = db.one("SELECT * FROM tasks WHERE title = 'Meet the owner at the salon'")
    assert t and t["task_type"] == "visit" and t["entity_id"] == oid
    for page in (c.get("/").data.decode(), c.get(f"/sellers/{oid}").data.decode(), c.get(f"/tasks/{t['id']}").data.decode()):
        assert "calendar.google.com/calendar/render" in page and "📅 Calendar" in page
    cal = c.get("/calendar").data.decode()
    assert "Meet the owner at the salon" in cal and "Add to Google Calendar" in cal
    c.post("/tasks/new", {"title": "Plain task", "assignee_id": str(me), "due_at": when, "next": "/"})
    tid = db.scalar("SELECT id FROM tasks WHERE title = 'Plain task'")
    assert "📅 Calendar" not in c.get(f"/tasks/{tid}").data.decode()                  # only appointments
