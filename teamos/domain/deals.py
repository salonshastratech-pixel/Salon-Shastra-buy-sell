"""Deal room (PRD §9.2, §9.3; FR-019; UAT-17; Milestone 6).

A deal opens when an introduction is approved. It tracks visits, versioned offers, a due-diligence checklist,
agreement, handover and closure. The platform tracks milestones only — it gives no legal, accounting or
investment warranty (§9.3, Appendix F).

The agreed sale consideration belongs to the buyer and seller; it is stored for the deal record and so that a
percentage fee can be calculated in Admin Finance. It is never company revenue (FR-020).
"""
from __future__ import annotations

from .. import audit
from ..permissions import user_can
from ..timeutil import now_iso, parse_local_input
from . import matching as M
from . import tasks as T
from .common import parse_inr
from .errors import PermissionDenied, ValidationError

STAGES = [("introduced", "Introduced"), ("visit_scheduled", "Visit scheduled"), ("visit_completed", "Visit completed"),
          ("offer_received", "Offer received"), ("negotiation", "Negotiation"), ("due_diligence", "Due diligence"),
          ("agreement", "Agreement / payment"), ("handover", "Handover"), ("closed_won", "Closed won")]
OTHER = [("on_hold", "On hold"), ("withdrawn", "Withdrawn"), ("closed_lost", "Closed lost")]
STAGE_LABELS = dict(STAGES + OTHER)
OPEN_STAGES = [k for k, _ in STAGES if k != "closed_won"] + ["on_hold"]

CHECKLIST = [  # §9.3 due diligence
    ("ownership", "Ownership & signing authority"), ("lease", "Lease and transfer rights (landlord consent)"),
    ("financials", "Financials checked against records"), ("liabilities", "Loans, dues and liabilities"),
    ("prepaid", "Customer prepaid balances / memberships"), ("equipment", "Equipment & inventory list"),
    ("employees", "Employee matters (dues, continuity)"), ("tax_legal", "Tax / legal review (by professionals)"),
    ("brand", "Brand / franchise rights"),
]
CHECK_STATUSES = [("pending", "Pending"), ("in_progress", "In progress"), ("reviewed", "Reviewed"), ("issue", "Issue found"),
                  ("not_applicable", "Not applicable")]
LOST_REASONS = [("price", "Price / valuation gap"), ("financing", "Buyer financing"), ("lease", "Lease / landlord"),
                ("diligence", "Diligence issue"), ("seller_withdrew", "Seller withdrew"), ("buyer_withdrew", "Buyer withdrew"),
                ("competition", "Sold elsewhere"), ("other", "Other")]


def ref(deal_id: int) -> str:
    return f"DL-{deal_id:05d}"


DEAL_SELECT = (
    "SELECT d.*, bo.ref_code AS buyer_ref, bc.full_name AS buyer_name, bo.primary_owner_id AS buyer_owner_id, "
    "so.ref_code AS seller_ref, sb.name AS salon_name, sb.locality AS salon_locality, so.primary_owner_id AS seller_owner_id, "
    "u.full_name AS owner_name FROM deals d JOIN opportunities bo ON bo.id = d.buyer_opp_id "
    "JOIN contacts bc ON bc.id = bo.contact_id JOIN opportunities so ON so.id = d.seller_opp_id "
    "LEFT JOIN salon_businesses sb ON sb.id = so.business_id LEFT JOIN users u ON u.id = d.owner_id ")


def load(db, deal_id: int) -> dict | None:
    return db.one(DEAL_SELECT + "WHERE d.id = ?", (deal_id,))


def _event(db, deal_id, event, actor_id, from_stage=None, to_stage=None, body=None):
    db.insert("deal_events", {"deal_id": deal_id, "event": event, "from_stage": from_stage, "to_stage": to_stage,
                              "body": body, "actor_id": actor_id, "at": now_iso()})


def _log_both(db, actor_id, d, text):
    for oid in (d["buyer_opp_id"], d["seller_opp_id"]):
        T.add_activity(db, actor_id, "opportunity", oid, "system", f"{d['ref_code']}: {text}")


def _close_tasks(db, actor_id, deal_id: int, title_prefix: str, outcome: str) -> None:
    """Close the deal's open tasks that this action has just completed (no stale to-dos left behind)."""
    now = now_iso()
    for t in db.all("SELECT id FROM tasks WHERE entity_type = 'deal' AND entity_id = ? AND status = 'open' "
                    "AND title LIKE ?", (deal_id, title_prefix + "%")):
        db.update("tasks", t["id"], {"status": "done", "outcome": outcome, "completed_at": now,
                                     "completed_by": actor_id, "updated_at": now})
        db.insert("task_events", {"task_id": t["id"], "event": "completed", "detail": outcome, "actor_id": actor_id,
                                  "at": now})


def create_from_match(db, actor, match_id: int) -> int:
    existing = db.scalar("SELECT id FROM deals WHERE match_id = ?", (match_id,))
    if existing:
        return existing
    c = M._cand(db, match_id)
    now = now_iso()
    synthetic = db.scalar("SELECT is_synthetic FROM opportunities WHERE id = ?", (c["buyer_opp_id"],)) or 0
    did = db.insert("deals", {"match_id": match_id, "buyer_opp_id": c["buyer_opp_id"], "seller_opp_id": c["seller_opp_id"],
                              "stage": "introduced", "owner_id": c["match_owner_id"] or c["seller_owner_id"] or actor["id"],
                              "is_synthetic": synthetic, "created_at": now, "updated_at": now})
    db.update("deals", did, {"ref_code": ref(did)})
    for k, _ in CHECKLIST:
        db.execute("INSERT INTO deal_checklist (deal_id, item_key, status, updated_at) VALUES (?, ?, 'pending', ?)",
                   (did, k, now))
    _event(db, did, "opened", actor["id"], to_stage="introduced", body="Deal opened after approved introduction")
    T.create_task(db, actor, title=f"Arrange a site visit for {c['buyer_name']} at {c['salon_name']}",
                  assignee_id=c["match_owner_id"] or c["seller_owner_id"] or actor["id"],
                  due_at=T.first_contact_due(48 * 60), task_type="visit", priority="high", entity_type="deal",
                  entity_id=did)
    return did


def checklist(db, deal_id):
    rows = {r["item_key"]: r for r in db.all("SELECT c.*, u.full_name AS by_name FROM deal_checklist c "
                                             "LEFT JOIN users u ON u.id = c.updated_by WHERE deal_id = ?", (deal_id,))}
    return [{"key": k, "label": label, **rows.get(k, {"status": "pending"})} for k, label in CHECKLIST]


def offers(db, deal_id):
    return db.all("SELECT o.*, u.full_name AS by_name FROM deal_offers o LEFT JOIN users u ON u.id = o.created_by "
                  "WHERE deal_id = ? ORDER BY version DESC", (deal_id,))


def visits(db, deal_id):
    return db.all("SELECT * FROM deal_visits WHERE deal_id = ? ORDER BY scheduled_at DESC", (deal_id,))


def transition_blockers(db, actor, d, to) -> str | None:
    if to not in STAGE_LABELS:
        return "Unknown stage"
    if d["stage"] in ("closed_won", "closed_lost", "withdrawn") and not user_can(db, actor, "listing.approve"):
        return "Closed deals can only be reopened by an approver"
    if to == "visit_scheduled" and not db.scalar("SELECT COUNT(*) FROM deal_visits WHERE deal_id = ? AND status = "
                                                 "'scheduled'", (d["id"],)):
        return "Schedule a visit first"
    if to == "visit_completed" and not db.scalar("SELECT COUNT(*) FROM deal_visits WHERE deal_id = ? AND status = "
                                                 "'completed'", (d["id"],)):
        return "Mark a visit as completed first"
    if to in ("offer_received", "negotiation") and not db.scalar("SELECT COUNT(*) FROM deal_offers WHERE deal_id = ?",
                                                                 (d["id"],)):
        return "Record an offer first"
    if to in ("agreement", "handover", "closed_won"):
        if not db.scalar("SELECT COUNT(*) FROM deal_offers WHERE deal_id = ? AND status = 'accepted'", (d["id"],)):
            return "An offer must be accepted first"
    if to in ("handover", "closed_won"):
        open_items = [c["label"] for c in checklist(db, d["id"]) if c["status"] not in ("reviewed", "not_applicable")]
        if open_items:
            return "Due diligence still open: " + ", ".join(open_items[:3]) + ("…" if len(open_items) > 3 else "")
    if to == "closed_won":
        if not user_can(db, actor, "listing.approve"):
            return "Only an approver can close a deal as won"
        if not d.get("handover_date"):
            return "Record the handover date first"
    return None


def change_stage(db, actor, deal_id: int, to: str, note: str | None = None, reason: str | None = None,
                 lessons: str | None = None) -> None:
    if not user_can(db, actor, "stage.operational"):
        raise PermissionDenied("stage.operational")
    d = load(db, deal_id)
    if not d:
        raise ValidationError("Deal not found.")
    why = transition_blockers(db, actor, d, to)
    if why:
        audit.record(db, actor["id"], "deal.stage", "deal", deal_id, {"to": to, "blocked": why}, outcome="denied")
        raise ValidationError({"stage": why})
    if to == "closed_lost" and (reason not in dict(LOST_REASONS) or not (lessons or "").strip()):
        raise ValidationError({"reason": "A closed-lost deal needs a reason and a short lesson learned (§9.3)."})
    now = now_iso()
    with db:
        upd = {"stage": to, "updated_at": now}
        if to in ("closed_won", "closed_lost", "withdrawn"):
            upd.update(closed_at=now, closed_reason=dict(LOST_REASONS).get(reason) if reason else None,
                       lessons=(lessons or "").strip() or None)
            T.cancel_open_tasks(db, actor["id"], "deal", deal_id, f"Deal {STAGE_LABELS[to].lower()}")
        db.update("deals", deal_id, upd)
        _event(db, deal_id, "stage", actor["id"], d["stage"], to, (note or "").strip() or None)
        _log_both(db, actor["id"], d, f"{STAGE_LABELS[d['stage']]} → {STAGE_LABELS[to]}" + (f" — {note.strip()}" if note and
                                                                                            note.strip() else ""))
        if to == "closed_won":
            for oid in (d["buyer_opp_id"], d["seller_opp_id"]):
                db.update("opportunities", oid, {"stage": "closed_won", "status": "closed",
                                                 "closed_reason": f"Deal {d['ref_code']} closed", "updated_at": now})
                db.insert("stage_history", {"opportunity_id": oid, "to_stage": "closed_won", "actor_id": actor["id"],
                                            "reason": f"Deal {d['ref_code']} closed", "at": now})
                T.cancel_open_tasks(db, actor["id"], "opportunity", oid, f"Deal {d['ref_code']} closed")
            for a in db.all("SELECT id FROM users WHERE is_active = 1 AND role IN ('super_admin','finance')"):
                T.notify(db, a["id"], "finance", f"{d['ref_code']} closed — check fees earned", "/admin/finance")
        audit.record(db, actor["id"], "deal.stage", "deal", deal_id, {"from": d["stage"], "to": to, "reason": reason})


def schedule_visit(db, actor, deal_id: int, when: str | None, attendees: str | None) -> int:
    if not user_can(db, actor, "crm.log_activity"):
        raise PermissionDenied("crm.log_activity")
    if not when:
        raise ValidationError({"when": "Choose the visit date and time."})
    d = load(db, deal_id)
    now = now_iso()
    with db:
        tid = T.create_task(db, actor, title=f"Site visit: {d['buyer_name']} at {d['salon_name']}",
                            assignee_id=d["owner_id"] or actor["id"], due_at=when, task_type="visit", priority="high",
                            entity_type="deal", entity_id=deal_id)
        vid = db.insert("deal_visits", {"deal_id": deal_id, "scheduled_at": when, "attendees": (attendees or "").strip()
                                        or None, "status": "scheduled", "task_id": tid, "created_by": actor["id"],
                                        "created_at": now, "updated_at": now})
        _event(db, deal_id, "visit", actor["id"], body="Visit scheduled")
        _close_tasks(db, actor["id"], deal_id, "Arrange a site visit", "Visit scheduled")
        if d["stage"] == "introduced":
            db.update("deals", deal_id, {"stage": "visit_scheduled", "updated_at": now})
            _event(db, deal_id, "stage", actor["id"], "introduced", "visit_scheduled", "Automatic: visit scheduled")
        _log_both(db, actor["id"], d, "Site visit scheduled")
    return vid


def record_visit(db, actor, visit_id: int, status: str, outcome: str | None, feedback: str | None) -> None:
    if not user_can(db, actor, "crm.log_activity"):
        raise PermissionDenied("crm.log_activity")
    if status not in ("completed", "cancelled", "no_show"):
        raise ValidationError({"status": "Choose what happened."})
    v = db.one("SELECT * FROM deal_visits WHERE id = ?", (visit_id,))
    if not v:
        raise ValidationError("Visit not found.")
    if status == "completed" and not (outcome or "").strip():
        raise ValidationError({"outcome": "Note what happened at the visit."})
    d = load(db, v["deal_id"])
    now = now_iso()
    with db:
        db.update("deal_visits", visit_id, {"status": status, "outcome_note": (outcome or "").strip() or None,
                                            "buyer_feedback": (feedback or "").strip() or None, "updated_at": now})
        task = db.one("SELECT * FROM tasks WHERE id = ? AND status = 'open'", (v["task_id"],)) if v["task_id"] else None
        if task:
            db.update("tasks", task["id"], {"status": "done", "outcome": f"Visit {status.replace('_', ' ')}",
                                            "completed_at": now, "completed_by": actor["id"], "updated_at": now})
        _event(db, d["id"], "visit", actor["id"], body=f"Visit {status.replace('_', ' ')}" +
               (f": {outcome.strip()}" if outcome and outcome.strip() else ""))
        if status == "completed" and d["stage"] in ("introduced", "visit_scheduled"):
            db.update("deals", d["id"], {"stage": "visit_completed", "updated_at": now})
            _event(db, d["id"], "stage", actor["id"], d["stage"], "visit_completed", "Automatic: visit completed")
        _log_both(db, actor["id"], d, f"Visit {status.replace('_', ' ')}")


def add_offer(db, actor, deal_id: int, f: dict) -> int:
    """UAT-17: a new offer or counter-offer is a NEW version; previous versions are kept unchanged."""
    if not user_can(db, actor, "crm.log_activity"):
        raise PermissionDenied("crm.log_activity")
    d = load(db, deal_id)
    if f.get("from_party") not in ("buyer", "seller"):
        raise ValidationError({"from_party": "Who made this offer?"})
    try:
        amount = parse_inr(f.get("amount"))
        wc = parse_inr(f.get("working_capital")) if f.get("working_capital") else None
    except ValueError as e:
        raise ValidationError({"amount": str(e)})
    if not amount:
        raise ValidationError({"amount": "Enter the offer amount."})
    valid_until = (f.get("valid_until") or "").strip() or None
    response_due = None
    if valid_until:  # a calendar date; the response task is due at 6 pm IST that day
        try:
            response_due = parse_local_input(valid_until[:10] + "T18:00")
        except ValueError:
            raise ValidationError({"valid_until": "Use a valid date."})
        valid_until = valid_until[:10]
    now = now_iso()
    with db:
        version = (db.scalar("SELECT MAX(version) FROM deal_offers WHERE deal_id = ?", (deal_id,)) or 0) + 1
        db.execute("UPDATE deal_offers SET status = 'countered' WHERE deal_id = ? AND status = 'open'", (deal_id,))
        _close_tasks(db, actor["id"], deal_id, "Respond to offer", f"Responded with offer v{version}")
        oid = db.insert("deal_offers", {
            "deal_id": deal_id, "version": version, "from_party": f["from_party"], "amount": amount,
            "deposit_treatment": f.get("deposit_treatment") or None, "stock_included": f.get("stock_included") or None,
            "working_capital": wc, "contingencies": (f.get("contingencies") or "").strip() or None,
            "financing": (f.get("financing") or "").strip() or None, "valid_until": valid_until,
            "status": "open", "note": (f.get("note") or "").strip() or None, "created_by": actor["id"], "created_at": now})
        _event(db, deal_id, "offer", actor["id"], body=f"Offer v{version} from the {f['from_party']}")
        if d["stage"] in ("introduced", "visit_scheduled", "visit_completed"):
            db.update("deals", deal_id, {"stage": "offer_received", "updated_at": now})
            _event(db, deal_id, "stage", actor["id"], d["stage"], "offer_received", "Automatic: offer recorded")
        elif d["stage"] == "offer_received" and version > 1:
            db.update("deals", deal_id, {"stage": "negotiation", "updated_at": now})
            _event(db, deal_id, "stage", actor["id"], d["stage"], "negotiation", "Automatic: counter-offer")
        other_owner = d["buyer_owner_id"] if f["from_party"] == "seller" else d["seller_owner_id"]
        T.create_task(db, actor, title=f"Respond to offer v{version} on {d['ref_code']}",
                      assignee_id=other_owner or d["owner_id"] or actor["id"], due_at=response_due or
                      T.first_contact_due(48 * 60), task_type="follow_up", priority="high", entity_type="deal",
                      entity_id=deal_id)
        _log_both(db, actor["id"], d, f"Offer v{version} recorded (from the {f['from_party']})")
        audit.record(db, actor["id"], "deal.offer", "deal", deal_id, {"version": version, "from": f["from_party"]})
    return oid


def set_offer_status(db, actor, offer_id: int, status: str) -> None:
    if not user_can(db, actor, "crm.log_activity"):
        raise PermissionDenied("crm.log_activity")
    if status not in ("accepted", "rejected", "withdrawn", "expired"):
        raise ValidationError("Unknown offer status.")
    o = db.one("SELECT * FROM deal_offers WHERE id = ?", (offer_id,))
    if not o or o["status"] != "open":
        raise ValidationError("Only the open (latest) offer can be accepted or closed.")
    d = load(db, o["deal_id"])
    now = now_iso()
    with db:
        db.update("deal_offers", offer_id, {"status": status})
        if status == "accepted":
            db.update("deals", d["id"], {"agreed_consideration": o["amount"], "updated_at": now})
            if d["stage"] in ("offer_received", "negotiation", "visit_completed", "introduced", "visit_scheduled"):
                db.update("deals", d["id"], {"stage": "due_diligence"})
                _event(db, d["id"], "stage", actor["id"], d["stage"], "due_diligence", "Automatic: offer accepted")
        _event(db, d["id"], "offer", actor["id"], body=f"Offer v{o['version']} {status}")
        _close_tasks(db, actor["id"], d["id"], f"Respond to offer v{o['version']}", f"Offer {status}")
        _log_both(db, actor["id"], d, f"Offer v{o['version']} {status}")
        audit.record(db, actor["id"], "deal.offer_status", "deal", d["id"], {"version": o["version"], "status": status})


def set_check(db, actor, deal_id: int, key: str, status: str, note: str | None) -> None:
    if not user_can(db, actor, "crm.edit"):
        raise PermissionDenied("crm.edit")
    if key not in dict(CHECKLIST) or status not in dict(CHECK_STATUSES):
        raise ValidationError("Unknown checklist item or status.")
    if status == "issue" and not (note or "").strip():
        raise ValidationError({"note": "Describe the issue."})
    with db:
        db.execute("UPDATE deal_checklist SET status = ?, note = ?, updated_by = ?, updated_at = ? WHERE deal_id = ? "
                   "AND item_key = ?", (status, (note or "").strip() or None, actor["id"], now_iso(), deal_id, key))
        _event(db, deal_id, "diligence", actor["id"], body=f"{dict(CHECKLIST)[key]}: {dict(CHECK_STATUSES)[status]}"
               + (f" — {note.strip()}" if note and note.strip() else ""))


def set_dates(db, actor, deal_id: int, agreement_date: str | None, handover_date: str | None) -> None:
    if not user_can(db, actor, "crm.edit"):
        raise PermissionDenied("crm.edit")
    with db:
        db.update("deals", deal_id, {"agreement_date": agreement_date or None, "handover_date": handover_date or None,
                                     "updated_at": now_iso()})
        _event(db, deal_id, "dates", actor["id"], body=f"Agreement {agreement_date or '—'} · handover {handover_date or '—'}")


def add_note(db, actor, deal_id: int, body: str) -> None:
    if not user_can(db, actor, "crm.log_activity"):
        raise PermissionDenied("crm.log_activity")
    if not (body or "").strip():
        raise ValidationError({"body": "Write a note."})
    d = load(db, deal_id)
    with db:
        _event(db, deal_id, "note", actor["id"], body=body.strip()[:2000])
        for u in db.all("SELECT id, full_name FROM users WHERE is_active = 1"):
            if f"@{u['full_name'].split()[0].lower()}" in body.lower() and u["id"] != actor["id"]:
                T.notify(db, u["id"], "mention", f"{actor['full_name']} mentioned you on {d['ref_code']}",
                         f"/deals/{deal_id}")


def commercial_status(db, deal_id: int) -> str:
    """What ordinary staff may know about company fees on a deal — never amounts (§11.5, UAT-47)."""
    n = db.scalar("SELECT COUNT(*) FROM fee_agreements WHERE deal_id = ? AND status = 'active'", (deal_id,))
    return "Commercial terms recorded (visible to Admin Finance only)" if n else "Commercial verification pending"
