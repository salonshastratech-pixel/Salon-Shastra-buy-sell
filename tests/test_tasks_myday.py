"""Tasks, My Day and Team Command Center (PRD §5.5, §11)."""
from conftest import SELLER_FORM, user

from teamos.timeutil import now_utc, to_iso
from datetime import timedelta


def test_uat34_my_day_groups_overdue_today_and_shows_reasons(login, db):
    c = login("seller@t.test")
    c.post("/sellers/new", SELLER_FORM)
    tid = db.scalar("SELECT id FROM tasks")
    db.execute("UPDATE tasks SET due_at = ? WHERE id = ?", (to_iso(now_utc() - timedelta(hours=2)), tid))
    page = c.get("/").data.decode()
    assert "Overdue" in page and "first-contact SLA" in page and "First call: Test Owner" in page


def test_records_without_next_action_surface_as_exceptions(login, db):
    c = login("seller@t.test")
    c.post("/sellers/new", SELLER_FORM)
    db.execute("UPDATE tasks SET status = 'cancelled'")
    assert b"No next step yet" in c.get("/").data
    assert b"Exceptions: no next action" in c.get("/team").data


def test_uat46_team_lead_transfers_task_history_retained(login, db):
    login("seller@t.test").post("/sellers/new", SELLER_FORM)
    tid = db.scalar("SELECT id FROM tasks")
    buyer_id = user(db, "buyer@t.test")["id"]
    login("seller@t.test").post(f"/tasks/{tid}/reassign", {"assignee_id": buyer_id, "reason": "Shaheen on leave"})
    assert db.scalar("SELECT assignee_id FROM tasks WHERE id = ?", (tid,)) == buyer_id
    events = [e["event"] for e in db.all("SELECT event FROM task_events WHERE task_id = ? ORDER BY id", (tid,))]
    assert events == ["created", "reassigned"]
    assert db.scalar("SELECT COUNT(*) FROM notifications WHERE user_id = ?", (buyer_id,)) == 1


def test_cross_team_task_request_notifies_colleague(login, db):
    c = login("seller@t.test")
    c.post("/sellers/new", SELLER_FORM)
    opp_id = db.scalar("SELECT id FROM opportunities")
    buyer_id = user(db, "buyer@t.test")["id"]
    c.post(f"/sellers/{opp_id}/task", {"title": "Check buyer appetite for Kondapur", "assignee_id": buyer_id,
                                       "due_at": "2030-01-01T10:00", "task_type": "collect_info"})
    assert b"Check buyer appetite" in login("buyer@t.test").get("/").data


def test_finance_cannot_complete_team_tasks(login, db):
    login("seller@t.test").post("/sellers/new", SELLER_FORM)
    tid = db.scalar("SELECT id FROM tasks")
    r = login("fin@t.test").post(f"/tasks/{tid}/complete", {"outcome": "done", "next_title": "x"})
    assert r.status_code == 403
    assert db.scalar("SELECT status FROM tasks") == "open"


def test_completing_with_next_action_keeps_record_owned(login, db):
    c = login("seller@t.test")
    c.post("/sellers/new", SELLER_FORM)
    tid = db.scalar("SELECT id FROM tasks")
    c.post(f"/tasks/{tid}/complete", {"outcome": "Spoke briefly", "next_title": "Send detailed questions",
                                      "next_due": "2030-01-02T11:00"})
    assert db.scalar("SELECT status FROM tasks WHERE id = ?", (tid,)) == "done"
    assert db.scalar("SELECT next_action FROM opportunities") == "Send detailed questions"


def test_mentions_notify_colleague(login, db):
    c = login("seller@t.test")
    c.post("/sellers/new", SELLER_FORM)
    opp_id = db.scalar("SELECT id FROM opportunities")
    c.post(f"/sellers/{opp_id}/note", {"body": "@harika can you check budget fit?"})
    assert db.scalar("SELECT COUNT(*) FROM notifications WHERE kind = 'mention' AND user_id = ?",
                     (user(db, "buyer@t.test")["id"],)) == 1


def test_completing_last_task_without_next_action_is_refused(login, db):
    c = login("seller@t.test")
    c.post("/sellers/new", SELLER_FORM)
    tid = db.scalar("SELECT id FROM tasks")
    c.post(f"/tasks/{tid}/complete", {"outcome": "Called"})
    assert db.scalar("SELECT status FROM tasks WHERE id = ?", (tid,)) == "open"
    assert db.scalar("SELECT next_action FROM opportunities") is not None
