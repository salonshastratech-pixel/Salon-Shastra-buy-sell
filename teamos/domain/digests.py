"""Morning e-mail digest (FR-028; Milestone 7). One per user per IST day, only about that user's own work.

Privacy: the digest contains task titles and counts for the recipient only — never phone numbers, documents or
company finance (finance users get only fee totals). Provider failures are visible in Admin → Integrations.
"""
from __future__ import annotations

from flask import current_app, has_app_context

from .. import messaging
from ..permissions import user_can
from ..timeutil import fmt_ist, ist_day_bounds, now_iso, now_utc


def build(db, user) -> tuple[str, str]:
    start, end = ist_day_bounds()
    now = now_iso()
    overdue = db.all("SELECT title, due_at FROM tasks WHERE assignee_id = ? AND status = 'open' AND due_at < ? "
                     "ORDER BY due_at LIMIT 10", (user["id"], now))
    today = db.all("SELECT title, due_at FROM tasks WHERE assignee_id = ? AND status = 'open' AND due_at >= ? AND due_at < ? "
                   "ORDER BY due_at LIMIT 10", (user["id"], now, end))
    lines = [f"Good morning {user['full_name'].split()[0]},", ""]
    lines.append(f"Overdue: {len(overdue)} · Due today: {len(today)}")
    for t in overdue:
        lines.append(f"  ! {t['title']} (was due {fmt_ist(t['due_at'], '%d %b %I:%M %p')})")
    for t in today:
        lines.append(f"  - {t['title']} ({fmt_ist(t['due_at'], '%I:%M %p')})")
    if user["role"] != "finance":
        from . import matching as M
        tiles = M.my_tiles(db, user["id"])
        lines += ["", "Matching: " + " · ".join(f"{t['label']}: {t['count']}" for t in tiles if t["count"])]
        forms = db.scalar("SELECT COUNT(*) FROM form_submissions s JOIN opportunities o ON o.id = s.opportunity_id "
                          "WHERE s.status = 'submitted' AND o.primary_owner_id = ?", (user["id"],))
        unread = db.scalar("SELECT COUNT(*) FROM conversations WHERE assigned_to = ? AND unread > 0", (user["id"],))
        lines.append(f"Customer forms waiting for you: {forms} · Unread WhatsApp conversations: {unread}")
    if user_can(db, user, "finance.view"):
        from . import finance as FN
        tot = FN.ledger(db, user)["totals"]
        lines += ["", f"Fees outstanding (earned − received): ₹{tot['due']:,}"]
    lines += ["", "Open TeamOS to act on these. You can switch this e-mail off on My Day."]
    subject = f"Your day — {fmt_ist(now, '%a %d %b')}: {len(overdue)} overdue, {len(today)} due today"
    return subject, "\n".join(lines)


def send_all(db) -> str:
    cfg = current_app.config if has_app_context() else None
    prov = messaging.email_provider(cfg)
    day = fmt_ist(now_iso(), "%Y-%m-%d")
    sent = 0
    for u in db.all("SELECT * FROM users WHERE is_active = 1 AND digest_opt_in = 1"):
        key = f"digest:{u['id']}:{day}"
        if db.scalar("SELECT id FROM email_outbox WHERE dedupe_key = ?", (key,)):
            continue
        subject, body = build(db, u)
        res = prov.send(u["email"], subject, body)
        db.insert("email_outbox", {"user_id": u["id"], "to_addr": u["email"], "subject": subject, "body": body,
                                   "kind": "digest", "status": res["status"], "provider": prov.name,
                                   "error": res.get("detail") if res["status"] == "failed" else None, "dedupe_key": key,
                                   "created_at": now_iso(), "sent_at": now_iso() if res["status"] == "sent" else None})
        sent += 1
    return f"{sent} digest(s) via {prov.name}"


def due_now() -> bool:
    """Digests go out from 08:00 IST (the job runs hourly; the dedupe key keeps it to one per user per day)."""
    from ..timeutil import IST
    return now_utc().astimezone(IST).hour >= 8
