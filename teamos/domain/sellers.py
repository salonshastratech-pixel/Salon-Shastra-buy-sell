"""Seller management domain logic (PRD §5, §6).

Covers: lead creation with duplicate detection, field updates with provenance and change
history, all-in buyer cash requirement, financial summary, completeness, readiness score,
stage rules, listing checklist and approval with re-review on material change.
"""
from __future__ import annotations

from datetime import datetime

from .. import audit
from ..permissions import can_view_restricted_seller, user_can
from ..timeutil import now_iso, now_utc
from . import tasks as T
from .common import normalize_indian_mobile, parse_float, parse_inr, parse_int
from .errors import PermissionDenied, ValidationError
from .seller_fields import (ANSWER_STATE_LABELS, BY_KEY, CHECKLIST_STATUSES, FIELDS, LISTING_CHECKLIST,
                            MATERIAL_KEYS, RESTRICTED_KEYS, SECTIONS, is_applicable)

ENTITY = "seller_listing"

# -- Stages (§6.1) -------------------------------------------------------------------
STAGES = [
    ("new", "New"), ("contact_attempted", "Contact attempted"), ("connected", "Connected"),
    ("basic_qualified", "Basic qualified"), ("form_sent", "Detailed form sent"),
    ("form_received", "Form received"), ("assessment_review", "Assessment in review"),
    ("verification_pending", "Verification pending"), ("listing_approved", "Listing approved"),
    ("active_matching", "Active matching"), ("negotiation", "Negotiation / diligence"),
    ("handover", "Handover"), ("closed_won", "Closed won"),
]
HOLDING = [("nurture", "Nurture"), ("unqualified", "Unqualified"), ("withdrawn", "Withdrawn"),
           ("closed_lost", "Closed lost"), ("do_not_contact", "Do not contact")]
STAGE_LABELS = dict(STAGES + HOLDING)
OPERATIONAL = ["new", "contact_attempted", "connected", "basic_qualified", "form_sent", "form_received",
               "assessment_review", "verification_pending"]
POST_APPROVAL = ["active_matching", "negotiation", "handover"]
REASON_REQUIRED = {"nurture", "unqualified", "withdrawn", "closed_lost", "do_not_contact", "closed_won"}
CLOSED_STAGES = {"unqualified", "withdrawn", "closed_lost", "do_not_contact", "closed_won"}

SOURCES = [("manual_call", "Manual call"), ("referral", "Referral"), ("meta_lead", "Meta lead form"),
           ("landing_page", "Website / landing page"), ("whatsapp", "WhatsApp enquiry"),
           ("event", "Webinar / event"), ("import", "Spreadsheet import"), ("partner", "Partner / agent")]
SOURCE_LABELS = dict(SOURCES)

FIRST_CONTACT_SLA_MIN = 15  # PRD §5.4 proposal — pending founder approval (D-10)


def status_for(stage: str) -> str:
    if stage in CLOSED_STAGES:
        return "closed"
    if stage == "nurture":
        return "holding"
    return "active"


# -- Loading -------------------------------------------------------------------------
def load(db, opp_id: int) -> dict | None:
    row = db.one(
        "SELECT o.*, c.full_name AS contact_name, c.phone_normalized, c.alt_phone_normalized, c.email, "
        "c.do_not_contact, b.name AS salon_name, b.city, b.locality, u.full_name AS owner_name, "
        "l.id AS listing_id FROM opportunities o JOIN contacts c ON c.id = o.contact_id "
        "LEFT JOIN salon_businesses b ON b.id = o.business_id LEFT JOIN users u ON u.id = o.primary_owner_id "
        "LEFT JOIN seller_listings l ON l.opportunity_id = o.id WHERE o.id = ? AND o.kind = 'seller'", (opp_id,))
    return row


def load_listing(db, listing_id: int) -> dict:
    return db.one("SELECT * FROM seller_listings WHERE id = ?", (listing_id,))


def provenance(db, listing_id: int) -> dict[str, dict]:
    rows = db.all("SELECT * FROM field_provenance WHERE entity_type = ? AND entity_id = ?", (ENTITY, listing_id))
    return {r["field_key"]: r for r in rows}


def masked_listing(db, user: dict, opp: dict, listing: dict) -> tuple[dict, set[str]]:
    """Server-side field masking: restricted fields removed unless the user may see them (FR-036)."""
    if can_view_restricted_seller(db, user, opp):
        return dict(listing), set()
    out = dict(listing)
    for k in RESTRICTED_KEYS:
        out[k] = None
    return out, set(RESTRICTED_KEYS)


# -- Duplicate detection (§5.3) ------------------------------------------------------
def find_duplicates(db, phone: str | None, salon_name: str, locality: str) -> list[dict]:
    found: dict[int, dict] = {}
    if phone:
        for c in db.all("SELECT * FROM contacts WHERE phone_normalized = ? OR alt_phone_normalized = ?",
                        (phone, phone)):
            found[c["id"]] = {**c, "reason": "Same mobile number"}
    if salon_name and locality:
        for c in db.all(
            "SELECT c.* FROM salon_businesses b JOIN contacts c ON c.id = b.owner_contact_id "
            "WHERE lower(trim(b.name)) = lower(trim(?)) AND lower(trim(b.locality)) = lower(trim(?))",
                (salon_name, locality)):
            found.setdefault(c["id"], {**c, "reason": "Same salon name and locality"})
    for c in found.values():
        c["opportunities"] = db.all(
            "SELECT o.id, o.ref_code, o.kind, o.stage, b.name AS salon_name FROM opportunities o "
            "LEFT JOIN salon_businesses b ON b.id = o.business_id WHERE o.contact_id = ? ORDER BY o.id", (c["id"],))
    return list(found.values())


# -- Creation (§5.6, §6.2) -----------------------------------------------------------
def create_lead(db, actor: dict, data: dict, dedupe_decision: str | None = None,
                first_task_due: str | None = None, first_task_priority: str = "high") -> dict:
    """Returns {"opportunity_id": id} or {"duplicates": [...]} when a decision is needed."""
    if not user_can(db, actor, "crm.create"):
        raise PermissionDenied("crm.create")
    errors = {}
    name = (data.get("full_name") or "").strip()
    salon = (data.get("salon_name") or "").strip()
    city = (data.get("city") or "").strip()
    locality = (data.get("locality") or "").strip()
    source = data.get("source") or ""
    if not name:
        errors["full_name"] = "Contact name is required."
    if not salon:
        errors["salon_name"] = "Salon name is required."
    if not city:
        errors["city"] = "City is required."
    if not locality:
        errors["locality"] = "Locality is required."
    if source not in SOURCE_LABELS:
        errors["source"] = "Choose where this lead came from."
    phone, perr = normalize_indian_mobile(data.get("phone"))
    if perr:
        errors["phone"] = perr
    alt = None
    if (data.get("alt_phone") or "").strip():
        alt, aerr = normalize_indian_mobile(data.get("alt_phone"))
        if aerr:
            errors["alt_phone"] = aerr
    owner_id = int(data.get("owner_id") or actor["id"])
    if owner_id != actor["id"] and not user_can(db, actor, "crm.assign"):
        errors["owner_id"] = "You can create leads for yourself; ask a team lead to assign others."
    quick_values, qerrors = _parse_fields(data, [f.key for f in FIELDS if f.quick])
    errors.update(qerrors)
    if errors:
        raise ValidationError(errors)

    dups = find_duplicates(db, phone, salon, locality)
    link_contact_id = None
    if dups:
        if not dedupe_decision:
            return {"duplicates": dups}
        if dedupe_decision.startswith("link:"):
            link_contact_id = int(dedupe_decision.split(":", 1)[1])
            if link_contact_id not in {d["id"] for d in dups}:
                raise ValidationError("Chosen contact is not one of the detected duplicates.")
        elif dedupe_decision != "new_flagged":
            raise ValidationError("Choose how to handle the possible duplicate.")

    now = now_iso()
    synthetic = int(data.get("is_synthetic") or 0)
    with db:
        if link_contact_id:
            contact_id = link_contact_id  # never overwrite the existing contact's details
        else:
            contact_id = db.insert("contacts", {
                "full_name": name, "phone_normalized": phone, "phone_raw": data.get("phone"),
                "alt_phone_normalized": alt, "email": (data.get("email") or "").strip().lower() or None,
                "city": city, "locality": locality, "is_synthetic": synthetic, "created_by": actor["id"],
                "created_at": now, "updated_at": now})
            for d in dups:
                db.insert("duplicate_reviews", {"contact_id": contact_id, "candidate_contact_id": d["id"],
                                                "reason": d["reason"], "created_at": now})
        business = db.one("SELECT id FROM salon_businesses WHERE owner_contact_id = ? AND lower(trim(name)) = "
                          "lower(trim(?)) AND lower(trim(locality)) = lower(trim(?))", (contact_id, salon, locality))
        business_id = business["id"] if business else db.insert("salon_businesses", {
            "name": salon, "city": city, "locality": locality, "owner_contact_id": contact_id,
            "is_synthetic": synthetic, "created_at": now, "updated_at": now})
        opp_id = db.insert("opportunities", {
            "kind": "seller", "contact_id": contact_id, "business_id": business_id, "stage": "new",
            "status": "active", "primary_owner_id": owner_id, "priority": data.get("priority") or "normal",
            "source": source, "source_detail": (data.get("source_note") or "").strip() or None,
            "is_synthetic": synthetic, "created_by": actor["id"], "created_at": now, "updated_at": now})
        db.update("opportunities", opp_id, {"ref_code": f"SL-{opp_id:05d}"})
        listing_id = db.insert("seller_listings", {"opportunity_id": opp_id, "business_id": business_id,
                                                   "created_at": now, "updated_at": now})
        _apply_values(db, actor, listing_id, quick_values, source=f"staff_entry:{source}", bump_version=False)
        for key, _ in LISTING_CHECKLIST:
            db.execute("INSERT INTO listing_checklist (listing_id, item_key, status, updated_at) VALUES (?, ?, ?, ?)",
                       (listing_id, key, "not_requested", now))
        db.insert("contact_consents", {
            "contact_id": contact_id, "purpose": "service_contact", "channel": "any",
            "granted": 1 if data.get("consent_contact") else 0, "source": f"staff_entry:{source}",
            "text_version": "staff-call-v0.1", "captured_by": actor["id"], "captured_at": now})
        db.insert("stage_history", {"opportunity_id": opp_id, "from_stage": None, "to_stage": "new",
                                    "reason": "Lead created", "actor_id": actor["id"], "at": now})
        T.add_activity(db, actor["id"], "opportunity", opp_id, "system",
                       f"Seller lead created from {SOURCE_LABELS[source]}"
                       + (" (linked to existing contact)" if link_contact_id else "")
                       + (" — flagged for duplicate review" if dups and not link_contact_id else ""))
        T.create_task(db, actor, title=f"First call: {name} ({salon})", assignee_id=owner_id,
                      due_at=first_task_due or T.first_contact_due(FIRST_CONTACT_SLA_MIN), task_type="first_contact",
                      priority=first_task_priority, entity_type="opportunity", entity_id=opp_id, is_synthetic=synthetic)
        audit.record(db, actor["id"], "seller.create", "opportunity", opp_id,
                     {"source": source, "linked_contact": link_contact_id, "duplicate_candidates": len(dups)})
    return {"opportunity_id": opp_id}


# -- Field parsing / updating --------------------------------------------------------
def _parse_one(fd, raw):
    raw = (raw or "").strip() if isinstance(raw, str) else raw
    if raw in (None, ""):
        return None
    if fd.ftype == "inr":
        return parse_inr(raw)
    if fd.ftype == "int":
        return parse_int(raw)
    if fd.ftype == "pct":
        return parse_float(raw, 0, 100)
    if fd.ftype == "enum":
        if raw not in dict(fd.options):
            raise ValueError("Choose one of the listed options")
        return raw
    if fd.ftype == "date":
        datetime.strptime(raw, "%Y-%m-%d")
        return raw
    return str(raw)[:2000]


def _parse_fields(data: dict, keys: list[str]) -> tuple[dict, dict]:
    """data[key] = raw value, data[key + '__state'] = answer state. Returns {key: (value, state)}."""
    values, errors = {}, {}
    for key in keys:
        fd = BY_KEY[key]
        raw = data.get(key)
        state = data.get(key + "__state") or ""
        if state and state not in ANSWER_STATE_LABELS:
            errors[key] = "Invalid answer state"
            continue
        try:
            val = _parse_one(fd, raw)
        except ValueError as e:
            errors[key] = str(e)
            continue
        if val is not None:
            values[key] = (val, "value")
        elif state and state != "value":
            values[key] = (None, state)
        elif key in data or (key + "__state") in data:
            values[key] = (None, None)  # explicitly blank = not provided
    return values, errors


def _apply_values(db, actor, listing_id, values: dict, source: str, bump_version: bool = True) -> list[str]:
    listing = load_listing(db, listing_id)
    prov = provenance(db, listing_id)
    changed = []
    now = now_iso()
    new_version = listing["data_version"] + (1 if bump_version else 0)
    for key, (val, state) in values.items():
        old_val = listing.get(key)
        old_state = prov.get(key, {}).get("answer_state")
        if old_val == val and old_state == state:
            continue
        if state is None and old_state is None:
            continue
        db.execute(f"UPDATE seller_listings SET {key} = ? WHERE id = ?", (val, listing_id))
        if state is None:
            db.execute("DELETE FROM field_provenance WHERE entity_type = ? AND entity_id = ? AND field_key = ?",
                       (ENTITY, listing_id, key))
        else:
            db.execute("DELETE FROM field_provenance WHERE entity_type = ? AND entity_id = ? AND field_key = ?",
                       (ENTITY, listing_id, key))
            db.insert("field_provenance", {"entity_type": ENTITY, "entity_id": listing_id, "field_key": key,
                                           "answer_state": state, "evidence_status": "seller_declared",
                                           "source": source, "updated_by": actor["id"], "updated_at": now})
        db.insert("field_change_log", {"entity_type": ENTITY, "entity_id": listing_id, "data_version": new_version,
                                       "field_key": key, "old_value": None if old_val is None else str(old_val),
                                       "new_value": None if val is None else str(val), "old_state": old_state,
                                       "new_state": state, "actor_id": actor["id"], "at": now})
        changed.append(key)
    if changed and bump_version:
        db.execute("UPDATE seller_listings SET data_version = ?, updated_at = ? WHERE id = ?",
                   (new_version, now, listing_id))
    return changed


def update_fields(db, actor: dict, opp: dict, data: dict, section: str) -> list[str]:
    if not user_can(db, actor, "crm.edit"):
        raise PermissionDenied("crm.edit")
    keys = [f.key for f in FIELDS if f.section == section]
    if not can_view_restricted_seller(db, actor, opp):
        keys = [k for k in keys if k not in RESTRICTED_KEYS]  # cannot see => cannot edit
    values, errors = _parse_fields(data, keys)
    if errors:
        raise ValidationError(errors)
    biz_changed = []
    with db:
        if section == "quick":
            for col in ("salon_name", "city", "locality"):
                if col in data:
                    new = (data.get(col) or "").strip()
                    if not new:
                        raise ValidationError({col: "Required."})
                    dbcol = "name" if col == "salon_name" else col
                    if new != (opp.get(col) or ""):
                        db.execute(f"UPDATE salon_businesses SET {dbcol} = ?, updated_at = ? WHERE id = ?",
                                   (new, now_iso(), opp["business_id"]))
                        biz_changed.append(col)
        changed = _apply_values(db, actor, opp["listing_id"], values, source="staff_entry")
        if changed or biz_changed:
            labels = [BY_KEY[k].label for k in changed] + biz_changed
            T.add_activity(db, actor["id"], "opportunity", opp["id"], "data_update",
                           "Updated " + ", ".join(labels))
            _flag_re_review_if_material(db, actor, opp, set(changed) | ({"locality"} & set(biz_changed)))
            audit.record(db, actor["id"], "seller.update", "opportunity", opp["id"],
                         {"section": section, "fields": changed + biz_changed})
    return changed + biz_changed


def _flag_re_review_if_material(db, actor, opp, changed: set[str]) -> None:
    listing = load_listing(db, opp["listing_id"])
    material = changed & (MATERIAL_KEYS | {"locality"})
    if listing["approval_state"] == "approved" and material:
        db.execute("UPDATE seller_listings SET approval_state = 're_review_required' WHERE id = ?", (listing["id"],))
        db.insert("listing_approvals", {"listing_id": listing["id"], "decision": "re_review_flagged",
                                        "data_version": listing["data_version"],
                                        "conditions": "Material change: " + ", ".join(sorted(material)),
                                        "actor_id": actor["id"], "at": now_iso()})
        T.add_activity(db, actor["id"], "opportunity", opp["id"], "approval",
                       "Listing needs re-review after material change: "
                       + ", ".join(BY_KEY[k].label if k in BY_KEY else k for k in sorted(material)))
        for a in db.all("SELECT id FROM users WHERE role = 'super_admin' AND is_active = 1"):
            T.notify(db, a["id"], "re_review", f"{opp['ref_code']} needs listing re-review", f"/sellers/{opp['id']}")
        audit.record(db, actor["id"], "listing.re_review_required", "seller_listing", listing["id"],
                     {"fields": sorted(material), "data_version": listing["data_version"]})


def set_evidence(db, actor, opp, field_key: str, status: str, note: str | None) -> None:
    if not user_can(db, actor, "evidence.review"):
        raise PermissionDenied("evidence.review")
    if field_key not in BY_KEY:
        raise ValidationError("Unknown field.")
    prov = provenance(db, opp["listing_id"]).get(field_key)
    if not prov:
        raise ValidationError("Record an answer before reviewing its evidence.")
    with db:
        db.execute("UPDATE field_provenance SET evidence_status = ?, note = ?, updated_by = ?, updated_at = ? "
                   "WHERE entity_type = ? AND entity_id = ? AND field_key = ?",
                   (status, note, actor["id"], now_iso(), ENTITY, opp["listing_id"], field_key))
        T.add_activity(db, actor["id"], "opportunity", opp["id"], "data_update",
                       f"Evidence for {BY_KEY[field_key].label} marked {status.replace('_', ' ')}")
        audit.record(db, actor["id"], "evidence.review", "seller_listing", opp["listing_id"],
                     {"field": field_key, "status": status})


# -- All-in buyer cash requirement (§6.5, Appendix D) -------------------------------
def all_in_cost(listing: dict, prov: dict) -> dict:
    lines, missing = [], []
    total = 0

    def st(k):
        return prov.get(k, {}).get("answer_state")

    if listing.get("asking_price") is None:
        missing.append("Asking price")
    else:
        total += listing["asking_price"]
        lines.append(("Asking price (net consideration)", listing["asking_price"], "added"))

    dt = listing.get("deposit_treatment")
    if dt == "buyer_pays_separately":
        if listing.get("rent_deposit") is None:
            missing.append("Rent deposit amount")
        else:
            total += listing["rent_deposit"]
            lines.append(("Rent deposit (payable separately)", listing["rent_deposit"], "added"))
    elif dt == "included_in_asking":
        lines.append(("Rent deposit", listing.get("rent_deposit"), "included in asking — not added"))
    elif dt == "not_applicable":
        pass
    else:
        missing.append("Deposit treatment")

    if listing.get("stock_value_separate") is not None:
        total += listing["stock_value_separate"]
        lines.append(("Stock / inventory (separate)", listing["stock_value_separate"], "added"))
    elif st("stock_value_separate") != "not_applicable":
        missing.append("Separately payable stock")

    bm = listing.get("business_model")
    if bm == "franchise":
        if listing.get("franchise_transfer_fee") is not None:
            total += listing["franchise_transfer_fee"]
            lines.append(("Franchise transfer fee", listing["franchise_transfer_fee"], "added"))
        elif st("franchise_transfer_fee") != "not_applicable":
            missing.append("Franchise transfer fee")
    elif bm is None:
        missing.append("Independent / franchise")

    if listing.get("working_capital_estimate") is not None:
        total += listing["working_capital_estimate"]
        lines.append(("Working capital / handover (estimate)", listing["working_capital_estimate"], "added"))

    return {
        "known_total": total if lines else None,
        "complete": not missing,
        "missing": missing,
        "lines": lines,
        "note": "Excludes Salon Shastra professional fees and taxes, which are agreed separately.",
    }


# -- Financials (§6.5) ---------------------------------------------------------------
def save_month(db, actor, opp, month: str, revenue: str, expenses: str, drawings: str, source: str) -> None:
    if not user_can(db, actor, "crm.edit"):
        raise PermissionDenied("crm.edit")
    try:
        datetime.strptime(month, "%Y-%m")
    except ValueError:
        raise ValidationError({"month": "Use YYYY-MM"})
    try:
        r, e, d = parse_inr(revenue), parse_inr(expenses), parse_inr(drawings)
    except ValueError as ex:
        raise ValidationError({"amount": str(ex)})
    now = now_iso()
    with db:
        db.execute("DELETE FROM seller_monthly_financials WHERE listing_id = ? AND month = ?",
                   (opp["listing_id"], month))
        db.insert("seller_monthly_financials", {
            "listing_id": opp["listing_id"], "month": month, "revenue": r, "operating_expenses": e,
            "owner_drawings": d, "evidence_status": "seller_declared", "source": source or "staff_entry",
            "updated_by": actor["id"], "updated_at": now})
        T.add_activity(db, actor["id"], "opportunity", opp["id"], "data_update", f"Financials recorded for {month}")
        audit.record(db, actor["id"], "seller.financials", "opportunity", opp["id"], {"month": month})


def financial_summary(db, listing_id: int) -> dict:
    rows = db.all("SELECT * FROM seller_monthly_financials WHERE listing_id = ? ORDER BY month", (listing_id,))
    warnings = []
    both = [r for r in rows if r["revenue"] is not None and r["operating_expenses"] is not None]
    rev_only = [r["month"] for r in rows if r["revenue"] is not None and r["operating_expenses"] is None]
    if rev_only:
        warnings.append(f"Costs missing for {len(rev_only)} month(s) ({', '.join(rev_only)}): "
                        "operating surplus is incomplete and unverified.")
    gaps = []
    if rows:
        y, m = map(int, rows[0]["month"].split("-"))
        have = {r["month"] for r in rows}
        last = rows[-1]["month"]
        while f"{y:04d}-{m:02d}" <= last:
            key = f"{y:04d}-{m:02d}"
            if key not in have:
                gaps.append(key)
            m += 1
            if m > 12:
                y, m = y + 1, 1
        if gaps:
            warnings.append(f"No data for {len(gaps)} month(s) in range: {', '.join(gaps)}.")
    evidenced = [r for r in both if r["evidence_status"] in ("document_supported", "verified")]
    avg_surplus = None
    if both:
        avg_surplus = round(sum(r["revenue"] - r["operating_expenses"] for r in both) / len(both))
    if both and not evidenced:
        warnings.append("All figures are seller-declared; none are document-supported yet.")
    for r in rows:
        r["surplus"] = (r["revenue"] - r["operating_expenses"]) if r in both else None
    return {"rows": rows, "months_complete": len(both), "months_evidenced": len(evidenced),
            "avg_reported_surplus": avg_surplus, "warnings": warnings, "gaps": gaps}


# -- Completeness & readiness (§6.3A, §7.4) ------------------------------------------
def completeness(listing: dict, prov: dict) -> dict:
    per_section, missing_material = {}, []
    total = answered = 0
    for key, label in SECTIONS:
        fs = [f for f in FIELDS if f.section == key and is_applicable(f, listing)]
        a = sum(1 for f in fs if prov.get(f.key, {}).get("answer_state") in ("value", "not_applicable"))
        per_section[key] = {"label": label, "answered": a, "total": len(fs)}
        total += len(fs)
        answered += a
        for f in fs:
            if f.material and prov.get(f.key, {}).get("answer_state") not in ("value", "not_applicable"):
                missing_material.append(f)
    return {"pct": round(100 * answered / total) if total else 0, "answered": answered, "total": total,
            "sections": per_section, "missing_material": missing_material}


READINESS_VERSION = "seller-readiness v0.1 (PRD §7.4 proposal, pending founder approval D-08)"


def readiness_score(db, opp: dict, listing: dict, prov: dict) -> dict:
    factors = []

    def add(name, pts, mx, reason):
        factors.append({"name": name, "points": pts, "max": mx, "reason": reason})

    # Authority / mandate — 20
    role_ok = listing.get("ownership_role") in ("owner", "partner", "authorised_rep")
    auth = listing.get("transfer_authority") == "yes"
    add("Authority / mandate", (10 if role_ok else 0) + (10 if auth else 0), 20,
        "; ".join([("decision-maker" if role_ok else "respondent role not confirmed as decision-maker"),
                   ("transfer authority confirmed" if auth else "transfer authority not confirmed")]))
    # Sale objective & urgency — 15
    purpose = listing.get("sale_purpose") in ("sell_entire", "partnership", "franchise_transfer")
    tl = {"immediate": 7, "within_3m": 7, "3_6m": 5, "6_12m": 3}.get(listing.get("sale_timeline"), 0)
    add("Sale objective & urgency", (8 if purpose else 0) + tl, 15,
        ("clear sale intent" if purpose else "sale intent unclear or help-seeking") + f"; timeline {tl}/7")
    # Financial evidence completeness — 25
    fin = financial_summary(db, listing["id"])
    pts = round(15 * min(fin["months_complete"], 6) / 6)
    if fin["months_complete"]:
        pts += round(10 * fin["months_evidenced"] / fin["months_complete"])
    add("Financial evidence", pts, 25,
        f"{fin['months_complete']} month(s) with revenue and costs; {fin['months_evidenced']} evidence-backed")
    # Asset / lease / obligation clarity — 20
    keys = ["chairs", "carpet_area_sqft", "monthly_rent", "deposit_treatment", "lease_expiry",
            "lease_transfer_status", "outstanding_loans", "supplier_dues", "prepaid_liabilities", "staff_headcount"]
    known = [k for k in keys if prov.get(k, {}).get("answer_state") in ("value", "not_applicable")
             and not (k == "lease_transfer_status" and listing.get(k) == "unchecked")]
    add("Asset, lease & obligation clarity", 2 * len(known), 20, f"{len(known)} of {len(keys)} key facts known")
    # Asking-term transparency — 10
    cost = all_in_cost(listing, prov)
    p = (4 if listing.get("asking_price") is not None else 0) + (4 if cost["complete"] else 0) \
        + (2 if listing.get("negotiable") in ("yes", "no") else 0)
    add("Asking-term transparency", p, 10,
        "all-in cost complete" if cost["complete"] else "missing: " + ", ".join(cost["missing"]))
    # Responsiveness — 10
    connected = db.scalar("SELECT COUNT(*) FROM activities WHERE entity_type = 'opportunity' AND entity_id = ? "
                          "AND kind = 'call' AND call_outcome IN ('connected','callback_requested')", (opp["id"],))
    comp = completeness(listing, prov)["pct"]
    add("Responsiveness", (5 if connected else 0) + (5 if comp >= 50 else 0), 10,
        f"{connected} connected call(s); intake {comp}% complete")
    score = sum(f["points"] for f in factors)
    band = "High priority" if score >= 80 else "Qualified – developing" if score >= 60 else "Nurture / needs information"
    return {"score": score, "band": band, "factors": factors, "version": READINESS_VERSION,
            "advisory": "Advisory only. Consent, authority and legal blockers are absolute regardless of score."}


# -- Stage transitions (§6.1) --------------------------------------------------------
def transition_options(db, user: dict, opp: dict, listing: dict, prov: dict) -> list[dict]:
    """Every stage with allowed flag and, if blocked, the missing prerequisite."""
    out = []
    for code, label in STAGES + HOLDING:
        if code == opp["stage"]:
            continue
        ok, why = check_transition(db, user, opp, listing, prov, code)
        out.append({"code": code, "label": label, "allowed": ok, "why": why})
    return out


def check_transition(db, user, opp, listing, prov, to: str) -> tuple[bool, str | None]:
    if to not in STAGE_LABELS:
        return False, "Unknown stage"
    if opp["status"] == "closed" and not user_can(db, user, "listing.approve"):
        return False, "Closed opportunities can only be reopened by an approver"
    if to == "listing_approved":
        return False, "Use the listing approval action (approver only)"
    if to == "closed_won":
        if not user_can(db, user, "listing.approve"):
            return False, "Only an approver can close a deal as won"
        if listing["approval_state"] != "approved":
            return False, "Listing must be approved first"
        return True, None
    if not user_can(db, user, "stage.operational"):
        return False, "You do not have permission to change stages"
    if to in POST_APPROVAL and listing["approval_state"] != "approved":
        return False, "Requires an approved listing"
    if to == "basic_qualified":
        missing = []
        if prov.get("ownership_role", {}).get("answer_state") != "value":
            missing.append("respondent role")
        if listing.get("sale_purpose") not in ("sell_entire", "partnership", "franchise_transfer"):
            missing.append("a sale intent (help-seeking owners are not sellers)")
        if missing:
            return False, "Needs " + " and ".join(missing)
    if to in ("verification_pending",):
        pct = completeness(listing, prov)["pct"]
        if pct < 50:
            return False, f"Intake is {pct}% complete; needs at least 50%"
    if to == "do_not_contact" and opp.get("do_not_contact"):
        return True, None
    return True, None


def change_stage(db, actor, opp: dict, to: str, reason: str | None, follow_up_due: str | None = None) -> None:
    listing = load_listing(db, opp["listing_id"])
    prov = provenance(db, listing["id"])
    ok, why = check_transition(db, actor, opp, listing, prov, to)
    if not ok:
        audit.record(db, actor["id"], "seller.stage", "opportunity", opp["id"],
                     {"to": to, "blocked": why}, outcome="denied")
        raise ValidationError({"stage": why})
    reason = (reason or "").strip()
    if to in REASON_REQUIRED and not reason:
        raise ValidationError({"reason": f"A reason is required for '{STAGE_LABELS[to]}'."})
    if to == "nurture" and not follow_up_due:
        raise ValidationError({"follow_up_due": "Nurture needs a follow-up date so the lead is not forgotten."})
    now = now_iso()
    with db:
        status = status_for(to)
        db.update("opportunities", opp["id"], {"stage": to, "status": status, "updated_at": now,
                                               "closed_reason": reason if status == "closed" else None})
        db.insert("stage_history", {"opportunity_id": opp["id"], "from_stage": opp["stage"], "to_stage": to,
                                    "reason": reason or None, "actor_id": actor["id"], "at": now})
        T.add_activity(db, actor["id"], "opportunity", opp["id"], "stage_change",
                       f"{STAGE_LABELS.get(opp['stage'])} → {STAGE_LABELS[to]}" + (f": {reason}" if reason else ""))
        if to == "do_not_contact":
            db.execute("UPDATE contacts SET do_not_contact = 1, updated_at = ? WHERE id = ?", (now, opp["contact_id"]))
            db.insert("contact_consents", {"contact_id": opp["contact_id"], "purpose": "service_contact",
                                           "channel": "any", "granted": 0, "source": "staff_do_not_contact",
                                           "note": reason, "captured_by": actor["id"], "captured_at": now})
        if status == "closed":
            T.cancel_open_tasks(db, actor["id"], "opportunity", opp["id"], f"Opportunity moved to {STAGE_LABELS[to]}")
        if to == "nurture":
            T.cancel_open_tasks(db, actor["id"], "opportunity", opp["id"], "Moved to nurture")
            T.create_task(db, actor, title=f"Nurture follow-up: {opp['contact_name']}",
                          assignee_id=opp["primary_owner_id"] or actor["id"], due_at=follow_up_due,
                          task_type="follow_up", priority="low", entity_type="opportunity", entity_id=opp["id"])
        audit.record(db, actor["id"], "seller.stage", "opportunity", opp["id"],
                     {"from": opp["stage"], "to": to, "reason": reason})


# -- Calls & notes -------------------------------------------------------------------
NO_ANSWER = {"no_answer", "switched_off", "busy"}


def log_call(db, actor, opp: dict, outcome: str, note: str, complete_task_id: int | None,
             next_title: str | None, next_due: str | None) -> None:
    if not user_can(db, actor, "crm.log_activity"):
        raise PermissionDenied("crm.log_activity")
    if outcome not in T.CALL_OUTCOME_LABELS:
        raise ValidationError({"outcome": "Choose the call outcome."})
    now = now_iso()
    with db:
        T.add_activity(db, actor["id"], "opportunity", opp["id"], "call", (note or "").strip() or None, outcome)
        from .timeline import mark_contacted
        mark_contacted(db, opp["id"], now)
        if outcome == "do_not_contact":
            change_stage(db, actor, opp, "do_not_contact", (note or "").strip() or "Requested on call")
            return
        if next_title and next_title.strip():
            T.create_task(db, actor, title=next_title, assignee_id=opp["primary_owner_id"] or actor["id"],
                          due_at=next_due, task_type="call" if outcome in NO_ANSWER else "follow_up",
                          priority="normal", entity_type="opportunity", entity_id=opp["id"])
        if complete_task_id:
            T.complete_task(db, actor, complete_task_id, f"Call: {T.CALL_OUTCOME_LABELS[outcome]}"
                            + (f" — {note.strip()}" if note and note.strip() else ""))
        # automatic early-stage progression
        if opp["stage"] == "new" and outcome in NO_ANSWER | {"wrong_number"}:
            _auto_stage(db, actor, opp, "contact_attempted")
        elif opp["stage"] in ("new", "contact_attempted") and outcome in ("connected", "callback_requested"):
            _auto_stage(db, actor, opp, "connected")
        open_count = db.scalar("SELECT COUNT(*) FROM tasks WHERE entity_type = 'opportunity' AND entity_id = ? "
                               "AND status = 'open'", (opp["id"],))
        if open_count == 0:
            raise ValidationError({"next_title": "Add a next action — every active seller needs one."})


def _auto_stage(db, actor, opp, to):
    now = now_iso()
    db.update("opportunities", opp["id"], {"stage": to, "updated_at": now})
    db.insert("stage_history", {"opportunity_id": opp["id"], "from_stage": opp["stage"], "to_stage": to,
                                "reason": "Automatic from call outcome", "actor_id": actor["id"], "at": now})
    T.add_activity(db, actor["id"], "opportunity", opp["id"], "stage_change",
                   f"{STAGE_LABELS[opp['stage']]} → {STAGE_LABELS[to]} (from call outcome)")


def add_note(db, actor, opp, body: str) -> None:
    if not user_can(db, actor, "crm.log_activity"):
        raise PermissionDenied("crm.log_activity")
    if not (body or "").strip():
        raise ValidationError({"body": "Write a note."})
    with db:
        T.add_activity(db, actor["id"], "opportunity", opp["id"], "note", body.strip()[:4000])
        # @mentions: notify users whose first name is mentioned with @
        for u in db.all("SELECT id, full_name FROM users WHERE is_active = 1"):
            first = u["full_name"].split()[0].lower()
            if f"@{first}" in body.lower() and u["id"] != actor["id"]:
                T.notify(db, u["id"], "mention", f"{actor['full_name']} mentioned you on {opp['ref_code']}",
                         f"/sellers/{opp['id']}")


def assign_owner(db, actor, opp, new_owner_id: int, reason: str) -> None:
    if not user_can(db, actor, "crm.assign"):
        raise PermissionDenied("crm.assign")
    new = db.one("SELECT id, full_name, is_active FROM users WHERE id = ?", (new_owner_id,))
    if not new or not new["is_active"]:
        raise ValidationError({"owner_id": "Choose an active user."})
    if not (reason or "").strip():
        raise ValidationError({"reason": "Give a reason for the handoff."})
    with db:
        db.update("opportunities", opp["id"], {"primary_owner_id": new_owner_id, "updated_at": now_iso()})
        T.add_activity(db, actor["id"], "opportunity", opp["id"], "assignment",
                       f"Primary owner: {opp['owner_name'] or 'none'} → {new['full_name']}. {reason.strip()}")
        T.notify(db, new_owner_id, "assignment", f"You now own {opp['ref_code']} ({opp['salon_name']})",
                 f"/sellers/{opp['id']}")
        audit.record(db, actor["id"], "seller.assign", "opportunity", opp["id"],
                     {"from": opp["primary_owner_id"], "to": new_owner_id, "reason": reason})


# -- SWOT ----------------------------------------------------------------------------
def add_swot(db, actor, opp, kind, statement, source, confidence, open_question):
    if not user_can(db, actor, "crm.edit"):
        raise PermissionDenied("crm.edit")
    if kind not in ("strength", "weakness", "opportunity", "threat") or not (statement or "").strip():
        raise ValidationError({"statement": "Choose a type and write the statement."})
    if source not in ("seller", "staff_observation", "document", "third_party"):
        raise ValidationError({"source": "Choose a source."})
    if confidence not in ("low", "medium", "high"):
        raise ValidationError({"confidence": "Choose confidence."})
    with db:
        db.insert("swot_items", {"listing_id": opp["listing_id"], "kind": kind, "statement": statement.strip()[:1000],
                                 "source": source, "confidence": confidence,
                                 "open_question": (open_question or "").strip() or None,
                                 "created_by": actor["id"], "created_at": now_iso()})
        T.add_activity(db, actor["id"], "opportunity", opp["id"], "data_update", f"SWOT {kind} added")


# -- Checklist & approval (§6.6) -----------------------------------------------------
def set_checklist(db, actor, opp, item_key: str, status: str, note: str | None) -> None:
    if not user_can(db, actor, "crm.edit"):
        raise PermissionDenied("crm.edit")
    if item_key not in dict(LISTING_CHECKLIST) or status not in dict(CHECKLIST_STATUSES):
        raise ValidationError("Invalid checklist update.")
    if status == "reviewed" and not user_can(db, actor, "evidence.review"):
        raise PermissionDenied("evidence.review")
    with db:
        db.execute("UPDATE listing_checklist SET status = ?, note = ?, updated_by = ?, updated_at = ? "
                   "WHERE listing_id = ? AND item_key = ?",
                   (status, (note or "").strip() or None, actor["id"], now_iso(), opp["listing_id"], item_key))
        T.add_activity(db, actor["id"], "opportunity", opp["id"], "data_update",
                       f"Checklist: {dict(LISTING_CHECKLIST)[item_key]} → {dict(CHECKLIST_STATUSES)[status]}")


def approval_blockers(db, listing_id: int) -> list[str]:
    rows = db.all("SELECT item_key, status FROM listing_checklist WHERE listing_id = ?", (listing_id,))
    labels = dict(LISTING_CHECKLIST)
    return [labels[r["item_key"]] for r in rows if r["status"] not in ("reviewed", "not_applicable")]


def decide_listing(db, actor, opp: dict, decision: str, conditions: str | None) -> None:
    if not user_can(db, actor, "listing.approve"):
        audit.record(db, actor["id"], "listing.decide", "opportunity", opp["id"], {"decision": decision},
                     outcome="denied")
        raise PermissionDenied("listing.approve")
    listing = load_listing(db, opp["listing_id"])
    if decision == "approved":
        blockers = approval_blockers(db, listing["id"])
        if blockers:
            raise ValidationError({"approval": "Checklist items not reviewed: " + "; ".join(blockers)})
    elif decision == "rejected":
        if not (conditions or "").strip():
            raise ValidationError({"approval": "Give a reason for rejecting."})
    else:
        raise ValidationError("Unknown decision.")
    now = now_iso()
    with db:
        db.insert("listing_approvals", {"listing_id": listing["id"], "decision": decision,
                                        "data_version": listing["data_version"],
                                        "conditions": (conditions or "").strip() or None,
                                        "actor_id": actor["id"], "at": now})
        db.update("seller_listings", listing["id"], {
            "approval_state": decision, "updated_at": now,
            "approved_version": listing["data_version"] if decision == "approved" else listing["approved_version"]})
        if decision == "approved" and opp["stage"] != "listing_approved":
            db.update("opportunities", opp["id"], {"stage": "listing_approved", "status": "active", "updated_at": now})
            db.insert("stage_history", {"opportunity_id": opp["id"], "from_stage": opp["stage"],
                                        "to_stage": "listing_approved", "reason": "Listing approved",
                                        "actor_id": actor["id"], "at": now})
        T.add_activity(db, actor["id"], "opportunity", opp["id"], "approval",
                       f"Listing {decision} at data version {listing['data_version']}"
                       + (f": {conditions.strip()}" if conditions and conditions.strip() else ""))
        if opp["primary_owner_id"] and opp["primary_owner_id"] != actor["id"]:
            T.notify(db, opp["primary_owner_id"], "approval", f"{opp['ref_code']} listing {decision}",
                     f"/sellers/{opp['id']}")
        audit.record(db, actor["id"], "listing.decide", "seller_listing", listing["id"],
                     {"decision": decision, "data_version": listing["data_version"]})


def request_review(db, actor, opp) -> None:
    listing = load_listing(db, opp["listing_id"])
    if listing["approval_state"] in ("approved", "in_review"):
        raise ValidationError("Listing is already approved or in review.")
    with db:
        db.update("seller_listings", listing["id"], {"approval_state": "in_review", "updated_at": now_iso()})
        approver = db.one("SELECT id FROM users WHERE role = 'super_admin' AND is_active = 1 ORDER BY id LIMIT 1")
        if approver:
            T.create_task(db, actor, title=f"Review listing {opp['ref_code']} ({opp['salon_name']})",
                          assignee_id=approver["id"], due_at=T.first_contact_due(24 * 60), task_type="approval",
                          priority="high", entity_type="opportunity", entity_id=opp["id"])
        T.add_activity(db, actor["id"], "opportunity", opp["id"], "approval", "Listing submitted for review")


def contact_consent_state(db, contact_id: int) -> dict:
    rows = db.all("SELECT * FROM contact_consents WHERE contact_id = ? ORDER BY captured_at, id", (contact_id,))
    latest = {}
    for r in rows:
        latest[r["purpose"]] = r
    return latest


def timeline(db, opp_id: int) -> list[dict]:
    return db.all("SELECT a.*, u.full_name AS actor_name FROM activities a LEFT JOIN users u ON u.id = a.actor_id "
                  "WHERE a.entity_type = 'opportunity' AND a.entity_id = ? ORDER BY a.at DESC, a.id DESC", (opp_id,))


def days_since(value: str | None) -> int | None:
    from ..timeutil import parse_iso
    dt = parse_iso(value)
    return (now_utc() - dt).days if dt else None


# -- Respondent submission (public quick form, Milestone 4) ---------------------------
def _seller_team_owner(db) -> int | None:
    """Default routing suggestion (§5.4): seller team first, else an active super admin."""
    row = db.one("SELECT id FROM users WHERE is_active = 1 AND team = 'seller' AND role IN ('team_member','team_lead') "
                 "ORDER BY id LIMIT 1") or db.one("SELECT id FROM users WHERE is_active = 1 AND role = 'super_admin' "
                                                 "ORDER BY id LIMIT 1")
    return row["id"] if row else None


def submit_respondent_form(db, payload: dict, source: str = "landing_page", is_synthetic: int = 0) -> int:
    """A seller's own quick-form submission. Captured as seller-declared with a review task; never linked to,
    merged with or overwriting an existing contact (possible duplicates go to the review queue)."""
    errors = {}
    name = (payload.get("full_name") or "").strip()
    salon = (payload.get("salon_name") or "").strip()
    city = (payload.get("city") or "").strip()
    locality = (payload.get("locality") or "").strip() or city
    if not name:
        errors["full_name"] = "Please enter your name."
    if not salon:
        errors["salon_name"] = "Please enter the salon name."
    if not city:
        errors["city"] = "Please enter the city."
    if source not in SOURCE_LABELS:
        errors["source"] = "Unknown source."
    phone, perr = normalize_indian_mobile(payload.get("phone"))
    if perr:
        errors["phone"] = perr
    values, qerrors = _parse_fields(payload, [f.key for f in FIELDS if f.quick])
    errors.update(qerrors)
    if errors:
        raise ValidationError(errors)
    owner_id = _seller_team_owner(db)
    dups = find_duplicates(db, phone, salon, locality)
    now = now_iso()
    with db:
        contact_id = db.insert("contacts", {
            "full_name": name, "phone_normalized": phone, "phone_raw": payload.get("phone"),
            "email": (payload.get("email") or "").strip().lower() or None, "city": city, "locality": locality,
            "is_synthetic": is_synthetic, "created_at": now, "updated_at": now})
        for d in dups:
            db.insert("duplicate_reviews", {"contact_id": contact_id, "candidate_contact_id": d["id"],
                                            "reason": d["reason"], "created_at": now})
        business_id = db.insert("salon_businesses", {
            "name": salon, "city": city, "locality": locality, "owner_contact_id": contact_id,
            "is_synthetic": is_synthetic, "created_at": now, "updated_at": now})
        opp_id = db.insert("opportunities", {
            "kind": "seller", "contact_id": contact_id, "business_id": business_id, "stage": "new",
            "status": "active", "primary_owner_id": owner_id, "priority": "normal", "source": source,
            "source_detail": "Respondent submission", "is_synthetic": is_synthetic, "created_at": now,
            "updated_at": now})
        db.update("opportunities", opp_id, {"ref_code": f"SL-{opp_id:05d}"})
        listing_id = db.insert("seller_listings", {"opportunity_id": opp_id, "business_id": business_id,
                                                   "created_at": now, "updated_at": now})
        _apply_values(db, {"id": None}, listing_id, values, source="public_form", bump_version=False)
        for key, _ in LISTING_CHECKLIST:
            db.execute("INSERT INTO listing_checklist (listing_id, item_key, status, updated_at) VALUES (?, ?, ?, ?)",
                       (listing_id, key, "not_requested", now))
        db.insert("contact_consents", {
            "contact_id": contact_id, "purpose": "service_contact", "channel": "any",
            "granted": 1 if payload.get("consent_contact") else 0, "source": "public_form",
            "text_version": payload.get("consent_version") or "public-form-consent-v0.1", "captured_at": now})
        db.insert("stage_history", {"opportunity_id": opp_id, "from_stage": None, "to_stage": "new",
                                    "reason": "Seller submitted the quick form", "actor_id": None, "at": now})
        T.add_activity(db, None, "opportunity", opp_id, "system",
                       "Seller submitted the quick form" + (" — flagged for duplicate review" if dups else ""))
        T.create_task(db, None, title=f"Review seller submission: {name} ({salon})", assignee_id=owner_id,
                      due_at=T.first_contact_due(FIRST_CONTACT_SLA_MIN), task_type="review", priority="high",
                      entity_type="opportunity", entity_id=opp_id, is_synthetic=is_synthetic)
        audit.record(db, None, "seller.submitted", "opportunity", opp_id, {"source": source, "duplicates": len(dups)})
    return opp_id
