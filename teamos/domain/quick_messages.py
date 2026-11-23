"""Ready-to-send WhatsApp follow-ups for each stage (one click after a call).

The team should never have to think "what do I write now?". After a call is logged — or any time on a record — this
suggests at most three messages that fit the record's stage and the call outcome, fully filled in (name, sender, area,
call-back time, visit time). Every suggestion goes through the same send gate as any other message (consent, opt-out,
caps, approved template, colleague warning), and the server re-builds the suggestion on send, so nothing typed in the
browser can change what is sent.
"""
from __future__ import annotations

from datetime import timedelta

from .. import messaging
from ..permissions import user_can
from ..timeutil import fmt_ist, now_iso, now_utc, to_iso
from . import inbox as I

NO_ANSWER = {"no_answer", "switched_off", "busy"}
NO_MESSAGE = {"wrong_number", "not_interested", "disqualified", "do_not_contact"}

# stage -> ordered suggestion ids. "form" = create the personal detailed-form link and send it.
SELLER = {
    "new": ["enquiry_ack", "callback_request"], "contact_attempted": ["callback_request"],
    "connected": ["call_thanks_seller", "form"], "basic_qualified": ["form", "call_thanks_seller"],
    "form_sent": ["incomplete_form", "next_steps"], "form_received": ["docs_checklist", "next_steps"],
    "assessment_review": ["docs_checklist", "next_steps"], "verification_pending": ["docs_checklist", "next_steps"],
    "listing_approved": ["listing_live", "next_steps"], "active_matching": ["listing_live", "next_steps"],
    "negotiation": ["visit_confirm", "next_steps"], "handover": ["next_steps"], "nurture": ["inactive_followup"],
}
BUYER = {
    "new": ["enquiry_ack", "callback_request"], "contact_attempted": ["callback_request"],
    "connected": ["call_thanks_buyer", "form"], "requirements_captured": ["form", "call_thanks_buyer"],
    "form_sent": ["incomplete_form", "next_steps"], "form_received": ["funds_proof", "next_steps"],
    "basic_qualified": ["funds_proof", "shortlist_update"], "funding_review": ["funds_proof", "next_steps"],
    "qualified_shortlist": ["shortlist_update", "next_steps"], "shortlist_shared": ["shortlist_update", "next_steps"],
    "viewing_offer": ["visit_confirm", "next_steps"], "transaction": ["next_steps"],
    "nurture": ["inactive_followup"], "exploring_only": ["inactive_followup"],
}
WHY = {
    "callback_request": "They didn't pick up — ask when to call",
    "callback_confirm": "Confirm the call-back time you agreed",
    "call_thanks_seller": "Thank them and reassure: confidential sale",
    "call_thanks_buyer": "Thank them and set expectations",
    "form": "Send their personal detailed form",
    "incomplete_form": "Their form isn't finished yet",
    "docs_checklist": "Documents needed for verification",
    "funds_proof": "Proof of funds before serious options",
    "listing_live": "Tell them the listing is live (confidential)",
    "shortlist_update": "Tell them the shortlist is in progress",
    "visit_confirm": "Confirm the upcoming visit",
    "next_steps": "Thank them; next steps follow",
    "enquiry_ack": "Acknowledge the enquiry before your first call",
    "inactive_followup": "Gentle check-in",
}


def _opp(db, opp_id):
    return db.one("SELECT o.*, c.full_name AS contact_name, c.do_not_contact FROM opportunities o "
                  "JOIN contacts c ON c.id = o.contact_id WHERE o.id = ?", (opp_id,))


def _callback_due(db, opp_id):
    """The call-back time just agreed = the most recently created open follow-up with a future due time."""
    return db.scalar("SELECT due_at FROM tasks WHERE entity_type = 'opportunity' AND entity_id = ? AND status = 'open' "
                     "AND due_at IS NOT NULL AND due_at >= ? ORDER BY id DESC LIMIT 1", (opp_id, now_iso()))


def _next_visit(db, opp_id):
    return db.scalar("SELECT v.scheduled_at FROM deal_visits v JOIN deals d ON d.id = v.deal_id WHERE "
                     "(d.buyer_opp_id = ? OR d.seller_opp_id = ?) AND v.status = 'scheduled' AND v.scheduled_at >= ? "
                     "ORDER BY v.scheduled_at LIMIT 1", (opp_id, opp_id, now_iso()))


def _area(db, opp_id):
    return db.scalar("SELECT l.locality FROM buyer_localities l JOIN buyer_mandates m ON m.id = l.mandate_id WHERE "
                     "m.opportunity_id = ? AND l.kind = 'preferred' ORDER BY l.rank_order LIMIT 1", (opp_id,))


def _open_form(db, opp_id):
    return db.scalar("SELECT COUNT(*) FROM form_invitations WHERE opportunity_id = ? AND template_key IN "
                     "('seller_deep','buyer_deep') AND status IN ('sent','opened','started')", (opp_id,))


def suggest(db, actor, opp_id: int, outcome: str | None = None) -> list[dict]:
    """Up to three ready messages for this record now. Each has: id, label, why, preview, blocks, warns."""
    if not user_can(db, actor, "crm.log_activity"):
        return []
    opp = _opp(db, opp_id)
    if not opp or opp["status"] == "closed" or opp["do_not_contact"] or outcome in NO_MESSAGE:
        return []
    if outcome in NO_ANSWER:
        ids = ["callback_request"]
    elif outcome == "callback_requested":
        ids = ["callback_confirm"]
    else:
        ids = list((SELLER if opp["kind"] == "seller" else BUYER).get(opp["stage"], ["next_steps"]))
        if outcome == "connected" and opp["stage"] in ("new", "contact_attempted"):
            ids = ["call_thanks_" + opp["kind"], "form"]  # just spoke to them for the first time
        if "next_steps" not in ids and opp["stage"] not in ("new", "contact_attempted"):
            ids.append("next_steps")
    first = (opp["contact_name"] or "").split()[0] if opp["contact_name"] else ""
    sender = actor["full_name"].split()[0]
    out = []
    for sid in ids:
        item = _build(db, actor, opp, sid, first, sender)
        if item:
            out.append(item)
        if len(out) == 3:
            break
    return out


def _build(db, actor, opp, sid, first, sender):
    v = {"name": first, "sender": sender}
    if sid == "form":
        if _open_form(db, opp["id"]):
            return None  # a live personal link already exists — the reminder covers it
        from . import forms as F
        key = "deep_form_link"
        expires = fmt_ist(to_iso(now_utc() + timedelta(days=7)), "%d %b %Y")
        v.update({"link": "[personal link — created when you press Send]", "expires": expires})
        blocks = F.send_blockers(db, F.load_opportunity(db, opp["id"]), "whatsapp_mock")
        label = "Detailed form (personal link)"
    else:
        key = sid
        if sid == "incomplete_form" and not _open_form(db, opp["id"]):
            return None
        if sid == "callback_confirm":
            due = _callback_due(db, opp["id"])
            if not due:
                return None
            v["when"] = fmt_ist(due, "%a %d %b, %I:%M %p")
        if sid == "visit_confirm":
            when = _next_visit(db, opp["id"])
            if not when:
                return None
            v["when"] = fmt_ist(when, "%a %d %b, %I:%M %p")
        if sid == "shortlist_update":
            v["area"] = _area(db, opp["id"]) or "your preferred areas"
        blocks = []
        label = None
    t = I.template(db, key)
    if not t:
        return None
    st = messaging.whatsapp_status(I._config())
    probs = I.template_problems(t, v, st["live"])
    gate_blocks, warns = I.check_send(db, actor, opp["contact_id"], t["category"])
    return {"id": sid, "template_key": key, "label": label or t["name"], "why": WHY.get(sid, ""),
            "preview": messaging.render(t["body"], v), "variables": v,
            "blocks": list(dict.fromkeys(blocks + probs + gate_blocks)), "warns": warns}


def send(db, actor, opp_id: int, sid: str, outcome: str | None = None, acknowledge: bool = False,
         link_base: str = "") -> dict:
    """Send one suggestion, rebuilt on the server (the browser only says which one)."""
    item = next((s for s in suggest(db, actor, opp_id, outcome) if s["id"] == sid), None)
    if not item:
        raise I.ValidationError({"send": "That message is no longer suggested for this record — refresh the page."})
    opp = _opp(db, opp_id)
    if sid == "form":
        from . import forms as F
        tk = "seller_deep" if opp["kind"] == "seller" else "buyer_deep"
        res = F.create_invitation(db, actor, opp_id, tk, "whatsapp_mock", link_base=link_base)
        comm = res.get("communication") or {}
        if comm.get("status") == "failed":
            raise I.ValidationError({"send": "Form link created but the message failed: " + (comm.get("detail") or "")})
        return {"status": comm.get("status", "provider_accepted"), "label": item["label"]}
    m = I.send(db, actor, contact_id=opp["contact_id"], opportunity_id=opp_id, template_key=item["template_key"],
               variables=item["variables"], acknowledge=acknowledge)
    return {"status": m["status"], "label": item["label"], "detail": m.get("detail")}
