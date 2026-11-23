"""Tasks, activities, notifications and the next-action rule (PRD §5.5, §11.1).

A single task model serves personal and team work. Every active/holding opportunity must
always have at least one open task — completing the last one requires a next action.
"""
from __future__ import annotations

from datetime import timedelta

from .. import audit
from ..permissions import user_can
from ..timeutil import now_iso, now_utc, to_iso
from .errors import PermissionDenied, ValidationError

TASK_TYPES = [
    ("first_contact", "First contact"), ("call", "Call"), ("follow_up", "Follow-up"),
    ("collect_info", "Collect information"), ("review", "Review"), ("approval", "Approval"),
    ("visit", "Visit"), ("match_review", "Match review"), ("other", "Other"),
]
PRIORITIES = [("urgent", "Urgent"), ("high", "High"), ("normal", "Normal"), ("low", "Low")]
PRIORITY_RANK = {"urgent": 0, "high": 1, "normal": 2, "low": 3}

CALL_OUTCOMES = [
    ("connected", "Connected"), ("no_answer", "No answer"), ("switched_off", "Switched off"),
    ("busy", "Busy"), ("wrong_number", "Wrong number"), ("callback_requested", "Callback requested"),
    ("not_interested", "Not interested"), ("disqualified", "Disqualified"), ("do_not_contact", "Do not contact"),
]
CALL_OUTCOME_LABELS = dict(CALL_OUTCOMES)


def notify(db, user_id: int | None, kind: str, title: str, link: str | None = None) -> None:
    if user_id:
        db.insert("notifications", {"user_id": user_id, "kind": kind, "title": title, "link": link,
                                    "created_at": now_iso()})


def add_activity(db, actor_id, entity_type, entity_id, kind, body=None, call_outcome=None) -> int:
    return db.insert("activities", {"entity_type": entity_type, "entity_id": entity_id, "kind": kind,
                                    "body": body, "call_outcome": call_outcome, "actor_id": actor_id,
                                    "at": now_iso()})


def _task_event(db, task_id, event, actor_id, detail=None):
    db.insert("task_events", {"task_id": task_id, "event": event, "detail": detail, "actor_id": actor_id,
                              "at": now_iso()})


def refresh_next_action(db, entity_type: str | None, entity_id: int | None) -> None:
    if entity_type != "opportunity" or not entity_id:
        return
    t = db.one("SELECT title, due_at FROM tasks WHERE entity_type = 'opportunity' AND entity_id = ? "
               "AND status = 'open' ORDER BY CASE WHEN due_at IS NULL THEN 1 ELSE 0 END, due_at LIMIT 1",
               (entity_id,))
    db.update("opportunities", entity_id, {
        "next_action": t["title"] if t else None,
        "next_action_due": t["due_at"] if t else None,
        "updated_at": now_iso(),
    })


def create_task(db, actor: dict, *, title: str, assignee_id: int | None, due_at: str | None,
                task_type: str = "follow_up", priority: str = "normal", entity_type: str | None = None,
                entity_id: int | None = None, parent_task_id: int | None = None, is_synthetic: int = 0) -> int:
    title = (title or "").strip()
    errors = {}
    if not title:
        errors["title"] = "Describe the next action."
    if task_type not in dict(TASK_TYPES):
        errors["task_type"] = "Unknown task type."
    if priority not in PRIORITY_RANK:
        errors["priority"] = "Unknown priority."
    if not assignee_id:
        errors["assignee_id"] = "Choose who is responsible."
    if errors:
        raise ValidationError(errors)
    assignee = db.one("SELECT id, is_active, full_name FROM users WHERE id = ?", (assignee_id,))
    if not assignee or not assignee["is_active"]:
        raise ValidationError({"assignee_id": "Assignee is not an active user."})
    now = now_iso()
    tid = db.insert("tasks", {
        "title": title[:200], "task_type": task_type, "priority": priority, "due_at": due_at,
        "assignee_id": assignee_id, "creator_id": actor["id"] if actor else None,
        "entity_type": entity_type, "entity_id": entity_id, "parent_task_id": parent_task_id,
        "is_synthetic": is_synthetic, "created_at": now, "updated_at": now,
    })
    _task_event(db, tid, "created", actor["id"] if actor else None, f"assigned to {assignee['full_name']}")
    if actor and assignee_id != actor["id"]:
        notify(db, assignee_id, "task_assigned", f"{actor['full_name']} assigned you: {title}", f"/tasks/{tid}")
    refresh_next_action(db, entity_type, entity_id)
    return tid


def first_contact_due(minutes: int) -> str:
    return to_iso(now_utc() + timedelta(minutes=minutes))


def open_tasks_for(db, entity_type: str, entity_id: int) -> list[dict]:
    return db.all("SELECT t.*, u.full_name AS assignee_name FROM tasks t LEFT JOIN users u ON u.id = t.assignee_id "
                  "WHERE t.entity_type = ? AND t.entity_id = ? AND t.status = 'open' ORDER BY t.due_at",
                  (entity_type, entity_id))


def _requires_next_action(db, task: dict) -> bool:
    if task["entity_type"] != "opportunity":
        return False
    opp = db.one("SELECT status FROM opportunities WHERE id = ?", (task["entity_id"],))
    if not opp or opp["status"] == "closed":
        return False
    remaining = db.scalar("SELECT COUNT(*) FROM tasks WHERE entity_type = 'opportunity' AND entity_id = ? "
                          "AND status = 'open' AND id <> ?", (task["entity_id"], task["id"]))
    return remaining == 0


def can_act_on_task(db, user: dict, task: dict) -> bool:
    return (task["assignee_id"] == user["id"] or task["creator_id"] == user["id"]
            or user_can(db, user, "crm.assign"))


def complete_task(db, actor: dict, task_id: int, outcome: str, next_title: str | None = None,
                  next_due: str | None = None, next_assignee_id: int | None = None) -> None:
    task = db.one("SELECT * FROM tasks WHERE id = ?", (task_id,))
    if not task:
        raise ValidationError("Task not found.")
    if task["status"] != "open":
        raise ValidationError("Task is already closed.")
    if not can_act_on_task(db, actor, task):
        raise PermissionDenied("task.complete_others")
    outcome = (outcome or "").strip()
    if not outcome:
        raise ValidationError({"outcome": "Record what happened."})
    with db:
        if next_title and next_title.strip():
            create_task(db, actor, title=next_title, assignee_id=next_assignee_id or task["assignee_id"],
                        due_at=next_due, task_type="follow_up", priority=task["priority"],
                        entity_type=task["entity_type"], entity_id=task["entity_id"])
        elif _requires_next_action(db, task):
            raise ValidationError({"next_title": "This record would have no next action. Add one, or move the "
                                                 "opportunity to a holding/closed state first."})
        now = now_iso()
        db.update("tasks", task_id, {"status": "done", "outcome": outcome[:1000], "completed_at": now,
                                     "completed_by": actor["id"], "updated_at": now})
        _task_event(db, task_id, "completed", actor["id"], outcome[:200])
        refresh_next_action(db, task["entity_type"], task["entity_id"])
        _close_parent_if_done(db, actor, task)


def _close_parent_if_done(db, actor, task) -> None:
    """A shared parent (e.g. a match task with buyer- and seller-side subtasks, UAT-35) closes itself when the
    last open subtask is done, so nobody has to remember to tidy it up."""
    pid = task.get("parent_task_id")
    if not pid:
        return
    if db.scalar("SELECT COUNT(*) FROM tasks WHERE parent_task_id = ? AND status = 'open'", (pid,)):
        return
    parent = db.one("SELECT * FROM tasks WHERE id = ? AND status = 'open'", (pid,))
    if not parent:
        return
    now = now_iso()
    db.update("tasks", pid, {"status": "done", "outcome": "All subtasks completed", "completed_at": now,
                             "completed_by": actor["id"], "updated_at": now})
    _task_event(db, pid, "completed", actor["id"], "All subtasks completed")
    refresh_next_action(db, parent["entity_type"], parent["entity_id"])


def reassign_task(db, actor: dict, task_id: int, new_assignee_id: int, reason: str) -> None:
    if not user_can(db, actor, "crm.assign"):
        raise PermissionDenied("crm.assign")
    task = db.one("SELECT * FROM tasks WHERE id = ?", (task_id,))
    if not task or task["status"] != "open":
        raise ValidationError("Only open tasks can be reassigned.")
    if not (reason or "").strip():
        raise ValidationError({"reason": "Give a reason so the history makes sense."})
    new = db.one("SELECT id, full_name, is_active FROM users WHERE id = ?", (new_assignee_id,))
    if not new or not new["is_active"]:
        raise ValidationError({"assignee_id": "Choose an active user."})
    with db:
        db.update("tasks", task_id, {"assignee_id": new_assignee_id, "updated_at": now_iso()})
        _task_event(db, task_id, "reassigned", actor["id"],
                    f"from user #{task['assignee_id']} to {new['full_name']}: {reason.strip()[:200]}")
        notify(db, new_assignee_id, "task_assigned", f"{actor['full_name']} handed you: {task['title']}",
               f"/tasks/{task_id}")
        audit.record(db, actor["id"], "task.reassign", "task", task_id,
                     {"from": task["assignee_id"], "to": new_assignee_id, "reason": reason})


def cancel_open_tasks(db, actor_id, entity_type, entity_id, reason: str) -> None:
    for t in db.all("SELECT id FROM tasks WHERE entity_type = ? AND entity_id = ? AND status = 'open'",
                    (entity_type, entity_id)):
        db.update("tasks", t["id"], {"status": "cancelled", "outcome": reason, "updated_at": now_iso()})
        _task_event(db, t["id"], "cancelled", actor_id, reason)
    refresh_next_action(db, entity_type, entity_id)


def priority_reason(task: dict, now_iso_str: str, day_end: str) -> str:
    """Human-readable reason shown next to each My Day item."""
    parts = []
    if task.get("due_at") and task["due_at"] < now_iso_str:
        parts.append("Overdue")
    elif task.get("due_at") and task["due_at"] < day_end:
        parts.append("Due today")
    if task["task_type"] == "first_contact":
        parts.append("first-contact SLA")
    if task["priority"] in ("urgent", "high"):
        parts.append(task["priority"] + " priority")
    return " · ".join(parts) or "Planned"
