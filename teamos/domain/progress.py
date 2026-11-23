"""Milestone 8 — keeping work moving: stuck records and team workload.

Stuck = a live seller, buyer or deal has sat in the same stage longer than that stage usually takes AND nobody has
spoken to the customer in that time. Each stage has a usual duration and one suggested push (decision D-61:
starting values, tuned from pilot data). Leads whose first call is simply not due yet are not stuck.

Workload = each person's open records and tasks, so a team lead can spot overload, mark someone away and hand
their work to colleagues in one step (the person's records, open tasks and pending first calls move together).
"""
from __future__ import annotations

from .. import audit
from ..permissions import user_can
from ..timeutil import ist_day_bounds, now_iso, now_utc, parse_iso, to_iso
from . import tasks as T
from .errors import PermissionDenied, ValidationError

# stage: (usual days, suggested push)
SELLER_PACE = {
    "new": (2, "Make the first call — new leads go cold within days."),
    "contact_attempted": (4, "Try a different time of day, then send the call-back WhatsApp."),
    "connected": (5, "Send the detailed form or capture the basics on a call."),
    "basic_qualified": (7, "Send the detailed form with photos."),
    "form_sent": (7, "Call and fill the form together — owners often get stuck on numbers."),
    "form_received": (5, "Review the answers and fill the gaps on a call."),
    "assessment_review": (7, "Finish the checklist and send it for approval."),
    "verification_pending": (10, "Chase the missing documents (rent agreement, sales report)."),
    "listing_approved": (7, "Check the Match Desk and share the salon with suitable buyers."),
    "active_matching": (30, "Share an update with the seller and review the price if no buyer bites."),
    "negotiation": (21, "Agree the next meeting date with both sides."),
    "handover": (21, "Confirm the handover date and the open checklist items."),
}
BUYER_PACE = {
    "new": (2, "Make the first call — new leads go cold within days."),
    "contact_attempted": (4, "Try a different time of day, then send the call-back WhatsApp."),
    "connected": (5, "Capture budget, area and timeline on a call."),
    "requirements_captured": (7, "Send the detailed form or confirm the requirements."),
    "form_sent": (7, "Call and fill the form together."),
    "form_received": (5, "Review the answers and confirm the criteria."),
    "basic_qualified": (7, "Check how the money will be arranged (funding review)."),
    "funding_review": (10, "Ask for proof of funds or the loan sanction."),
    "qualified_shortlist": (14, "Send a shortlist from the Match Desk."),
    "shortlist_shared": (14, "Ask which salons they liked and book visits."),
    "viewing_offer": (14, "Ask for a decision or an offer after the visit."),
    "transaction": (30, "Agree the next step and the date with both sides."),
}
DEAL_PACE = {
    "introduced": (7, "Book the salon visit."),
    "visit_scheduled": (7, "Confirm the visit happened and record how it went."),
    "visit_completed": (7, "Ask the buyer for an offer or a clear no."),
    "offer_received": (7, "Take the offer to the seller and get an answer."),
    "negotiation": (14, "Set a date to close the gap on price or terms."),
    "due_diligence": (21, "Chase the open checklist items."),
    "agreement": (14, "Confirm the payment and signing date."),
    "handover": (21, "Fix the handover date and the staff/landlord steps."),
    "on_hold": (30, "Check whether the deal is still alive."),
}
PACE = {"seller": SELLER_PACE, "buyer": BUYER_PACE, "deal": DEAL_PACE}


def _days(since: str | None, now) -> int:
    d = parse_iso(since) if since else None
    return max(0, (now - d).days) if d else 0


def stuck(db, owner_id: int | None = None, limit: int = 200, only: tuple | None = None) -> list[dict]:
    """Stuck sellers, buyers and deals, longest-overdue (relative to usual pace) first. `only` = (kind, id)."""
    now = now_utc()
    out = []
    where, params = "", []
    if owner_id:
        where, params = " AND o.primary_owner_id = ?", [owner_id]
    if only:
        where, params = where + " AND o.id = ?", params + [only[1] if only[0] != "deal" else -1]
    rows = db.all(
        "SELECT o.id, o.kind, o.ref_code, o.stage, o.primary_owner_id, o.last_contact_at, o.created_at, "
        "o.next_action, o.next_action_due, c.full_name AS contact_name, b.name AS salon_name, u.full_name AS owner_name, "
        "(SELECT MAX(sh.at) FROM stage_history sh WHERE sh.opportunity_id = o.id) AS stage_at "
        "FROM opportunities o JOIN contacts c ON c.id = o.contact_id "
        "LEFT JOIN salon_businesses b ON b.id = o.business_id LEFT JOIN users u ON u.id = o.primary_owner_id "
        "WHERE o.status = 'active'" + where, params)
    now_s = to_iso(now)
    for r in rows:
        pace = PACE[r["kind"]].get(r["stage"])
        if not pace:
            continue
        usual, push = pace
        since = r["stage_at"] or r["created_at"]
        in_stage = _days(since, now)
        quiet = _days(max(filter(None, [r["last_contact_at"], since])), now)
        if in_stage <= usual or quiet <= usual:
            continue
        if r["next_action_due"] and r["next_action_due"] > now_s and r["stage"] == "new":
            continue  # first call simply not due yet (spread-out import schedule)
        out.append({**r, "days": in_stage, "quiet": quiet, "usual": usual,
                    "push": push, "over": in_stage / usual, "name": r["salon_name"] or r["contact_name"],
                    "stage_label": _stage_label(r["kind"], r["stage"])})
    dwhere, dparams = "", []
    if owner_id:
        dwhere, dparams = " AND d.owner_id = ?", [owner_id]
    if only:
        dwhere, dparams = dwhere + " AND d.id = ?", dparams + [only[1] if only[0] == "deal" else -1]
    for d in db.all(
            "SELECT d.id, d.ref_code, d.stage, d.owner_id AS primary_owner_id, d.created_at, u.full_name AS owner_name, "
            "b.name AS salon_name, bc.full_name AS contact_name, "
            "(SELECT MAX(e.at) FROM deal_events e WHERE e.deal_id = d.id AND e.to_stage IS NOT NULL) AS stage_at, "
            "(SELECT MAX(e.at) FROM deal_events e WHERE e.deal_id = d.id) AS last_event "
            "FROM deals d JOIN opportunities so ON so.id = d.seller_opp_id "
            "LEFT JOIN salon_businesses b ON b.id = so.business_id "
            "JOIN opportunities bo ON bo.id = d.buyer_opp_id JOIN contacts bc ON bc.id = bo.contact_id "
            "LEFT JOIN users u ON u.id = d.owner_id "
            "WHERE d.stage NOT IN ('closed_won','closed_lost','withdrawn')" + dwhere, dparams):
        pace = DEAL_PACE.get(d["stage"])
        if not pace:
            continue
        usual, push = pace
        since = d["stage_at"] or d["created_at"]
        in_stage = _days(since, now)
        quiet = _days(d["last_event"] or since, now)
        if in_stage <= usual or quiet <= usual:
            continue
        out.append({**d, "kind": "deal", "days": in_stage, "quiet": quiet, "usual": usual, "push": push,
                    "over": in_stage / usual, "name": f"{d['salon_name'] or 'Salon'} ↔ {d['contact_name']}",
                    "stage_label": _stage_label("deal", d["stage"])})
    out.sort(key=lambda x: -x["over"])
    return out[:limit]


def stuck_one(db, kind: str, rec_id: int) -> dict | None:
    """The stuck entry for one record (for the banner on its page), or None."""
    found = stuck(db, only=(kind, rec_id))
    return found[0] if found else None


def _stage_label(kind: str, stage: str) -> str:
    if kind == "deal":
        from .deals import STAGE_LABELS
    elif kind == "buyer":
        from .buyers import STAGE_LABELS
    else:
        from .sellers import STAGE_LABELS
    return STAGE_LABELS.get(stage, stage)


def stuck_sweep(db) -> str:
    """Daily: one alert per owner with stuck work and one summary per team lead — never one per record."""
    day = now_utc().strftime("%Y-%m-%d")
    items = stuck(db, limit=10_000)
    by_owner: dict[int, int] = {}
    for s in items:
        if s["primary_owner_id"]:
            by_owner[s["primary_owner_id"]] = by_owner.get(s["primary_owner_id"], 0) + 1
    sent = 0
    for uid, n in by_owner.items():
        if not db.scalar("SELECT 1 FROM notifications WHERE user_id = ? AND kind = 'stuck' AND created_at >= ?",
                         (uid, day)):
            T.notify(db, uid, "stuck", f"{n} of your records have not moved for a while — see the suggested next steps",
                     "/#stuck")
            sent += 1
    if items:
        for lead in db.all("SELECT id FROM users WHERE is_active = 1 AND role IN ('team_lead','super_admin')"):
            if not db.scalar("SELECT 1 FROM notifications WHERE user_id = ? AND kind = 'stuck_team' AND created_at >= ?",
                             (lead["id"], day)):
                T.notify(db, lead["id"], "stuck_team", f"Team: {len(items)} records are stuck", "/team#stuck")
                sent += 1
    return f"{len(items)} stuck, {sent} alerts"


# -- Workload ------------------------------------------------------------------------------------------------
def is_away(user: dict) -> bool:
    return bool(user.get("away_until") and user["away_until"] >= now_iso()[:10])


def workload(db) -> list[dict]:
    now = now_iso()
    _start, today_end = ist_day_bounds()
    users = db.all("SELECT id, full_name, role, team, away_until FROM users WHERE is_active = 1 AND role IN "
                   "('team_member','team_lead','super_admin') ORDER BY full_name")
    opp = {r["uid"]: r for r in db.all(
        "SELECT primary_owner_id AS uid, COUNT(*) AS n, SUM(CASE WHEN kind = 'seller' THEN 1 ELSE 0 END) AS sellers, "
        "SUM(CASE WHEN kind = 'buyer' THEN 1 ELSE 0 END) AS buyers FROM opportunities WHERE status <> 'closed' "
        "GROUP BY primary_owner_id")}
    tk = {r["uid"]: r for r in db.all(
        "SELECT assignee_id AS uid, COUNT(*) AS open, "
        "SUM(CASE WHEN due_at IS NOT NULL AND due_at < ? THEN 1 ELSE 0 END) AS overdue, "
        "SUM(CASE WHEN due_at IS NOT NULL AND due_at >= ? AND due_at <= ? THEN 1 ELSE 0 END) AS today, "
        "SUM(CASE WHEN task_type = 'first_contact' THEN 1 ELSE 0 END) AS first_calls "
        "FROM tasks WHERE status = 'open' GROUP BY assignee_id", (now, now, today_end))}
    out = []
    for u in users:
        o, t = opp.get(u["id"]) or {}, tk.get(u["id"]) or {}
        out.append({**u, "away": is_away(u), "records": o.get("n") or 0, "sellers": o.get("sellers") or 0,
                    "buyers": o.get("buyers") or 0, "open": t.get("open") or 0, "overdue": t.get("overdue") or 0,
                    "today": t.get("today") or 0, "first_calls": t.get("first_calls") or 0})
    active = [w for w in out if not w["away"] and w["role"] != "super_admin"]
    avg = sum(w["records"] for w in active) / len(active) if active else 0
    for w in out:
        w["heavy"] = bool(avg and w["records"] > avg * 1.4 and w["records"] - avg >= 10) or w["overdue"] >= 15
    return out


def set_away(db, actor: dict, user_id: int, until: str | None) -> None:
    if actor["id"] != user_id and not user_can(db, actor, "crm.assign"):
        raise PermissionDenied("Only a team lead can mark someone else as away.")
    if until and until < now_iso()[:10]:
        raise ValidationError({"until": "Choose today or a later date."})
    db.execute("UPDATE users SET away_until = ? WHERE id = ?", (until or None, user_id))
    audit.record(db, actor["id"], "user.away", "user", user_id, {"until": until})


def hand_over(db, actor: dict, from_id: int, to_ids: list[int], kind: str = "all", limit: int | None = None,
              preview: bool = False) -> dict:
    """Move a person's live records (with their open tasks) to colleagues, spread evenly. Preview first."""
    if not user_can(db, actor, "crm.assign"):
        raise PermissionDenied("Handing over work needs a team lead.")
    to_ids = [int(t) for t in to_ids if int(t) != from_id]
    if not to_ids:
        raise ValidationError({"to": "Choose at least one colleague to receive the work."})
    q = ("SELECT o.id, o.kind, o.ref_code FROM opportunities o WHERE o.primary_owner_id = ? AND o.status <> 'closed'"
         + ("" if kind == "all" else " AND o.kind = ?") + " ORDER BY COALESCE(o.last_contact_at, o.created_at)")
    recs = db.all(q, [from_id] + ([] if kind == "all" else [kind]))
    if limit:
        recs = recs[:limit]
    load = {t: db.scalar("SELECT COUNT(*) FROM opportunities WHERE primary_owner_id = ? AND status <> 'closed'",
                         (t,)) or 0 for t in to_ids}
    plan = []
    for r in recs:
        to = min(to_ids, key=lambda t: load[t])
        load[to] += 1
        plan.append((r, to))
    summary = {"records": len(plan), "per_person": {}}
    for _r, to in plan:
        summary["per_person"][to] = summary["per_person"].get(to, 0) + 1
    if preview:
        return summary
    now = now_iso()
    moved_tasks = 0
    for r, to in plan:
        db.execute("UPDATE opportunities SET primary_owner_id = ?, updated_at = ? WHERE id = ?", (to, now, r["id"]))
        moved_tasks += db.execute(
            "UPDATE tasks SET assignee_id = ?, updated_at = ? WHERE status = 'open' AND assignee_id = ? AND "
            "entity_type = 'opportunity' AND entity_id = ?", (to, now, from_id, r["id"])).rowcount or 0
        T.add_activity(db, actor["id"], "opportunity", r["id"], "note", "Handed over to a colleague (workload balancing)")
    for to, n in summary["per_person"].items():
        T.notify(db, to, "task_assigned", f"{n} records were handed to you (workload balancing)", "/")
    audit.record(db, actor["id"], "workload.hand_over", "user", from_id,
                 {"to": to_ids, "records": len(plan), "tasks": moved_tasks})
    summary["tasks"] = moved_tasks
    return summary


def pick_owner(db, team: str, load: dict | None = None) -> int | None:
    """Least-busy available person in a team (people marked away are skipped)."""
    load = {} if load is None else load
    people = [p for p in db.all("SELECT id, away_until FROM users WHERE is_active = 1 AND team = ? AND role IN "
                                "('team_member','team_lead') ORDER BY id", (team,)) if not is_away(p)]
    if not people:
        return None
    for p in people:
        load.setdefault(p["id"], db.scalar("SELECT COUNT(*) FROM opportunities WHERE primary_owner_id = ? AND "
                                           "status <> 'closed'", (p["id"],)) or 0)
    pid = min(people, key=lambda p: load[p["id"]])["id"]
    load[pid] += 1
    return pid
