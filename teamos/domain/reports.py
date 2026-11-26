"""Leadership and team reporting (PRD §1.4, §11.4; FR-021; UAT-22; Milestone 7).

Every metric is counted from the underlying records for one period with explicit IST boundaries
[start, end) and can be exported; the export is produced from the same numbers shown on screen, so they reconcile.
Company fees appear only for users with `finance.view`. No targets or baselines are invented (§1.4).
"""
from __future__ import annotations

import csv
import io
from datetime import date, datetime, timedelta

from ..permissions import user_can
from ..timeutil import IST, now_utc, to_iso

PERIODS = [("today", "Today"), ("yesterday", "Yesterday"), ("week", "This week"), ("month", "This month"),
           ("custom", "Custom")]


def period_bounds(key: str, start: str | None = None, end: str | None = None, now=None) -> dict:
    """UTC ISO [start, end) for an IST period. Weeks start on Monday."""
    local = (now or now_utc()).astimezone(IST)
    today = local.replace(hour=0, minute=0, second=0, microsecond=0)
    if key == "yesterday":
        s, e = today - timedelta(days=1), today
    elif key == "week":
        s, e = today - timedelta(days=today.weekday()), today + timedelta(days=1)
    elif key == "month":
        s, e = today.replace(day=1), today + timedelta(days=1)
    elif key == "custom":
        try:
            ds, de = date.fromisoformat(start), date.fromisoformat(end)
        except (TypeError, ValueError):
            ds = de = today.date()
        if de < ds:
            ds, de = de, ds
        s = datetime(ds.year, ds.month, ds.day, tzinfo=IST)
        e = datetime(de.year, de.month, de.day, tzinfo=IST) + timedelta(days=1)
    else:
        key = "today"
        s, e = today, today + timedelta(days=1)
    label = s.strftime("%d %b %Y") if (e - s).days == 1 else f"{s.strftime('%d %b')} – {(e - timedelta(days=1)).strftime('%d %b %Y')}"
    return {"key": key, "start": to_iso(s), "end": to_iso(e), "label": label + " (IST)",
            "start_date": s.date().isoformat(), "end_date": (e - timedelta(days=1)).date().isoformat()}


def _n(db, sql, *params):
    return db.scalar(sql, params) or 0


def build(db, user, p: dict) -> dict:
    s, e = p["start"], p["end"]
    from .lead_marks import LEAD_AT   # when the lead really came in (an import's own date, never the import day)
    leads = db.all(f"SELECT o.kind, o.source, COUNT(*) AS n FROM opportunities o WHERE o.stage != 'moved' AND {LEAD_AT} >= ? AND {LEAD_AT} < ? "
                   "GROUP BY o.kind, o.source ORDER BY o.kind, n DESC", (s, e))
    new_opps = db.all(f"SELECT o.id, {LEAD_AT} AS created_at FROM opportunities o WHERE o.stage != 'moved' AND {LEAD_AT} >= ? AND {LEAD_AT} < ?",
                      (s, e))
    reached = 0
    speeds = []
    for o in new_opps:
        first = db.scalar("SELECT MIN(at) FROM activities WHERE entity_type = 'opportunity' AND entity_id = ? AND kind = "
                          "'call' AND call_outcome IN ('connected','callback_requested')", (o["id"],))
        if first:
            reached += 1
            try:
                speeds.append((datetime.fromisoformat(first.replace("Z", "+00:00")) -
                               datetime.fromisoformat(o["created_at"].replace("Z", "+00:00"))).total_seconds() / 60)
            except ValueError:
                pass
    speeds.sort()
    median_min = round(speeds[len(speeds) // 2]) if speeds else None
    team = db.all(
        "SELECT u.id, u.full_name, "
        "(SELECT COUNT(*) FROM tasks t WHERE t.completed_by = u.id AND t.status = 'done' AND t.completed_at >= ? AND t.completed_at < ?) AS done, "
        "(SELECT COUNT(*) FROM tasks t WHERE t.completed_by = u.id AND t.status = 'done' AND t.completed_at >= ? AND t.completed_at < ? "
        "  AND (t.due_at IS NULL OR t.completed_at <= t.due_at)) AS on_time, "
        "(SELECT COUNT(*) FROM tasks t WHERE t.assignee_id = u.id AND t.status = 'open' AND t.due_at < ?) AS overdue_now, "
        "(SELECT COUNT(*) FROM activities a WHERE a.actor_id = u.id AND a.kind = 'call' AND a.at >= ? AND a.at < ?) AS calls, "
        "(SELECT COUNT(*) FROM activities a WHERE a.actor_id = u.id AND a.kind = 'call' AND a.at >= ? AND a.at < ? "
        "  AND a.call_outcome IN ('connected','callback_requested')) AS conversations, "
        "(SELECT COUNT(*) FROM form_submissions f JOIN opportunities o ON o.id = f.opportunity_id WHERE f.reviewed_by = u.id "
        "  AND f.reviewed_at >= ? AND f.reviewed_at < ?) AS forms_reviewed "
        "FROM users u WHERE u.is_active = 1 AND u.role <> 'finance' ORDER BY u.full_name",
        (s, e, s, e, to_iso(now_utc()), s, e, s, e, s, e))
    forms = {"sent": _n(db, "SELECT COUNT(*) FROM form_invitations WHERE created_at >= ? AND created_at < ?", s, e),
             "opened": _n(db, "SELECT COUNT(*) FROM form_invitations WHERE opened_at >= ? AND opened_at < ?", s, e),
             "submitted": _n(db, "SELECT COUNT(*) FROM form_submissions WHERE submitted_at >= ? AND submitted_at < ?", s, e),
             "reviewed": _n(db, "SELECT COUNT(*) FROM form_submissions WHERE reviewed_at >= ? AND reviewed_at < ?", s, e)}
    matching = {
        "new candidates": _n(db, "SELECT COUNT(*) FROM match_candidates WHERE created_at >= ? AND created_at < ?", s, e),
        "shortlisted": _n(db, "SELECT COUNT(*) FROM match_events WHERE to_stage = 'shortlisted' AND at >= ? AND at < ?", s, e),
        "approved to approach": _n(db, "SELECT COUNT(*) FROM match_events WHERE to_stage = 'approved_to_approach' AND at >= ? AND at < ?", s, e),
        "interest confirmed": _n(db, "SELECT COUNT(*) FROM match_events WHERE to_stage = 'interest_confirmed' AND at >= ? AND at < ?", s, e),
        "introductions approved": _n(db, "SELECT COUNT(*) FROM introductions WHERE status = 'approved' AND decided_at >= ? AND decided_at < ?", s, e),
        "visits completed": _n(db, "SELECT COUNT(*) FROM deal_visits WHERE status = 'completed' AND updated_at >= ? AND updated_at < ?", s, e),
        "offers recorded": _n(db, "SELECT COUNT(*) FROM deal_offers WHERE created_at >= ? AND created_at < ?", s, e),
        "deals closed won": _n(db, "SELECT COUNT(*) FROM deals WHERE stage = 'closed_won' AND closed_at >= ? AND closed_at < ?", s, e),
        "deals lost / withdrawn": _n(db, "SELECT COUNT(*) FROM deals WHERE stage IN ('closed_lost','withdrawn') AND closed_at >= ? AND closed_at < ?", s, e),
    }
    reject = db.all("SELECT suppressed_reason AS reason, COUNT(*) AS n FROM match_candidates WHERE suppressed = 1 AND "
                    "updated_at >= ? AND updated_at < ? GROUP BY suppressed_reason ORDER BY n DESC", (s, e))
    msgs = {k: _n(db, f"SELECT COUNT(*) FROM messages WHERE direction = 'out' AND kind <> 'system' AND {cond} "
                      "AND created_at >= ? AND created_at < ?", s, e)
            for k, cond in [("sent (all)", "1=1"), ("delivered or read", "status IN ('delivered','read')"),
                            ("failed", "status = 'failed'"), ("uncertain", "status = 'uncertain'")]}
    msgs["received"] = _n(db, "SELECT COUNT(*) FROM messages WHERE direction = 'in' AND created_at >= ? AND created_at < ?", s, e)
    msgs["opt-outs"] = _n(db, "SELECT COUNT(*) FROM contact_consents WHERE purpose = 'service_contact' AND granted = 0 AND "
                              "source = 'whatsapp_optout' AND captured_at >= ? AND captured_at < ?", s, e)
    inventory = {
        "sellers active": _n(db, "SELECT COUNT(*) FROM opportunities WHERE kind = 'seller' AND status = 'active'"),
        "listings approved": _n(db, "SELECT COUNT(*) FROM seller_listings l JOIN opportunities o ON o.id = l.opportunity_id "
                                    "WHERE l.approval_state = 'approved' AND o.status = 'active'"),
        "listings waiting for review": _n(db, "SELECT COUNT(*) FROM seller_listings WHERE approval_state IN ('in_review','re_review_required')"),
        "buyers active": _n(db, "SELECT COUNT(*) FROM opportunities WHERE kind = 'buyer' AND status = 'active'"),
        "buyers with reviewed funding": _n(db, "SELECT COUNT(*) FROM buyer_mandates m JOIN opportunities o ON o.id = m.opportunity_id "
                                               "WHERE o.status = 'active' AND m.funding_status IN ('staff_reviewed','verified')"),
        "open deals": _n(db, "SELECT COUNT(*) FROM deals WHERE stage NOT IN ('closed_won','closed_lost','withdrawn')"),
    }
    out = {"period": p, "leads": leads, "lead_total": len(new_opps), "reached": reached, "median_minutes": median_min,
           "team": team, "forms": forms, "matching": matching, "rejections": reject, "messages": msgs,
           "inventory": inventory, "finance": None}
    if user_can(db, user, "finance.view"):
        from . import finance as FN
        led = FN.ledger(db, user)
        closed_ids = {r["id"] for r in db.all("SELECT id FROM deals WHERE stage = 'closed_won' AND closed_at >= ? AND "
                                               "closed_at < ?", (s, e))}
        out["finance"] = {
            "fees earned on deals closed in period": sum(r["calc"]["earned"] for r in led["rows"]
                                                         if r["deal_id"] in closed_ids and r["status"] == "active"),
            "fees received in period": _n(db, "SELECT SUM(amount) FROM fee_receipts WHERE received_on >= ? AND received_on <= ?",
                                          p["start_date"], p["end_date"]),
            "expenses in period": _n(db, "SELECT SUM(amount) FROM company_expenses WHERE spent_on >= ? AND spent_on <= ?",
                                     p["start_date"], p["end_date"]),
            "outstanding fees (all time)": led["totals"]["due"],
        }
    return out


def to_csv(r: dict) -> str:
    """Aggregates only (no personal data); the same numbers as the screen."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Salon Shastra TeamOS report", r["period"]["label"]])
    w.writerow(["section", "metric", "value"])
    w.writerow(["leads", "new opportunities", r["lead_total"]])
    w.writerow(["leads", "reached (connected call)", r["reached"]])
    w.writerow(["leads", "median minutes to first real conversation", r["median_minutes"] if r["median_minutes"] is not None else ""])
    for row in r["leads"]:
        w.writerow(["leads by source", f"{row['kind']} / {row['source']}", row["n"]])
    for sec in ("forms", "matching", "messages", "inventory"):
        for k, v in r[sec].items():
            w.writerow([sec, k, v])
    for row in r["rejections"]:
        w.writerow(["match rejections", row["reason"] or "unspecified", row["n"]])
    for t in r["team"]:
        for k in ("done", "on_time", "overdue_now", "calls", "conversations", "forms_reviewed"):
            w.writerow(["team", f"{t['full_name']} — {k.replace('_', ' ')}", t[k]])
    if r["finance"]:
        for k, v in r["finance"].items():
            w.writerow(["finance", k, v])
    return buf.getvalue()


# -- Milestone 10: results by lead source ------------------------------------------------------------------------
SELLER_QUALIFIED = {"listing_approved", "active_matching", "negotiation", "handover", "closed_won"}
BUYER_QUALIFIED = {"qualified_shortlist", "shortlist_shared", "viewing_offer", "transaction", "closed_won"}
DETAIL_STAGES = {"form_received", "assessment_review", "verification_pending", "basic_qualified",
                 "requirements_captured", "funding_review"} | SELLER_QUALIFIED | BUYER_QUALIFIED
STEPS = ["leads", "reached", "details", "qualified", "matched", "deals", "won"]
STEP_LABELS = {"leads": "Leads", "reached": "Spoke with us", "details": "Gave details",
               "qualified": "Qualified", "matched": "Shortlisted in a match", "deals": "Introduced (deal opened)",
               "won": "Deal won"}


def sources(db, user, p: dict) -> dict:
    """Which channels bring people who go all the way — not just how many leads (PRD §2.2 channel ROI)."""
    from . import sellers as S
    s, e = p["start"], p["end"]
    from .lead_marks import LEAD_AT
    opps = db.all(f"SELECT o.id, o.kind, o.source, o.source_detail, {LEAD_AT} AS created_at, o.stage FROM opportunities o "
                  f"WHERE o.stage != 'moved' AND {LEAD_AT} >= ? AND {LEAD_AT} < ?", (s, e))
    ids = [o["id"] for o in opps]
    if not ids:
        return {"rows": [], "campaigns": [], "total": None, "STEPS": STEPS, "LABELS": STEP_LABELS, "fees": False}

    def ids_in(sql, extra=()):
        out = set()
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            out |= {list(r.values())[0] for r in db.all(sql.replace("{IDS}", ",".join("?" * len(chunk))),
                                                        (*chunk, *extra))}
        return out

    first_talk = {}
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        for r in db.all("SELECT entity_id AS oid, MIN(at) AS t FROM activities WHERE entity_type = 'opportunity' "
                        "AND kind = 'call' AND call_outcome IN ('connected','callback_requested') AND entity_id IN (" +
                        ",".join("?" * len(chunk)) + ") GROUP BY entity_id", chunk):
            first_talk[r["oid"]] = r["t"]
    stages_seen = {}
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        for r in db.all("SELECT opportunity_id, to_stage FROM stage_history WHERE opportunity_id IN (" +
                        ",".join("?" * len(chunk)) + ")", chunk):
            stages_seen.setdefault(r["opportunity_id"], set()).add(r["to_stage"])
    has_form = ids_in("SELECT opportunity_id FROM form_submissions WHERE opportunity_id IN ({IDS})")
    matched = ids_in("SELECT seller_opp_id FROM match_candidates WHERE seller_opp_id IN ({IDS}) AND stage IN "
                     "('shortlisted','approved_to_approach','interest_confirmed')") | \
        ids_in("SELECT buyer_opp_id FROM match_candidates WHERE buyer_opp_id IN ({IDS}) AND stage IN "
               "('shortlisted','approved_to_approach','interest_confirmed')")
    deal_of = {}
    for col in ("seller_opp_id", "buyer_opp_id"):
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            for r in db.all(f"SELECT {col} AS oid, id, stage FROM deals WHERE {col} IN (" +
                            ",".join("?" * len(chunk)) + ")", chunk):
                deal_of.setdefault(r["oid"], []).append((r["id"], r["stage"]))
    fees_ok = user_can(db, user, "finance.view")
    earned_by_deal = {}
    if fees_ok:
        from . import finance as FN
        for r in FN.ledger(db, user)["rows"]:
            if r["status"] == "active":
                earned_by_deal[r["deal_id"]] = earned_by_deal.get(r["deal_id"], 0) + (r["calc"]["earned"] or 0)

    def channel(o):
        return S.SOURCE_LABELS.get(o["source"], (o["source"] or "Unknown").replace("_", " ").capitalize())

    def campaign(o):
        d = (o["source_detail"] or "").strip()
        return d[:80] if d and not d.startswith("{") else None

    def blank():
        return {**{k: 0 for k in STEPS}, "hours": [], "fees": 0, "deal_ids": set()}

    groups, camps = {}, {}
    total = blank()
    for o in opps:
        seen = stages_seen.get(o["id"], set()) | {o["stage"]}
        qual = SELLER_QUALIFIED if o["kind"] == "seller" else BUYER_QUALIFIED
        flags = {"leads": True, "reached": o["id"] in first_talk,
                 "details": o["id"] in has_form or bool(seen & DETAIL_STAGES),
                 "qualified": bool(seen & qual), "matched": o["id"] in matched or bool(deal_of.get(o["id"])),
                 "deals": bool(deal_of.get(o["id"])),
                 "won": any(st == "closed_won" for _, st in deal_of.get(o["id"], []))}
        hrs = None
        if o["id"] in first_talk:
            try:
                hrs = (datetime.fromisoformat(first_talk[o["id"]].replace("Z", "+00:00")) -
                       datetime.fromisoformat(o["created_at"].replace("Z", "+00:00"))).total_seconds() / 3600
            except ValueError:
                hrs = None
        keys = [(groups, (o["kind"], channel(o)))]
        if campaign(o):
            keys.append((camps, (o["kind"], channel(o), campaign(o))))
        for bucket, key in keys + [(None, None)]:
            g = total if bucket is None else bucket.setdefault(key, blank())
            for k in STEPS:
                g[k] += 1 if flags[k] else 0
            if hrs is not None and hrs >= 0:
                g["hours"].append(hrs)
            for did, st in deal_of.get(o["id"], []):
                if st == "closed_won" and did not in g["deal_ids"]:
                    g["deal_ids"].add(did)
                    g["fees"] += earned_by_deal.get(did, 0)

    def finish(g):
        h = sorted(g.pop("hours"))
        g.pop("deal_ids")
        g["median_hours"] = round(h[len(h) // 2], 1) if h else None
        g["pct"] = {k: (round(100 * g[k] / g["leads"]) if g["leads"] else 0) for k in STEPS}
        if not fees_ok:
            g.pop("fees")
        return g

    rows = [{"kind": k[0], "channel": k[1], **finish(g)} for k, g in groups.items()]
    rows.sort(key=lambda r: (r["kind"], -r["leads"]))
    camp_rows = [{"kind": k[0], "channel": k[1], "campaign": k[2], **finish(g)} for k, g in camps.items()]
    camp_rows.sort(key=lambda r: -r["leads"])
    return {"rows": rows, "campaigns": camp_rows[:15], "total": finish(total), "STEPS": STEPS, "LABELS": STEP_LABELS,
            "fees": fees_ok}
