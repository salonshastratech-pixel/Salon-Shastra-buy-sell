"""My Day, Team Command Center, search, notifications, field dictionary."""
from flask import Blueprint, g, redirect, render_template, request, url_for

from ..db import get_db
from ..domain import tasks as T
from ..domain.buyers import STAGE_LABELS as BUYER_STAGE_LABELS
from ..domain.sellers import STAGE_LABELS as SELLER_STAGE_LABELS
from ..security import login_required, require
from datetime import timedelta

from ..timeutil import ist_day_bounds, now_iso, now_utc, to_iso

bp = Blueprint("main", __name__)

# Shared screens list sellers and buyers together; stage codes like 'new' mean the same in both funnels.
STAGE_LABELS = {**SELLER_STAGE_LABELS, **BUYER_STAGE_LABELS}

TASK_SELECT = (
    "SELECT t.*, u.full_name AS assignee_name, o.ref_code, o.id AS opp_id, o.kind AS opp_kind, "
    "c.full_name AS contact_name, b.name AS salon_name, o.stage AS opp_stage "
    "FROM tasks t LEFT JOIN users u ON u.id = t.assignee_id "
    "LEFT JOIN opportunities o ON t.entity_type = 'opportunity' AND o.id = t.entity_id "
    "LEFT JOIN contacts c ON c.id = o.contact_id LEFT JOIN salon_businesses b ON b.id = o.business_id ")


def _sort(tasks):
    return sorted(tasks, key=lambda t: (t["due_at"] is None, T.PRIORITY_RANK.get(t["priority"], 9), t["due_at"] or ""))


@bp.route("/")
@login_required
def my_day():
    db = get_db()
    now = now_iso()
    day_start, day_end = ist_day_bounds()
    mine = db.all(TASK_SELECT + "WHERE t.status = 'open' AND t.assignee_id = ?", (g.user["id"],))
    for t in mine:
        t["why"] = T.priority_reason(t, now, day_end)
    overdue = _sort([t for t in mine if t["due_at"] and t["due_at"] < now])
    today = _sort([t for t in mine if t["due_at"] and now <= t["due_at"] < day_end])
    upcoming = _sort([t for t in mine if not t["due_at"] or t["due_at"] >= day_end])[:15]
    done_today = db.all(TASK_SELECT + "WHERE t.status = 'done' AND t.completed_by = ? AND t.completed_at >= ? "
                        "ORDER BY t.completed_at DESC", (g.user["id"], day_start))
    calls_today = db.scalar("SELECT COUNT(*) FROM activities WHERE actor_id = ? AND kind = 'call' AND at >= ?",
                            (g.user["id"], day_start))
    conversations = db.scalar("SELECT COUNT(*) FROM activities WHERE actor_id = ? AND kind = 'call' AND at >= ? "
                              "AND call_outcome IN ('connected','callback_requested')", (g.user["id"], day_start))
    no_next = db.all("SELECT o.*, c.full_name AS contact_name, b.name AS salon_name FROM opportunities o "
                     "JOIN contacts c ON c.id = o.contact_id LEFT JOIN salon_businesses b ON b.id = o.business_id "
                     "WHERE o.primary_owner_id = ? AND o.status IN ('active','holding') AND NOT EXISTS "
                     "(SELECT 1 FROM tasks t WHERE t.entity_type = 'opportunity' AND t.entity_id = o.id "
                     "AND t.status = 'open')", (g.user["id"],))
    notes = db.all("SELECT * FROM notifications WHERE user_id = ? AND read_at IS NULL ORDER BY created_at DESC LIMIT 8",
                   (g.user["id"],))
    from ..domain import matching as MM
    forms_waiting = db.scalar("SELECT COUNT(*) FROM form_submissions s JOIN opportunities o ON o.id = s.opportunity_id "
                              "WHERE s.status = 'submitted' AND o.primary_owner_id = ?", (g.user["id"],))
    return render_template("my_day.html", overdue=overdue, today=today, upcoming=upcoming, done_today=done_today,
                           calls_today=calls_today, conversations=conversations, no_next=no_next,
                           notifications=notes, STAGE_LABELS=STAGE_LABELS,
                           match_tiles=MM.my_tiles(db, g.user["id"]) if g.user["role"] != "finance" else [],
                           forms_waiting=forms_waiting)


@bp.route("/team")
@require("crm.view")
def team_board():
    db = get_db()
    now = now_iso()
    day_start, day_end = ist_day_bounds()
    users = db.all("SELECT id, full_name, role, team FROM users WHERE is_active = 1 AND role <> 'finance' ORDER BY full_name")
    open_tasks = db.all(TASK_SELECT + "WHERE t.status = 'open'")
    for u in users:
        ts = [t for t in open_tasks if t["assignee_id"] == u["id"]]
        u["open"] = len(ts)
        u["overdue"] = sum(1 for t in ts if t["due_at"] and t["due_at"] < now)
        u["today"] = sum(1 for t in ts if t["due_at"] and now <= t["due_at"] < day_end)
        u["done_today"] = db.scalar("SELECT COUNT(*) FROM tasks WHERE completed_by = ? AND completed_at >= ?",
                                    (u["id"], day_start))
        u["active_opps"] = db.scalar("SELECT COUNT(*) FROM opportunities WHERE primary_owner_id = ? AND status = 'active'",
                                     (u["id"],))
    who = request.args.get("who")
    shown = open_tasks if not who else [t for t in open_tasks if str(t["assignee_id"]) == who]
    for t in shown:
        t["why"] = T.priority_reason(t, now, day_end)
    overdue = _sort([t for t in shown if t["due_at"] and t["due_at"] < now])
    due_today = _sort([t for t in shown if t["due_at"] and now <= t["due_at"] < day_end])
    later = _sort([t for t in shown if not t["due_at"] or t["due_at"] >= day_end])
    exceptions = db.all("SELECT o.*, c.full_name AS contact_name, b.name AS salon_name, u.full_name AS owner_name "
                        "FROM opportunities o JOIN contacts c ON c.id = o.contact_id "
                        "LEFT JOIN salon_businesses b ON b.id = o.business_id LEFT JOIN users u ON u.id = o.primary_owner_id "
                        "WHERE o.status IN ('active','holding') AND NOT EXISTS (SELECT 1 FROM tasks t WHERE "
                        "t.entity_type = 'opportunity' AND t.entity_id = o.id AND t.status = 'open')")
    unowned = db.all("SELECT o.*, c.full_name AS contact_name, b.name AS salon_name FROM opportunities o "
                     "JOIN contacts c ON c.id = o.contact_id LEFT JOIN salon_businesses b ON b.id = o.business_id "
                     "WHERE o.status = 'active' AND o.primary_owner_id IS NULL")
    review_queue = db.all("SELECT o.id, o.ref_code, b.name AS salon_name, l.approval_state FROM seller_listings l "
                          "JOIN opportunities o ON o.id = l.opportunity_id JOIN salon_businesses b ON b.id = l.business_id "
                          "WHERE l.approval_state IN ('in_review','re_review_required') ORDER BY l.updated_at")
    buyer_queue = db.all("SELECT o.id, o.ref_code, c.full_name AS contact_name, m.funding_status, "
                         "m.criteria_reviewed_version, m.data_version FROM buyer_mandates m "
                         "JOIN opportunities o ON o.id = m.opportunity_id JOIN contacts c ON c.id = o.contact_id "
                         "WHERE o.status = 'active' AND (m.funding_status = 'evidence_requested' OR "
                         "(m.criteria_reviewed_version IS NOT NULL AND m.criteria_reviewed_version <> m.data_version) "
                         "OR o.stage IN ('form_received','funding_review')) ORDER BY o.updated_at")
    dup_open = db.scalar("SELECT COUNT(*) FROM duplicate_reviews WHERE status = 'open'")
    from ..domain import matching as MM
    match_counts = {k: db.scalar("SELECT COUNT(*) FROM match_candidates mc JOIN opportunities bo ON bo.id = mc.buyer_opp_id "
                                 "JOIN opportunities so ON so.id = mc.seller_opp_id WHERE " + MM.ACTIVE_WHERE +
                                 f" AND ({MM.QUEUES[k][1]})") for k in ("new", "info", "near", "consent", "ready", "stale")}
    top_matches = db.all(MM.CAND_SELECT + "WHERE " + MM.ACTIVE_WHERE + " AND mc.suppressed = 0 AND mc.outcome IN "
                         "('ready','near') ORDER BY mc.rank_score DESC LIMIT 5")
    return render_template("team.html", users=users, overdue=overdue, due_today=due_today, later=later,
                           exceptions=exceptions, unowned=unowned, review_queue=review_queue, buyer_queue=buyer_queue, dup_open=dup_open,
                           match_counts=match_counts, top_matches=top_matches, MQ=MM.QUEUES,
                           who=who, STAGE_LABELS=STAGE_LABELS)


@bp.route("/search")
@require("crm.view")
def search():
    q = (request.args.get("q") or "").strip()
    results = []
    if len(q) >= 2:
        db = get_db()
        digits = "".join(ch for ch in q if ch.isdigit())
        like = f"%{q.lower()}%"
        params = [like, like, like, like, like]
        phone_clause = ""
        if len(digits) >= 4:
            phone_clause = " OR c.phone_normalized LIKE ? OR c.alt_phone_normalized LIKE ?"
            params += [f"%{digits[-10:]}%", f"%{digits[-10:]}%"]
        results = db.all(
            "SELECT o.id, o.ref_code, o.kind, o.stage, o.status, c.full_name AS contact_name, c.phone_normalized, "
            "b.name AS salon_name, COALESCE(b.locality, c.locality) AS locality, COALESCE(b.city, c.city) AS city, "
            "u.full_name AS owner_name FROM opportunities o "
            "JOIN contacts c ON c.id = o.contact_id LEFT JOIN salon_businesses b ON b.id = o.business_id "
            "LEFT JOIN users u ON u.id = o.primary_owner_id WHERE lower(c.full_name) LIKE ? OR lower(b.name) LIKE ? "
            "OR lower(b.locality) LIKE ? OR lower(o.ref_code) LIKE ? OR lower(b.city) LIKE ?" + phone_clause +
            " ORDER BY o.updated_at DESC LIMIT 50", params)
    return render_template("search.html", q=q, results=results, STAGE_LABELS=STAGE_LABELS)


@bp.route("/notifications/read", methods=["POST"])
@login_required
def notifications_read():
    get_db().execute("UPDATE notifications SET read_at = ? WHERE user_id = ? AND read_at IS NULL",
                     (now_iso(), g.user["id"]))
    return redirect(request.referrer or url_for("main.my_day"))


@bp.route("/dictionary")
@require("crm.view")
def dictionary():
    return render_template("dictionary.html")


@bp.route("/planned/<module>")
@login_required
def planned(module):
    shipped = {"buyers": "buyers.index", "forms": "forms.inbox", "match": "matches.desk"}  # M3, M4, M5
    if module in shipped:
        return redirect(url_for(shipped[module]))
    names = {"match": ("Match Desk", "Milestone 5"),
             "forms": ("Forms & Data Requests", "Milestone 4"), "inbox": ("WhatsApp Team Inbox", "Milestone 7"),
             "deals": ("Deals / Workrooms", "Milestone 6"), "reports": ("Reports", "Milestone 7")}
    if module not in names:
        return redirect(url_for("main.my_day"))
    return render_template("planned.html", name=names[module][0], milestone=names[module][1])


@bp.route("/healthz")
def healthz():
    """Uptime check for monitoring. Reveals no business data."""
    from flask import jsonify
    db = get_db()
    try:
        db.scalar("SELECT 1")
        version = db.scalar("SELECT max(version) FROM schema_migrations")
        dead = db.scalar("SELECT COUNT(*) FROM jobs WHERE status = 'dead'")
        stale = db.scalar("SELECT COUNT(*) FROM jobs WHERE status = 'queued' AND run_after < ?",
                          (to_iso(now_utc() - timedelta(minutes=30)),))
    except Exception:  # noqa: BLE001
        return jsonify({"status": "error", "db": "unreachable"}), 503
    ok = stale == 0
    return jsonify({"status": "ok" if ok else "degraded", "db": "ok", "schema": version,
                    "jobs_dead": dead, "jobs_backlog_30m": stale}), (200 if ok else 503)
