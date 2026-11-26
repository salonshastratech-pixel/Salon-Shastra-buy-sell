"""Lead review: which records probably don't belong any more, and why — so the admin can look and remove them.

Each record is listed once, under its strongest reason:
  said_no      a call outcome of not interested / wrong number / do not contact / disqualified
  cold_remark  the team's remark says cold, time pass, just asking, not interested
  never_reached  called 3+ times and never connected
  untouched    no contact at all 30+ days after the lead came in
  test         a test / sample / dummy entry
"""
from __future__ import annotations

import re
from datetime import timedelta

from ..timeutil import now_utc, to_iso
from . import smart_import as SI
from .lead_marks import LEAD_AT

REASONS = [("said_no", "Said no on a call (not interested, wrong number, do not contact)"),
           ("cold_remark", "The team's remark says cold / time pass / not interested"),
           ("never_reached", "Called 3 or more times — never connected"),
           ("untouched", "Never contacted, 30+ days after coming in"),
           ("test", "Test / sample entry")]
REASON_LABELS = dict(REASONS)
NO_OUTCOMES = ("not_interested", "wrong_number", "do_not_contact", "disqualified")
NOT_REACHED = ("no_answer", "switched_off", "busy")
TEST_NAME = re.compile(r"^\s*(test|testing|sample|dummy|demo)\b", re.I)


def review(db, kind: str, why: str = "") -> dict:
    """{'rows': [...], 'counts': {reason: n}} for active records of this side."""
    side = "buyer" if kind == "buyer" else "seller"
    rows = db.all(
        f"SELECT o.id, o.kind, o.ref_code, o.stage, o.status, o.source, o.lead_created_at, o.created_at, "
        f"o.first_contact_at, o.team_remark, o.lead_temperature, o.lead_category, o.updated_at, {LEAD_AT} AS lead_at, "
        f"c.full_name AS contact_name, c.phone_normalized, b.name AS salon_name, u.full_name AS owner_name, "
        f"(SELECT COUNT(*) FROM activities a WHERE a.entity_type = 'opportunity' AND a.entity_id = o.id AND a.kind = 'call' "
        f" AND a.call_outcome IN ({','.join('?' * len(NO_OUTCOMES))})) AS said_no, "
        f"(SELECT COUNT(*) FROM activities a WHERE a.entity_type = 'opportunity' AND a.entity_id = o.id AND a.kind = 'call' "
        f" AND a.call_outcome IN ({','.join('?' * len(NOT_REACHED))})) AS missed, "
        f"(SELECT COUNT(*) FROM activities a WHERE a.entity_type = 'opportunity' AND a.entity_id = o.id AND a.kind = 'call' "
        f" AND a.call_outcome IN ('connected', 'callback_requested')) AS reached, "
        f"(SELECT MAX(a.at) FROM activities a WHERE a.entity_type = 'opportunity' AND a.entity_id = o.id AND a.kind IN "
        f" ('call', 'note')) AS last_touch "
        f"FROM opportunities o JOIN contacts c ON c.id = o.contact_id LEFT JOIN salon_businesses b ON b.id = o.business_id "
        f"LEFT JOIN users u ON u.id = o.primary_owner_id WHERE o.kind = ? AND o.status <> 'closed' ORDER BY o.id DESC",
        (*NO_OUTCOMES, *NOT_REACHED, side))
    month_ago = to_iso(now_utc() - timedelta(days=30))
    out, counts = [], {k: 0 for k, _ in REASONS}
    for r in rows:
        remark = (r["team_remark"] or "").lower()
        if TEST_NAME.search(r["contact_name"] or "") or TEST_NAME.search(r["salon_name"] or ""):
            reason = "test"
        elif r["said_no"]:
            reason = "said_no"
        elif r["lead_temperature"] not in ("hot", "warm") and (SI.COLD_REMARK.search(remark) or SI.judge_row(remark)):
            reason = "cold_remark"
        elif r["missed"] >= 3 and not r["reached"]:
            reason = "never_reached"
        elif not r["first_contact_at"] and not r["last_touch"] and r["lead_at"] and r["lead_at"] < month_ago:
            reason = "untouched"
        else:
            continue
        counts[reason] += 1
        if not why or why == reason:
            out.append({**r, "reason": reason, "reason_label": REASON_LABELS[reason]})
    return {"rows": out, "counts": counts}
