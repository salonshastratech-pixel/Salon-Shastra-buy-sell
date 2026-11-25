"""Funnel report: of the leads that came in during a period, how many reached each step — overall, by lead source and
by team member — with conversion from the first step. Salon owners and investors have their own steps."""
from __future__ import annotations

from datetime import datetime, time, timedelta

from ..timeutil import IST, now_utc, to_iso

PERIODS = [("week", "Last 7 days"), ("month", "Last 30 days"), ("quarter", "Last 90 days"), ("all", "All time")]

SELLER_STEPS = [
    ("leads", "Leads", "1 = 1"),
    ("contacted", "Contacted", "o.first_contact_at IS NOT NULL"),
    ("spoke", "Had a conversation", "EXISTS (SELECT 1 FROM activities a WHERE a.entity_type = 'opportunity' AND "
                                    "a.entity_id = o.id AND a.kind = 'call' AND a.call_outcome = 'connected')"),
    ("form", "Filled the pilot form", "EXISTS (SELECT 1 FROM pilot_profiles p WHERE p.opportunity_id = o.id AND "
                                      "(p.form_status = 'filled' OR COALESCE(p.form_answered, 0) > 0))"),
    ("report", "SWOT report sent", "EXISTS (SELECT 1 FROM swot_reports r WHERE r.opportunity_id = o.id AND "
                                   "r.sent_at IS NOT NULL)"),
    ("vikas", "Call with Vikas", "EXISTS (SELECT 1 FROM vikas_bookings v WHERE v.opportunity_id = o.id AND "
                                 "v.status IN ('booked', 'done'))"),
    ("paid", "Paid (consultation / ChairPe)", "(EXISTS (SELECT 1 FROM consultations k WHERE k.opportunity_id = o.id "
                                              "AND k.paid = 1) OR EXISTS (SELECT 1 FROM pilot_profiles p2 WHERE "
                                              "p2.opportunity_id = o.id AND p2.chairpe_status = 'paying'))"),
]
BUYER_STEPS = [
    ("leads", "Leads", "1 = 1"),
    ("contacted", "Contacted", "o.first_contact_at IS NOT NULL"),
    ("spoke", "Had a conversation", SELLER_STEPS[2][2]),
    ("form", "Filled the investor form", "EXISTS (SELECT 1 FROM investor_profiles p WHERE p.opportunity_id = o.id AND "
                                         "(p.form_status = 'filled' OR COALESCE(p.form_answered, 0) > 0))"),
    ("vikas", "Call with Vikas", SELLER_STEPS[5][2]),
    ("visit", "Office visit", "EXISTS (SELECT 1 FROM investor_profiles p3 WHERE p3.opportunity_id = o.id AND "
                              "p3.office_visit_status = 'done')"),
    ("paid", "Registration paid", "EXISTS (SELECT 1 FROM investor_profiles p4 WHERE p4.opportunity_id = o.id AND "
                                  "p4.reg_status = 'paid')"),
]
SOURCE_NAMES = {"meta_lead": "Facebook / Instagram ads", "import": "Sheet import", "landing_page": "Website / open form",
                "manual_call": "Phone call", "referral": "Referral", "whatsapp": "WhatsApp", "walk_in": "Walk-in"}


def _since(period: str) -> str | None:
    days = {"week": 7, "month": 30, "quarter": 90}.get(period)
    if not days:
        return None
    start = datetime.combine(now_utc().astimezone(IST).date() - timedelta(days=days - 1), time(0, 0), tzinfo=IST)
    return to_iso(start)


def build(db, kind: str = "seller", period: str = "month") -> dict:
    steps = BUYER_STEPS if kind == "buyer" else SELLER_STEPS
    since = _since(period)
    where = "o.kind = ?" + (" AND COALESCE(o.lead_created_at, o.created_at) >= ?" if since else "")
    params = [kind] + ([since] if since else [])
    cols = ", ".join(f"SUM(CASE WHEN {cond} THEN 1 ELSE 0 END) AS s_{key}" for key, _l, cond in steps)

    def run(group_sql: str, group_name: str) -> list[dict]:
        grouping = "" if group_name == "all" else f" GROUP BY {group_sql} ORDER BY COUNT(*) DESC"
        rows = db.all(f"SELECT {group_sql} AS grp, {cols} FROM opportunities o LEFT JOIN users u ON "
                      f"u.id = o.primary_owner_id WHERE {where}{grouping}", params)
        out = []
        for r in rows:
            counts = [r[f"s_{k}"] or 0 for k, _l, _c in steps]
            name = r["grp"] if group_name != "source" else SOURCE_NAMES.get(r["grp"], (r["grp"] or "Other")
                                                                                      .replace("_", " ").capitalize())
            out.append({"name": name or "Unassigned", "counts": counts,
                        "pct": [round(100 * c / counts[0]) if counts[0] else 0 for c in counts]})
        return out
    total = run("'all'", "all")
    overall = total[0] if total else {"counts": [0] * len(steps), "pct": [0] * len(steps)}
    return {"kind": kind, "period": period, "steps": [(k, lbl) for k, lbl, _c in steps], "overall": overall,
            "by_source": run("o.source", "source"), "by_owner": run("u.full_name", "owner")}
