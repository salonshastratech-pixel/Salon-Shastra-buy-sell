"""Today's numbers for a person (My Day) or the whole team (Founder desk), the way sales CRMs show them: new leads
(and when they came in), calls to make, calls made and how many were productive, follow-ups due, missed (overdue),
and leads still waiting for their first call — plus a feed of the latest activity."""
from __future__ import annotations

from ..timeutil import ist_day_bounds, now_iso
from .lead_marks import LEAD_AT

CALL_TYPES = ("first_contact", "call")
FOLLOW_TYPES = ("follow_up", "collect_info", "visit", "review")
PRODUCTIVE = ("connected", "callback_requested")


def _in(ids: list[int]) -> tuple[str, list]:
    return ",".join("?" * len(ids)), list(ids)


def numbers(db, user_ids: list[int]) -> dict:
    if not user_ids:
        return {k: 0 for k in ("new_leads", "calls_to_do", "calls_made", "productive", "productive_pct",
                               "followups_due", "missed", "waiting")}
    now = now_iso()
    day_start, day_end = ist_day_bounds()
    m, ids = _in(user_ids)
    ct, cts = _in(list(CALL_TYPES))
    ft, fts = _in(list(FOLLOW_TYPES))
    pr, prs = _in(list(PRODUCTIVE))

    def n(sql, *p):
        return db.scalar(sql, p) or 0
    calls_made = n(f"SELECT COUNT(*) FROM activities WHERE kind = 'call' AND at >= ? AND actor_id IN ({m})",
                   day_start, *ids)
    productive = n(f"SELECT COUNT(*) FROM activities WHERE kind = 'call' AND at >= ? AND actor_id IN ({m}) AND "
                   f"call_outcome IN ({pr})", day_start, *ids, *prs)
    return {
        "new_leads": n(f"SELECT COUNT(*) FROM opportunities o WHERE o.primary_owner_id IN ({m}) AND "
                       f"o.stage != 'moved' AND {LEAD_AT} >= ?", *ids, day_start),
        "calls_to_do": n(f"SELECT COUNT(*) FROM tasks WHERE status = 'open' AND assignee_id IN ({m}) AND task_type IN "
                         f"({ct}) AND due_at IS NOT NULL AND due_at < ?", *ids, *cts, day_end),
        "calls_made": calls_made, "productive": productive,
        "productive_pct": round(100 * productive / calls_made) if calls_made else 0,
        "followups_due": n(f"SELECT COUNT(*) FROM tasks WHERE status = 'open' AND assignee_id IN ({m}) AND task_type "
                           f"IN ({ft}) AND due_at IS NOT NULL AND due_at < ?", *ids, *fts, day_end),
        "missed": n(f"SELECT COUNT(*) FROM tasks WHERE status = 'open' AND assignee_id IN ({m}) AND due_at IS NOT NULL "
                    f"AND due_at < ?", *ids, now),
        "waiting": n(f"SELECT COUNT(*) FROM opportunities WHERE primary_owner_id IN ({m}) AND status = 'active' AND "
                     f"first_contact_at IS NULL", *ids),
    }


def new_leads(db, user_ids: list[int], limit: int = 10) -> list[dict]:
    """Newest leads first, with when they came in and how fast they were first called."""
    if not user_ids:
        return []
    m, ids = _in(user_ids)
    rows = db.all(f"SELECT o.id, o.kind, o.ref_code, o.source, o.source_detail, o.lead_created_at, o.created_at, "
                  f"o.first_contact_at, o.status, o.source, o.lead_temperature, o.lead_category, o.team_remark, "
                  f"c.full_name AS contact_name, c.phone_normalized, b.name AS salon_name, "
                  f"u.full_name AS owner_name FROM opportunities o JOIN contacts c ON c.id = o.contact_id LEFT JOIN "
                  f"salon_businesses b ON b.id = o.business_id LEFT JOIN users u ON u.id = o.primary_owner_id WHERE "
                  f"o.primary_owner_id IN ({m}) AND o.status = 'active' AND {LEAD_AT} IS NOT NULL ORDER BY {LEAD_AT} "
                  f"DESC LIMIT ?", (*ids, limit))
    from .timeline import SOURCE_NAMES, span, speed
    for r in rows:
        r["lead_at"] = r["lead_created_at"] or r["created_at"]   # rows without a known time are not listed here
        r["source_name"] = SOURCE_NAMES.get(r["source"], (r["source"] or "").replace("_", " ").capitalize())
        r["speed"] = speed(r)
        r["first_after"] = span(r["lead_at"], r["first_contact_at"]) if r["first_contact_at"] else None
    return rows


def feed(db, user_ids: list[int], limit: int = 12) -> list[dict]:
    """Latest calls, notes and system events on these people's records (or by them)."""
    if not user_ids:
        return []
    m, ids = _in(user_ids)
    return db.all(f"SELECT a.at, a.kind, a.body, a.call_outcome, a.entity_id, o.kind AS opp_kind, o.ref_code, "
                  f"c.full_name AS contact_name, b.name AS salon_name, u.full_name AS actor_name FROM activities a "
                  f"JOIN opportunities o ON a.entity_type = 'opportunity' AND o.id = a.entity_id JOIN contacts c ON "
                  f"c.id = o.contact_id LEFT JOIN salon_businesses b ON b.id = o.business_id LEFT JOIN users u ON "
                  f"u.id = a.actor_id WHERE (o.primary_owner_id IN ({m}) OR a.actor_id IN ({m})) AND a.kind IN "
                  f"('call', 'note', 'system', 'stage_change', 'assignment') ORDER BY a.at DESC, a.id DESC LIMIT ?",
                  (*ids, *ids, limit))


def team_rows(db) -> list[dict]:
    """The same numbers per team member (Founder desk)."""
    people = db.all("SELECT id, full_name, work_status FROM users WHERE is_active = 1 AND role IN "
                    "('team_member', 'team_lead') ORDER BY full_name")
    from .availability import STATUS_LABELS
    return [{**p, "status_label": STATUS_LABELS.get(p["work_status"], ""), **numbers(db, [p["id"]])} for p in people]


def team_ids(db) -> list[int]:
    return [r["id"] for r in db.all("SELECT id FROM users WHERE is_active = 1 AND role IN ('team_member', 'team_lead', "
                                    "'super_admin')")]
