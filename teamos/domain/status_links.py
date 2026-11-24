"""Milestone 10 — a private progress link for the customer ("where do things stand?"), no login needed.

What the customer sees (D-69): their first name and reference, the steps of their journey in plain words with the
current one marked, what we still need from them, who their contact is (first name) and roughly when we will be back
in touch. Never: internal notes, scores, prices we were told, buyer or seller identities, match counts, documents.
The secret is stored only as a hash; one live link per record; it expires after 60 days and can be switched off.
Closed or do-not-contact records show nothing beyond "no longer active".
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta

from .. import audit
from ..permissions import user_can
from ..timeutil import IST, now_iso, now_utc, parse_iso, to_iso
from . import collect
from . import tasks as T
from .errors import PermissionDenied, ValidationError

LINK_DAYS = 60

SELLER_STEPS = [
    ("We received your enquiry", {"new", "contact_attempted"}),
    ("We spoke with you", {"connected", "basic_qualified"}),
    ("Your salon details", {"form_sent", "form_received"}),
    ("Our review", {"assessment_review", "verification_pending"}),
    ("Listed confidentially", {"listing_approved"}),
    ("Introductions to buyers", {"active_matching"}),
    ("Visits and offer", {"negotiation"}),
    ("Handover", {"handover", "closed_won"}),
]
BUYER_STEPS = [
    ("We received your enquiry", {"new", "contact_attempted"}),
    ("We spoke with you", {"connected", "requirements_captured"}),
    ("Your requirements", {"form_sent", "form_received", "basic_qualified"}),
    ("Our review", {"funding_review"}),
    ("Finding salons for you", {"qualified_shortlist"}),
    ("Shortlist shared", {"shortlist_shared"}),
    ("Visits and offer", {"viewing_offer"}),
    ("Completing the purchase", {"transaction", "closed_won"}),
]
STEP_NOTES = {
    "We received your enquiry": "Your contact will call you soon.",
    "We spoke with you": "Thank you for your time on the call.",
    "Your salon details": "The more we know, the better we can present your salon.",
    "Your requirements": "The more we know, the better the salons we suggest.",
    "Our review": "We are checking the details so that buyers can trust them.",
    "Listed confidentially": "Your salon is listed without your name or address.",
    "Introductions to buyers": "We introduce serious buyers only with your permission.",
    "Finding salons for you": "We are looking for salons that fit your budget and areas.",
    "Shortlist shared": "Tell your contact which salons you would like to know more about.",
    "Visits and offer": "Visits and offers are arranged with both sides' agreement.",
    "Handover": "We help with the handover until it is complete.",
    "Completing the purchase": "We help with the paperwork until it is complete.",
}


CUSTOMER_LABELS = {  # plain wording for the customer (field names are written for staff)
    'asking_price': 'Your asking price',
    'monthly_rent': 'Monthly rent',
    'maintenance_monthly': 'Monthly maintenance charges',
    'rent_deposit': 'Rent deposit',
    'deposit_treatment': 'Whether the deposit is part of the price',
    'lease_expiry': 'When the rent agreement ends',
    'lease_transfer_status': 'Whether the rent agreement can be transferred',
    'floor_level': 'Which floor the salon is on',
    'area_type': 'Type of area (main road, residential, mall…)',
    'parking': 'Parking for customers',
    'carpet_area_sqft': 'Salon size (sq ft)',
    'chairs': 'Number of chairs',
    'gender_format': 'Who the salon serves (men, women or unisex)',
    'positioning': 'Price level of the salon (budget, mid, premium)',
    'started_year': 'Year the salon started',
    'avg_monthly_sales': 'Average monthly sales',
    'avg_monthly_expenses': 'Average monthly costs',
    'staff_headcount': 'Number of staff',
    'staff_continuity': 'Whether staff will stay after the sale',
    'transfer_authority': 'Whether you can sign the sale yourself',
    'negotiable': 'Whether the price is negotiable',
    'equipment_condition': 'Condition of the equipment',
    'address_line': 'Salon address (kept private)',
    'maps_link': 'Google Maps location (kept private)',
    'all_in_max': 'Your total budget (everything included)',
    'need_type': 'What you are looking for',
    'decision_maker': 'Who will make the final decision',
    'cash_ready': 'How much of the money is ready',
    'finance_dependence': 'Whether you need a loan',
    'preferred_city': 'City you prefer',
    'max_monthly_rent': 'Highest monthly rent you would pay',
    'chairs_min': 'Smallest number of chairs',
    'parking_need': 'Whether you need parking',
    'purchase_window': 'When you want to buy',
    'min_lease_years_left': 'Years left on the rent agreement you need',
    'involvement': 'How involved you will be in running it',
}


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def current(db, opp_id: int) -> dict | None:
    return db.one("SELECT * FROM status_links WHERE opportunity_id = ? AND revoked_at IS NULL AND expires_at > ? "
                  "ORDER BY id DESC LIMIT 1", (opp_id, now_iso()))


def create(db, actor: dict, opp: dict) -> str:
    """A new link for this record (any older one stops working). Returns the secret once."""
    if not user_can(db, actor, "crm.log_activity"):
        raise PermissionDenied("crm.log_activity")
    if opp["status"] == "closed" or opp.get("do_not_contact") or opp["stage"] == "do_not_contact":
        raise ValidationError("This record is closed or marked do-not-contact, so no progress link can be shared.")
    now = now_utc()
    with db:
        db.execute("UPDATE status_links SET revoked_at = ? WHERE opportunity_id = ? AND revoked_at IS NULL",
                   (to_iso(now), opp["id"]))
        token = secrets.token_urlsafe(18)
        db.insert("status_links", {"opportunity_id": opp["id"], "token_hash": _hash(token), "created_by": actor["id"],
                                   "created_at": to_iso(now), "expires_at": to_iso(now + timedelta(days=LINK_DAYS))})
        T.add_activity(db, actor["id"], "opportunity", opp["id"], "note",
                       "Progress link created for the customer (shows stage and what we still need; no prices, "
                       "names or documents).")
        audit.record(db, actor["id"], "status_link.created", "opportunity", opp["id"], {})
    return token


def revoke(db, actor: dict, opp: dict) -> None:
    if not user_can(db, actor, "crm.log_activity"):
        raise PermissionDenied("crm.log_activity")
    db.execute("UPDATE status_links SET revoked_at = ? WHERE opportunity_id = ? AND revoked_at IS NULL",
               (now_iso(), opp["id"]))
    audit.record(db, actor["id"], "status_link.revoked", "opportunity", opp["id"], {})


def resolve(db, token: str) -> dict | None:
    """The live link for this secret (counts the visit), or None."""
    if not token or len(token) > 100:
        return None
    link = db.one("SELECT * FROM status_links WHERE token_hash = ? AND revoked_at IS NULL AND expires_at > ?",
                  (_hash(token), now_iso()))
    if link:
        db.execute("UPDATE status_links SET opens = opens + 1, last_opened_at = ? WHERE id = ?",
                   (now_iso(), link["id"]))
    return link


def load(db, opp_id: int) -> dict | None:
    return db.one("SELECT o.*, c.full_name AS contact_name, c.do_not_contact, u.full_name AS owner_name, "
                  "l.id AS listing_id, m.id AS mandate_id FROM opportunities o JOIN contacts c ON c.id = o.contact_id "
                  "LEFT JOIN users u ON u.id = o.primary_owner_id LEFT JOIN seller_listings l ON l.opportunity_id = o.id "
                  "LEFT JOIN buyer_mandates m ON m.opportunity_id = o.id WHERE o.id = ?", (opp_id,))


def view(db, opp: dict) -> dict:
    """Everything the customer page shows — and nothing else."""
    if opp["status"] == "closed" and opp["stage"] != "closed_won" or opp["do_not_contact"] or \
            opp["stage"] == "do_not_contact":
        return {"state": "inactive"}
    steps_def = SELLER_STEPS if opp["kind"] == "seller" else BUYER_STEPS
    at = next((i for i, (_, codes) in enumerate(steps_def) if opp["stage"] in codes), None)
    state = "done" if opp["stage"] == "closed_won" else "paused" if at is None else "active"
    steps = [{"label": label, "state": ("done" if state == "done" or (at is not None and i < at) else
                                        "now" if i == at else "next"), "note": STEP_NOTES.get(label, "")}
             for i, (label, _) in enumerate(steps_def)]
    need = []
    if state == "active":
        g = collect.gaps(db, opp, limit=6)
        need = [CUSTOMER_LABELS.get(i["key"], i["label"]) for i in g["items"]]
        if g.get("photos"):
            need.append("Photos of your salon")
        need += [label for _, label in g.get("docs", [])]
        need += g.get("extra", [])[:2]
    open_form = db.scalar("SELECT COUNT(*) FROM form_invitations WHERE opportunity_id = ? AND status IN "
                          "('sent','opened','started') AND expires_at > ?", (opp["id"], now_iso()))
    next_contact = None
    if opp["next_action_due"] and state == "active":
        d = parse_iso(opp["next_action_due"])
        if d and d > now_utc():
            next_contact = d.astimezone(IST)
    return {"state": state, "first_name": (opp["contact_name"] or "").split()[0] if opp["contact_name"] else "",
            "ref": opp["ref_code"], "kind": opp["kind"], "steps": steps, "need": need[:8], "open_form": open_form,
            "contact": (opp["owner_name"] or "").split()[0] if opp["owner_name"] else "",
            "next_contact": next_contact}
