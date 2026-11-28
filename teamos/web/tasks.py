from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from ..db import get_db
from ..domain import tasks as T
from ..domain.errors import ValidationError
from ..security import can, deny, login_required, safe_next
from ..timeutil import ist_day_bounds, now_iso, parse_local_input
from .main import TASK_SELECT, _sort

bp = Blueprint("tasks", __name__, url_prefix="/tasks")


@bp.route("/")
@login_required
def index():
    db = get_db()
    view = request.args.get("view", "mine")
    now = now_iso()
    _, day_end = ist_day_bounds()
    who = request.args.get("who") or ""
    if who.isdigit():  # one person's open tasks (from the Team page)
        view = "all"
        rows = db.all(TASK_SELECT + "WHERE t.status = 'open' AND t.assignee_id = ?", (int(who),))
    elif view == "all":
        rows = db.all(TASK_SELECT + "WHERE t.status = 'open'")
    elif view == "done":
        rows = db.all(TASK_SELECT + "WHERE t.status <> 'open' AND (t.assignee_id = ? OR t.completed_by = ?) "
                      "ORDER BY t.updated_at DESC LIMIT 100", (g.user["id"], g.user["id"]))
    elif view == "created":
        rows = db.all(TASK_SELECT + "WHERE t.status = 'open' AND t.creator_id = ? AND t.assignee_id <> ?"
                      + (" AND t.task_type = 'other'" if request.args.get("given") else ""),   # given with the box
                      (g.user["id"], g.user["id"]))
    else:
        rows = db.all(TASK_SELECT + "WHERE t.status = 'open' AND t.assignee_id = ?", (g.user["id"],))
    if not can("crm.view"):
        # Same rule as the task detail page: tasks linked to any CRM record (seller, buyer, match, deal)
        # need crm.view unless assigned to you.
        rows = [t for t in rows if not t["entity_type"] or t["assignee_id"] == g.user["id"]]
    if view != "done":
        rows = _sort(rows)
    for t in rows:
        t["why"] = T.priority_reason(t, now, day_end)
    users = db.all("SELECT id, full_name FROM users WHERE is_active = 1 AND role <> 'finance' ORDER BY full_name")
    return render_template("tasks/index.html", rows=rows, view=view, users=users, TASK_TYPES=T.TASK_TYPES,
                           PRIORITIES=T.PRIORITIES, now=now)


@bp.route("/new", methods=["POST"])
@login_required
def create():
    db = get_db()
    f = request.form
    from ..security import safe_next
    back = safe_next(f.get("next"), "")
    try:
        ref = "".join((f.get("ref") or "").split()).upper()
        entity = None
        if ref:
            if ref[:2] in ("SL", "BY") and ref[2:3] != "-" and ref[2:].isdigit():
                ref = f"{ref[:2]}-{int(ref[2:]):05d}"                  # "sl123" -> SL-00123
            entity = db.one("SELECT id FROM opportunities WHERE ref_code = ?", (ref,))
            if not entity:
                raise ValidationError({"ref": f"No lead {ref} — check the number (e.g. SL-00123 or BY-00045)."})
        with db:
            tid = T.create_task(db, g.user, title=f.get("title"), assignee_id=int(f.get("assignee_id") or 0),
                                due_at=parse_local_input(f.get("due_at")), task_type=f.get("task_type") or "other",
                                priority=f.get("priority") or "normal",
                                entity_type="opportunity" if entity else None, entity_id=entity["id"] if entity else None)
        who = db.scalar("SELECT full_name FROM users WHERE id = ?", (int(f.get("assignee_id") or 0),))
        flash("Task created." if int(f.get("assignee_id") or 0) == g.user["id"] else
              f"Task given to {who}. They have been told.", "ok")
        return redirect(back or url_for("tasks.detail", task_id=tid))
    except (ValidationError, ValueError) as e:
        flash(" ".join(e.errors.values()) if isinstance(e, ValidationError) else str(e), "error")
        return redirect(back or url_for("tasks.index"))


@bp.route("/<int:task_id>")
@login_required
def detail(task_id):
    db = get_db()
    task = db.one(TASK_SELECT + "WHERE t.id = ?", (task_id,))
    if not task:   # e.g. an old alert pointing at a task removed with its lead
        from flask import flash
        flash("That task no longer exists — it was removed, probably with its lead.", "warn")
        return redirect(url_for("tasks.index"))
    if task["entity_type"] and not can("crm.view") and task["assignee_id"] != g.user["id"]:
        deny("crm.view", "task", task_id)
    events = db.all("SELECT e.*, u.full_name AS actor_name FROM task_events e LEFT JOIN users u ON u.id = e.actor_id "
                    "WHERE task_id = ? ORDER BY e.at, e.id", (task_id,))
    users = db.all("SELECT id, full_name FROM users WHERE is_active = 1 AND role <> 'finance' ORDER BY full_name")
    return render_template("tasks/detail.html", task=task, events=events, users=users,
                           can_act=T.can_act_on_task(db, g.user, task))


@bp.route("/<int:task_id>/complete", methods=["POST"])
@login_required
def complete(task_id):
    db = get_db()
    f = request.form
    try:
        T.complete_task(db, g.user, task_id, f.get("outcome"), f.get("next_title"),
                        parse_local_input(f.get("next_due")), int(f["next_assignee_id"]) if f.get("next_assignee_id") else None)
        flash("Done. Nice work.", "ok")
    except ValidationError as e:
        flash(" ".join(e.errors.values()), "error")
        return redirect(url_for("tasks.detail", task_id=task_id))
    nxt = f.get("return_to") or ""
    return redirect(safe_next(nxt, url_for("main.my_day")))


@bp.route("/<int:task_id>/reassign", methods=["POST"])
@login_required
def reassign(task_id):
    db = get_db()
    try:
        T.reassign_task(db, g.user, task_id, int(request.form.get("assignee_id") or 0), request.form.get("reason"))
        flash("Task handed over.", "ok")
    except ValidationError as e:
        flash(" ".join(e.errors.values()), "error")
    return redirect(url_for("tasks.detail", task_id=task_id))
