"""Confidential teasers and introductions (PRD §9.1, §9.2; FR-016, FR-018; UAT-15, UAT-50; Milestone 6).

Teaser rules:
* Only WHITELISTED, non-identifying fields can ever appear (name, address, phone, franchise brand, private notes,
  lowest price and documents never can). Sizes and money are shown in bands, not exact figures.
* A teaser version needs (1) the seller's approval of exactly those content categories, then (2) a reviewer's
  approval. Its content is frozen at approval: later listing edits never leak into an approved teaser.
* It is shared with a buyer only through an expiring link, only for a match approved to approach, and every
  share is logged.

Introduction rules (identity release):
* Requested after interest is confirmed; approved only by an approver (Super Admin / granted `listing.approve`);
  requires both parties' recorded consent to share identity and an approved teaser already shared with the buyer.
* The approval records exactly which details went to whom and the consent rows (with versions) relied on.
"""
from __future__ import annotations

import hashlib
import json
import secrets
from datetime import timedelta

from .. import audit, messaging
from ..permissions import user_can
from ..timeutil import fmt_ist, now_iso, now_utc, to_iso
from . import matching as M
from . import sellers as S
from . import tasks as T
from .common import format_inr
from .errors import PermissionDenied, ValidationError
from .seller_fields import BY_KEY as SF

TEASER_SHARE_DAYS = 14
IDENTITY_CONSENT_VERSION = "identity-share-v0.1"
TEASER_CONSENT_VERSION = "teaser-content-v0.1"


def _band_inr(v, step):
    if v is None:
        return None
    lo = (v // step) * step
    return f"{format_inr(lo)} – {format_inr(lo + step)}"


def _years_left(expiry):
    yl = M._years_left(expiry)
    return None if yl is None else (f"about {round(yl)} years" if yl >= 1.5 else "under 2 years")


# key: (label, extractor(opp, listing) -> display or None, default-on)
TEASER_FIELDS = {
    "area": ("Area", lambda o, l: f"{o['locality']}, {o['city']}" if o.get("locality") else o.get("city"), True),
    "format": ("Format", lambda o, l: SF["gender_format"].options and dict(SF["gender_format"].options).get(l.get("gender_format")), True),
    "positioning": ("Positioning", lambda o, l: dict(SF["positioning"].options).get(l.get("positioning")), True),
    "model": ("Independent or franchise", lambda o, l: dict(SF["business_model"].options).get(l.get("business_model")), True),
    "size": ("Size", lambda o, l: " · ".join(x for x in [
        f"{l['chairs']} chairs" if l.get("chairs") is not None else None,
        f"about {round(l['carpet_area_sqft'], -2):,} sq ft" if l.get("carpet_area_sqft") else None] if x) or None, True),
    "years": ("Years in operation", lambda o, l: f"{l['operating_years']}+ years" if l.get("operating_years") else None, True),
    "asking": ("Indicative asking price", lambda o, l: format_inr(l["asking_price"]) if l.get("asking_price") else None, True),
    "sales_band": ("Declared monthly sales (band)", lambda o, l: _band_inr(l.get("avg_monthly_sales"), 2_00_000), False),
    "rent_band": ("Monthly rent (band)", lambda o, l: _band_inr(l.get("monthly_rent"), 25_000), False),
    "lease": ("Lease remaining", lambda o, l: _years_left(l.get("lease_expiry")), False),
    "staff": ("Team size", lambda o, l: f"{l['staff_headcount']} staff" if l.get("staff_headcount") else None, False),
    "structure": ("Deal structure", lambda o, l: dict(SF["sale_structure"].options).get(l.get("sale_structure")), True),
    "reason": ("Reason for sale", lambda o, l: dict(SF["sale_reason"].options).get(l.get("sale_reason"))
               if l.get("sale_reason") not in (None, "financial_pressure", "personal") else None, False),
}
NEVER_IN_TEASER = ["Owner name", "Phone / email", "Salon name", "Street address & landmark", "Franchise brand",
                   "Lowest acceptable price", "Private sale reason notes", "Documents & exact financial statements"]
STATUS = {"draft": "Draft", "seller_approved": "Seller approved — awaiting reviewer", "approved": "Approved",
          "retired": "Retired"}


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def build_content(db, seller_opp_id: int, field_keys: list[str], headline: str | None, highlights: str | None) -> dict:
    o = S.load(db, seller_opp_id)
    listing = S.load_listing(db, o["listing_id"])
    rows = []
    for k in field_keys:
        if k not in TEASER_FIELDS:
            continue
        label, fn, _ = TEASER_FIELDS[k]
        try:
            v = fn(o, listing)
        except (TypeError, ValueError):
            v = None
        if v:
            rows.append({"key": k, "label": label, "value": v})
    return {"headline": (headline or "").strip()[:140], "highlights": (highlights or "").strip()[:1200],
            "rows": rows, "listing_version": listing["data_version"]}


def teasers_for(db, seller_opp_id: int) -> list[dict]:
    rows = db.all("SELECT t.*, u.full_name AS approver_name, c.full_name AS consent_by_name FROM teasers t "
                  "LEFT JOIN users u ON u.id = t.approved_by LEFT JOIN users c ON c.id = t.seller_consent_by "
                  "WHERE t.seller_opp_id = ? ORDER BY t.version DESC", (seller_opp_id,))
    for r in rows:
        r["fields"] = json.loads(r["field_keys"])
        r["content_d"] = json.loads(r["content"]) if r["content"] else None
    return rows


def approved_teaser(db, seller_opp_id: int) -> dict | None:
    return db.one("SELECT * FROM teasers WHERE seller_opp_id = ? AND status = 'approved' ORDER BY version DESC LIMIT 1",
                  (seller_opp_id,))


def save_draft(db, actor, seller_opp_id: int, field_keys: list[str], headline: str, highlights: str) -> int:
    if not user_can(db, actor, "crm.edit"):
        raise PermissionDenied("crm.edit")
    keys = [k for k in field_keys if k in TEASER_FIELDS]
    if not keys:
        raise ValidationError({"fields": "Pick at least one item to show in the teaser."})
    if not (headline or "").strip():
        raise ValidationError({"headline": "Write a short, anonymous headline (no salon name)."})
    o = S.load(db, seller_opp_id)
    banned = [w for w in (o["salon_name"], o["contact_name"]) if w and w.lower() in
              f"{headline} {highlights}".lower()]
    if banned:
        raise ValidationError({"headline": "The teaser must stay anonymous — remove: " + ", ".join(banned)})
    content = build_content(db, seller_opp_id, keys, headline, highlights)
    now = now_iso()
    with db:
        draft = db.one("SELECT id FROM teasers WHERE seller_opp_id = ? AND status = 'draft'", (seller_opp_id,))
        payload = {"headline": content["headline"], "highlights": content["highlights"], "field_keys": json.dumps(keys),
                   "content": json.dumps(content), "listing_version": content["listing_version"], "updated_at": now}
        if draft:
            db.update("teasers", draft["id"], payload)
            tid = draft["id"]
        else:
            v = (db.scalar("SELECT MAX(version) FROM teasers WHERE seller_opp_id = ?", (seller_opp_id,)) or 0) + 1
            tid = db.insert("teasers", {**payload, "seller_opp_id": seller_opp_id, "version": v,
                                        "created_by": actor["id"], "created_at": now})
        T.add_activity(db, actor["id"], "opportunity", seller_opp_id, "data_update", "Teaser draft saved")
    return tid


def record_seller_approval(db, actor, teaser_id: int, method: str, note: str | None) -> None:
    """The seller agreed to these exact content categories (§9.1). Recorded as share_teaser consent too."""
    if not user_can(db, actor, "crm.log_activity"):
        raise PermissionDenied("crm.log_activity")
    t = db.one("SELECT * FROM teasers WHERE id = ?", (teaser_id,))
    if not t or t["status"] != "draft":
        raise ValidationError("Only a draft teaser can be sent for the seller's approval.")
    if method not in ("call", "whatsapp", "in_person", "form"):
        raise ValidationError({"method": "How did the seller approve it?"})
    o = S.load(db, t["seller_opp_id"])
    now = now_iso()
    labels = [TEASER_FIELDS[k][0] for k in json.loads(t["field_keys"])]
    with db:
        db.update("teasers", teaser_id, {"status": "seller_approved", "seller_consent_at": now,
                                         "seller_consent_method": method, "seller_consent_note": (note or "").strip() or None,
                                         "seller_consent_by": actor["id"], "updated_at": now})
        db.insert("contact_consents", {
            "contact_id": o["contact_id"], "purpose": "share_teaser", "channel": "any", "granted": 1,
            "source": f"staff_{method}", "text_version": f"{TEASER_CONSENT_VERSION} v{t['version']}",
            "note": "Seller approved teaser content: " + ", ".join(labels), "captured_by": actor["id"], "captured_at": now})
        T.add_activity(db, actor["id"], "opportunity", o["id"], "approval",
                       f"Seller approved teaser v{t['version']} content ({method.replace('_', ' ')})")
        audit.record(db, actor["id"], "teaser.seller_approved", "opportunity", o["id"], {"teaser": teaser_id})


def approve(db, actor, teaser_id: int) -> None:
    if not user_can(db, actor, "listing.approve"):
        raise PermissionDenied("listing.approve")
    t = db.one("SELECT * FROM teasers WHERE id = ?", (teaser_id,))
    if not t or t["status"] != "seller_approved":
        raise ValidationError("The seller must approve the teaser content before a reviewer can approve it.")
    listing = db.one("SELECT approval_state FROM seller_listings WHERE opportunity_id = ?", (t["seller_opp_id"],))
    if listing["approval_state"] != "approved":
        raise ValidationError("The listing itself must be approved first.")
    now = now_iso()
    with db:
        db.execute("UPDATE teasers SET status = 'retired', updated_at = ? WHERE seller_opp_id = ? AND status = 'approved'",
                   (now, t["seller_opp_id"]))
        db.update("teasers", teaser_id, {"status": "approved", "approved_by": actor["id"], "approved_at": now,
                                         "updated_at": now})
        T.add_activity(db, actor["id"], "opportunity", t["seller_opp_id"], "approval", f"Teaser v{t['version']} approved")
        audit.record(db, actor["id"], "teaser.approved", "opportunity", t["seller_opp_id"], {"teaser": teaser_id})


def share_with_buyer(db, actor, match_id: int, channel: str, link_base: str = "") -> dict:
    if not user_can(db, actor, "crm.log_activity"):
        raise PermissionDenied("crm.log_activity")
    c = M._cand(db, match_id)
    if c["stage"] not in ("approved_to_approach", "interest_confirmed"):
        raise ValidationError("A teaser can be shared only after the match is approved to approach.")
    t = approved_teaser(db, c["seller_opp_id"])
    if not t:
        raise ValidationError("This salon has no approved teaser yet (seller approval + reviewer approval).")
    from . import forms as F
    bopp = F.load_opportunity(db, c["buyer_opp_id"])
    blockers = F.send_blockers(db, bopp, channel)
    if blockers:
        raise ValidationError("Cannot share: " + "; ".join(blockers) + ".")
    token = secrets.token_urlsafe(24)
    now = now_iso()
    expires = to_iso(now_utc() + timedelta(days=TEASER_SHARE_DAYS))
    link = f"{link_base.rstrip('/')}/t/{token}"
    comm = None
    with db:
        sid = db.insert("teaser_shares", {"teaser_id": t["id"], "match_id": match_id, "buyer_opp_id": c["buyer_opp_id"],
                                          "token_hash": _hash(token), "channel": channel, "shared_by": actor["id"],
                                          "created_at": now, "expires_at": expires})
        if channel == "whatsapp_mock":
            from . import inbox as I
            variables = {"name": bopp["contact_name"].split()[0], "sender": actor["full_name"].split()[0],
                         "expires": fmt_ist(expires, "%d %b %Y"), "link": link}
            comm = I.send(db, actor, contact_id=bopp["contact_id"], opportunity_id=bopp["id"], template_key="teaser_share",
                          variables=variables, stored_variables={"link": "[teaser link]"}, system=True)["id"]
        M._event(db, match_id, "teaser_shared", actor["id"], body=f"Teaser v{t['version']} shared with the buyer"
                 + (" via WhatsApp (MOCK — not actually sent)" if channel == "whatsapp_mock" else " as a link"))
        T.add_activity(db, actor["id"], "opportunity", c["buyer_opp_id"], "system",
                       f"Anonymous teaser for {c['seller_ref']} shared (v{t['version']})")
        audit.record(db, actor["id"], "teaser.shared", "match", match_id, {"teaser": t["id"], "share": sid,
                                                                          "channel": channel})
    return {"share_id": sid, "link": link, "token": token, "communication": comm}


def open_share(db, token: str) -> dict | None:
    if not token or len(token) > 100:
        return None
    s = db.one("SELECT s.*, t.content, t.version FROM teaser_shares s JOIN teasers t ON t.id = s.teaser_id "
               "WHERE s.token_hash = ?", (_hash(token),))
    if not s or s["revoked_at"] or s["expires_at"] < now_iso():
        return None
    if not s["opened_at"]:
        db.update("teaser_shares", s["id"], {"opened_at": now_iso()})
        M._event(db, s["match_id"], "teaser_opened", None, body="Buyer opened the teaser link")
    s["content_d"] = json.loads(s["content"])
    return s


# -- Consent to share identity & introductions -------------------------------------------
def record_identity_consent(db, actor, match_id: int, side: str, method: str, note: str | None) -> None:
    if not user_can(db, actor, "crm.log_activity"):
        raise PermissionDenied("crm.log_activity")
    if side not in ("buyer", "seller") or method not in ("call", "whatsapp", "in_person", "form"):
        raise ValidationError("Choose the party and how they gave consent.")
    c = M._cand(db, match_id)
    opp_id = c["buyer_opp_id"] if side == "buyer" else c["seller_opp_id"]
    contact_id = db.scalar("SELECT contact_id FROM opportunities WHERE id = ?", (opp_id,))
    other = c["seller_ref"] if side == "buyer" else c["buyer_ref"]
    with db:
        db.insert("contact_consents", {
            "contact_id": contact_id, "purpose": "share_identity", "channel": "any", "granted": 1,
            "source": f"staff_{method}", "text_version": IDENTITY_CONSENT_VERSION,
            "note": f"Agreed to exchange contact details for match {c['buyer_ref']} ↔ {c['seller_ref']}"
                    + (f" — {note.strip()}" if note and note.strip() else ""),
            "captured_by": actor["id"], "captured_at": now_iso()})
        M._event(db, match_id, "consent", actor["id"], body=f"{side.title()} agreed to share their identity ({method})")
        T.add_activity(db, actor["id"], "opportunity", opp_id, "system",
                       f"Consent recorded: identity may be shared with {other} ({method})")
        audit.record(db, actor["id"], "consent.share_identity", "match", match_id, {"side": side, "method": method})


def _latest_consent(db, contact_id, purpose):
    return db.one("SELECT id, granted, text_version, captured_at, source FROM contact_consents WHERE contact_id = ? "
                  "AND purpose = ? ORDER BY captured_at DESC, id DESC LIMIT 1", (contact_id, purpose))


def intro_blockers(db, match_id: int) -> list[str]:
    c = M._cand(db, match_id)
    out = []
    if c["stage"] != "interest_confirmed":
        out.append("buyer interest has not been confirmed on this match")
    for side, oid in (("buyer", c["buyer_opp_id"]), ("seller", c["seller_opp_id"])):
        o = db.one("SELECT contact_id, status FROM opportunities o JOIN contacts c ON c.id = o.contact_id "
                   "WHERE o.id = ?", (oid,))
        dnc = db.scalar("SELECT do_not_contact FROM contacts WHERE id = ?", (o["contact_id"],))
        if dnc:
            out.append(f"the {side} asked not to be contacted")
        cons = _latest_consent(db, o["contact_id"], "share_identity")
        if not cons or not cons["granted"]:
            out.append(f"no {side} consent to share identity")
    if not db.scalar("SELECT COUNT(*) FROM teaser_shares WHERE match_id = ? AND revoked_at IS NULL", (match_id,)):
        out.append("no approved teaser has been shared with the buyer")
    return out


def request_introduction(db, actor, match_id: int, note: str | None) -> int:
    if not user_can(db, actor, "crm.log_activity"):
        raise PermissionDenied("crm.log_activity")
    if db.scalar("SELECT COUNT(*) FROM introductions WHERE match_id = ? AND status IN ('requested','approved')",
                 (match_id,)):
        raise ValidationError("An introduction is already requested or approved for this match.")
    c = M._cand(db, match_id)
    if c["stage"] != "interest_confirmed":
        raise ValidationError("Record the buyer's interest before requesting an introduction.")
    with db:
        iid = db.insert("introductions", {"match_id": match_id, "status": "requested", "requested_by": actor["id"],
                                          "requested_at": now_iso(), "request_note": (note or "").strip() or None})
        M._event(db, match_id, "intro_requested", actor["id"], body="Introduction requested" +
                 (f": {note.strip()}" if note and note.strip() else ""))
        for a in db.all("SELECT id FROM users WHERE role = 'super_admin' AND is_active = 1"):
            T.notify(db, a["id"], "approval", f"Introduction requested: {c['buyer_ref']} ↔ {c['seller_ref']}",
                     f"/matches/{match_id}")
        audit.record(db, actor["id"], "intro.requested", "match", match_id, {"introduction": iid})
    return iid


def decide_introduction(db, actor, intro_id: int, decision: str, note: str | None) -> int | None:
    """Approve → identity released + deal room created. Returns the deal id when approved."""
    if not user_can(db, actor, "listing.approve"):
        audit.record(db, actor["id"], "intro.decide", "introduction", intro_id, {"decision": decision}, outcome="denied")
        raise PermissionDenied("listing.approve")
    intro = db.one("SELECT * FROM introductions WHERE id = ?", (intro_id,))
    if not intro or intro["status"] != "requested":
        raise ValidationError("This introduction is not waiting for a decision.")
    if decision not in ("approved", "rejected"):
        raise ValidationError("Choose approve or reject.")
    now = now_iso()
    if decision == "rejected":
        if not (note or "").strip():
            raise ValidationError({"note": "Give a reason for rejecting."})
        with db:
            db.update("introductions", intro_id, {"status": "rejected", "decided_by": actor["id"], "decided_at": now,
                                                  "decision_note": note.strip()})
            M._event(db, intro["match_id"], "intro_rejected", actor["id"], body=f"Introduction rejected: {note.strip()}")
            audit.record(db, actor["id"], "intro.rejected", "match", intro["match_id"], {"introduction": intro_id})
        return None
    blockers = intro_blockers(db, intro["match_id"])
    if blockers:
        audit.record(db, actor["id"], "intro.approve_blocked", "match", intro["match_id"], {"blockers": blockers},
                     outcome="denied")
        raise ValidationError("Cannot introduce yet: " + "; ".join(blockers) + ".")
    c = M._cand(db, intro["match_id"])
    b = db.one("SELECT o.*, c.full_name, c.phone_normalized, c.email, c.id AS cid FROM opportunities o "
               "JOIN contacts c ON c.id = o.contact_id WHERE o.id = ?", (c["buyer_opp_id"],))
    s = db.one("SELECT o.*, c.full_name, c.phone_normalized, c.email, c.id AS cid, bz.name AS salon, bz.locality, "
               "l.address_line FROM opportunities o JOIN contacts c ON c.id = o.contact_id "
               "JOIN salon_businesses bz ON bz.id = o.business_id JOIN seller_listings l ON l.opportunity_id = o.id "
               "WHERE o.id = ?", (c["seller_opp_id"],))
    details = {"to_buyer": {"Seller contact": s["full_name"], "Seller phone": s["phone_normalized"],
                            "Salon": s["salon"], "Address": s["address_line"] or f"{s['locality']} (exact address on visit)"},
               "to_seller": {"Buyer contact": b["full_name"], "Buyer phone": b["phone_normalized"]}}
    snapshot = {side: dict(_latest_consent(db, cid, "share_identity")) for side, cid in (("buyer", b["cid"]),
                                                                                         ("seller", s["cid"]))}
    with db:
        db.update("introductions", intro_id, {"status": "approved", "decided_by": actor["id"], "decided_at": now,
                                              "decision_note": (note or "").strip() or None,
                                              "details_shared": json.dumps(details),
                                              "consent_snapshot": json.dumps(snapshot)})
        from . import deals as D
        deal_id = D.create_from_match(db, actor, intro["match_id"])
        M._event(db, intro["match_id"], "introduced", actor["id"],
                 body=f"Introduction approved — contact details exchanged; deal {D.ref(deal_id)} opened")
        for oid in (c["buyer_opp_id"], c["seller_opp_id"]):
            T.add_activity(db, actor["id"], "opportunity", oid, "approval",
                           f"Introduction approved for {c['buyer_ref']} ↔ {c['seller_ref']}; deal {D.ref(deal_id)} opened")
        audit.record(db, actor["id"], "intro.approved", "match", intro["match_id"],
                     {"introduction": intro_id, "deal": deal_id, "details_shared": details, "consents": snapshot})
    return deal_id
