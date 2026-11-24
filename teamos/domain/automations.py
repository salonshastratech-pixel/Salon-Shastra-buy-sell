"""Milestone 10 — everything TeamOS does by itself, in one list anyone can read and an admin can pause.

Each automation is a background job kind. Pausing one makes the worker skip it (periodic checks) or hold it
(incoming lead ads wait and are processed after it is switched back on — nothing is lost). "Pause everything"
does the same for all of them and also holds bulk WhatsApp sends. Automations only ever create internal tasks,
alerts and drafts: they never approve disclosures, move money or message customers on their own (PRD §10.5).
"""
from __future__ import annotations

from datetime import timedelta

from .. import audit
from ..permissions import user_can
from ..timeutil import IST, now_iso, now_utc, to_iso
from .errors import PermissionDenied, ValidationError

AUTOMATIONS = [
    {"key": "sla_sweep", "name": "Overdue alerts", "when": "Every 15 minutes",
     "what": "Tells the person when a task is overdue, and their team lead when it is a day late."},
    {"key": "form_chase", "name": "Chase unfinished forms", "when": "Every hour",
     "what": "Adds a follow-up task when a customer has not finished a form in 2 days; closes expired links."},
    {"key": "match_refresh", "name": "Re-check matches", "when": "Every 15 minutes",
     "what": "Re-scores buyer and salon pairs whose details changed, so the Match Desk stays current."},
    {"key": "stuck_sweep", "name": "Not-moving alerts", "when": "Once a day",
     "what": "One alert per person listing their records that have stopped moving, with a suggested push."},
    {"key": "daily_digest", "name": "Morning e-mail", "when": "8 am",
     "what": "People who switched it on get the day's work by e-mail (no phone numbers or documents)."},
    {"key": "learning_propose", "name": "Match-scoring suggestions", "when": "Once a week",
     "what": "Suggests scoring changes from declined matches. Nothing changes until the founder approves."},
    {"key": "lead_ads", "name": "Bring in lead-ad leads", "when": "As leads arrive",
     "what": "New Facebook / Instagram leads go through the smart import and get an owner and a first call. "
             "While paused, leads wait and are brought in when switched back on."},
    {"key": "webhook_retry", "name": "Retry failed WhatsApp updates", "when": "Every 15 minutes",
     "what": "Re-processes delivery updates and replies that failed. Always on — pausing could lose messages.",
     "fixed": True},
]
BY_KEY = {a["key"]: a for a in AUTOMATIONS}
HOLD_WHEN_PAUSED = {"lead_ads"}   # these wait instead of being skipped
QUIET_DEFAULT = "21-8"            # bulk WhatsApp not sent 9 pm – 8 am IST (D-68)


def _row(db, key):
    return db.one("SELECT * FROM automation_settings WHERE key = ?", (key,))


def is_on(db, key: str) -> bool:
    r = _row(db, key)
    return True if r is None else bool(r["enabled"])


def all_paused(db) -> bool:
    return not is_on(db, "_all")


def paused(db, kind: str) -> bool:
    """Should the worker hold back this job kind right now? Unknown kinds (tests, one-offs) always run."""
    a = BY_KEY.get(kind)
    if not a or a.get("fixed"):
        return False
    return all_paused(db) or not is_on(db, kind)


def set_enabled(db, actor: dict, key: str, enabled: bool, note: str | None = None) -> None:
    if not user_can(db, actor, "settings.manage"):
        raise PermissionDenied("settings.manage")
    if key not in BY_KEY and key not in ("_all", "quiet_hours"):
        raise ValidationError("Unknown automation.")
    if BY_KEY.get(key, {}).get("fixed"):
        raise ValidationError("This one always stays on.")
    note = (note or "").strip()[:200] or None
    now = now_iso()
    with db:
        if _row(db, key):
            db.execute("UPDATE automation_settings SET enabled = ?, note = ?, updated_by = ?, updated_at = ? "
                       "WHERE key = ?", (1 if enabled else 0, note, actor["id"], now, key))
        else:
            db.execute("INSERT INTO automation_settings (key, enabled, note, updated_by, updated_at) "
                       "VALUES (?, ?, ?, ?, ?)", (key, 1 if enabled else 0, note, actor["id"], now))
        audit.record(db, actor["id"], "automation." + ("resumed" if enabled else "paused"), None, None,
                     {"key": key, "note": note})


def _window(config) -> tuple[int, int] | None:
    raw = (config or {}).get("QUIET_HOURS", QUIET_DEFAULT)
    if not raw:
        return None
    try:
        a, b = (int(x) for x in str(raw).split("-"))
        return a % 24, b % 24
    except ValueError:
        return None


def quiet_now(db, config=None, at=None) -> bool:
    """True during quiet hours (IST) when the quiet-hours guard is on."""
    if not is_on(db, "quiet_hours"):
        return False
    w = _window(config)
    if not w:
        return False
    h = (at or now_utc()).astimezone(IST).hour
    start, end = w
    return (start <= h or h < end) if start > end else (start <= h < end)


def quiet_label(config=None) -> str:
    w = _window(config)
    if not w:
        return "not set"
    fmt = lambda h: f"{(h % 12) or 12} {'am' if h < 12 else 'pm'}"  # noqa: E731
    return f"{fmt(w[0])} – {fmt(w[1])}"


def bulk_send_blockers(db, config=None) -> list[str]:
    """Reasons a bulk WhatsApp batch may not go out right now (one-to-one messages are not affected)."""
    out = []
    if all_paused(db):
        out.append("Automations are paused for everyone, so bulk messages are on hold. An admin can resume them "
                   "in Tools & admin → Automations.")
    if quiet_now(db, config):
        out.append(f"Quiet hours ({quiet_label(config)}): bulk messages are not sent at night. Please send after "
                   f"{quiet_label(config).split('– ')[-1]}.")
    return out


def overview(db, config=None) -> dict:
    """The Automations page: each automation with its state, last run and what it did."""
    week_ago = to_iso(now_utc() - timedelta(days=7))
    names = {r["id"]: r["full_name"] for r in db.all("SELECT id, full_name FROM users")}
    items = []
    for a in AUTOMATIONS:
        r = _row(db, a["key"])
        last = db.one("SELECT jr.finished_at, jr.outcome, jr.message FROM job_runs jr JOIN jobs j ON j.id = jr.job_id "
                      "WHERE j.kind = ? AND jr.finished_at IS NOT NULL ORDER BY jr.id DESC LIMIT 1", (a["key"],))
        runs = db.scalar("SELECT COUNT(*) FROM job_runs jr JOIN jobs j ON j.id = jr.job_id WHERE j.kind = ? AND "
                         "jr.started_at >= ?", (a["key"], week_ago))
        errors = db.scalar("SELECT COUNT(*) FROM jobs WHERE kind = ? AND status = 'dead'", (a["key"],))
        items.append({**a, "on": True if r is None else bool(r["enabled"]),
                      "changed_by": names.get(r["updated_by"]) if r else None,
                      "changed_at": r["updated_at"] if r else None, "note": r["note"] if r else None,
                      "last": last, "runs_week": runs, "errors": errors})
    allr = _row(db, "_all")
    return {"autos": items, "all_paused": all_paused(db),
            "all_by": names.get(allr["updated_by"]) if allr else None, "all_at": allr["updated_at"] if allr else None,
            "all_note": allr["note"] if allr else None,
            "quiet_on": is_on(db, "quiet_hours"), "quiet_label": quiet_label(config),
            "quiet_now": quiet_now(db, config)}
