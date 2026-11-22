"""Buyer / investor management domain logic (PRD §7.1–7.3, §7.2A, §5.3–5.5; Milestone 3).

Covers: buyer lead creation with duplicate detection, the respondent-submission path used by public
forms (M4) with privileged-field stripping (UAT-02, UAT-09), the acquisition mandate (hard all-in
ceiling, ranked localities and exclusions, Must/Prefer/Flexible/Avoid preferences), reviewer-only
funding and criteria review, the seriousness score (§7.3) and buyer funnel stage rules (§7.1).

Matching itself is Milestone 5: nothing here produces or implies a match.
"""
from __future__ import annotations

from .. import audit
from ..permissions import user_can
from ..timeutil import now_iso
from . import sellers as S
from . import tasks as T
from .buyer_fields import (BY_KEY, CHOICE_DIMENSIONS, FIELDS, FUNDING_LABELS, IMPORTANCE_LABELS, PRIVILEGED_KEYS,
                           RESALE_NEEDS, SECTIONS)
from .common import normalize_indian_mobile
from .errors import PermissionDenied, ValidationError
from .seller_fields import ANSWER_STATE_LABELS

ENTITY = "buyer_mandate"

# -- Funnel (§7.1) -------------------------------------------------------------------
STAGES = [
    ("new", "New"), ("contact_attempted", "Contact attempted"), ("connected", "Connected"),
    ("requirements_captured", "Requirements captured"), ("form_sent", "Detailed form sent"),
    ("form_received", "Form received"), ("basic_qualified", "Basic qualified"),
    ("funding_review", "Funding / authority review"), ("qualified_shortlist", "Qualified to shortlist"),
    ("shortlist_shared", "Shortlist shared"), ("viewing_offer", "Viewing / offer"),
    ("transaction", "Transaction in progress"), ("closed_won", "Closed won"),
]
HOLDING = [("nurture", "Nurture"), ("exploring_only", "Exploring only"), ("unqualified", "Unqualified"),
           ("closed_lost", "Closed lost"), ("do_not_contact", "Do not contact")]
STAGE_LABELS = dict(STAGES + HOLDING)
CLOSED_STAGES = {"unqualified", "closed_lost", "do_not_contact", "closed_won"}
HOLDING_STAGES = {"nurture", "exploring_only"}
REASON_REQUIRED = {"nurture", "exploring_only", "unqualified", "closed_lost", "do_not_contact", "closed_won"}
# Stages that need modules not built yet. Blocked with an honest reason instead of being simulated.
FUTURE_STAGES = {
    "shortlist_shared": "Sharing a shortlist arrives with the Match Desk (Milestone 5)",
    "viewing_offer": "Viewings and offers arrive with Deals (Milestone 6)",
    "transaction": "Transactions arrive with Deals (Milestone 6)",
    "closed_won": "Closing a buyer as won arrives with Deals (Milestone 6)",
}

SOURCES = S.SOURCES
SOURCE_LABELS = S.SOURCE_LABELS
FIRST_CONTACT_SLA_MIN = S.FIRST_CONTACT_SLA_MIN


def status_for(stage: str) -> str:
    if stage in CLOSED_STAGES:
        return "closed"
    if stage in HOLDING_STAGES:
        return "holding"
    return "active"


# -- Loading -------------------------------------------------------------------------
def load(db, opp_id: int) -> dict | None:
    return db.one(
        "SELECT o.*, c.full_name AS contact_name, c.phone_normalized, c.alt_phone_normalized, c.email, "
        "c.city AS contact_city, c.locality AS contact_locality, c.do_not_contact, u.full_name AS owner_name, "
        "m.id AS mandate_id FROM opportunities o JOIN contacts c ON c.id = o.contact_id "
        "LEFT JOIN users u ON u.id = o.primary_owner_id LEFT JOIN buyer_mandates m ON m.opportunity_id = o.id "
        "WHERE o.id = ? AND o.kind = 'buyer'", (opp_id,))


def load_mandate(db, mandate_id: int) -> dict:
    return db.one("SELECT * FROM buyer_mandates WHERE id = ?", (mandate_id,))


def provenance(db, mandate_id: int) -> dict[str, dict]:
    rows = db.all("SELECT * FROM field_provenance WHERE entity_type = ? AND entity_id = ?", (ENTITY, mandate_id))
    return {r["field_key"]: r for r in rows}


def localities(db, mandate_id: int) -> dict[str, list[dict]]:
    rows = db.all("SELECT * FROM buyer_localities WHERE mandate_id = ? ORDER BY kind, rank_order, id", (mandate_id,))
    return {"preferred": [r for r in rows if r["kind"] == "preferred"],
            "excluded": [r for r in rows if r["kind"] == "excluded"]}


def preferences(db, mandate_id: int) -> dict[tuple[str, str], str]:
    rows = db.all("SELECT dimension, choice, importance FROM buyer_preferences WHERE mandate_id = ?", (mandate_id,))
    return {(r["dimension"], r["choice"]): r["importance"] for r in rows}


def can_view_funding_evidence(db, user: dict, opp: dict) -> bool:
    """Buyer funding proof (evidence notes + uploaded proof). Founder decision D-22 (23 Nov 2026): visible to the
    whole team by default — anyone who can open buyer records. Roles without CRM access (finance) need an explicit
    grant. Recording a funding review stays reviewer-only (set_funding_review)."""
    return (user_can(db, user, "crm.view")
            or user_can(db, user, "buyer.view_funding", "opportunity", opp["id"]))


def masked_mandate(db, user: dict, opp: dict, mandate: dict) -> dict:
    out = dict(mandate)
    if not can_view_funding_evidence(db, user, opp):
        out["funding_note"] = None
    return out


# -- Field parsing / updating (mirrors sellers, own dictionary) ---------------------
def _parse_fields(data: dict, keys: list[str]) -> tuple[dict, dict]:
    values, errors = {}, {}
    for key in keys:
        fd = BY_KEY[key]
        raw = data.get(key)
        state = data.get(key + "__state") or ""
        if state and state not in ANSWER_STATE_LABELS:
            errors[key] = "Invalid answer state"
            continue
        try:
            val = S._parse_one(fd, raw)
        except ValueError as e:
            errors[key] = str(e)
            continue
        if val is not None:
            values[key] = (val, "value")
        elif state and state != "value":
            values[key] = (None, state)
        elif key in data or (key + "__state") in data:
            values[key] = (None, None)
    return values, errors


def _split_privileged(data: dict, allow: frozenset = frozenset()) -> tuple[dict, list[str]]:
    """UAT-09: returns (payload without privileged fields, names of privileged fields that carried a value)."""
    blocked = PRIVILEGED_KEYS - allow
    attempted = sorted(k for k in data if k in blocked and str(data.get(k) or "").strip())
    return {k: v for k, v in data.items() if k not in blocked}, attempted


def _audit_ignored(db, actor_id, opp_id, attempted: list[str], channel: str) -> None:
    if attempted:
        audit.record(db, actor_id, "buyer.privileged_field_ignored", "opportunity", opp_id,
                     {"fields": attempted, "channel": channel}, outcome="denied")


def _apply_values(db, actor_id, mandate_id, values: dict, source: str, evidence: str,
                  bump_version: bool = True) -> list[str]:
    mandate = load_mandate(db, mandate_id)
    prov = provenance(db, mandate_id)
    changed = []
    now = now_iso()
    new_version = mandate["data_version"] + (1 if bump_version else 0)
    for key, (val, state) in values.items():
        old_val = mandate.get(key)
        old_state = prov.get(key, {}).get("answer_state")
        if old_val == val and old_state == state:
            continue
        if state is None and old_state is None:
            continue
        db.execute(f"UPDATE buyer_mandates SET {key} = ? WHERE id = ?", (val, mandate_id))
        db.execute("DELETE FROM field_provenance WHERE entity_type = ? AND entity_id = ? AND field_key = ?",
                   (ENTITY, mandate_id, key))
        if state is not None:
            db.insert("field_provenance", {"entity_type": ENTITY, "entity_id": mandate_id, "field_key": key,
                                           "answer_state": state, "evidence_status": evidence,
                                           "source": source, "updated_by": actor_id, "updated_at": now})
        _log_change(db, actor_id, mandate_id, new_version, key, old_val, val, old_state, state, now)
        changed.append(key)
    if changed and bump_version:
        db.execute("UPDATE buyer_mandates SET data_version = ?, updated_at = ? WHERE id = ?",
                   (new_version, now, mandate_id))
    return changed


def _log_change(db, actor_id, mandate_id, version, key, old_val, new_val, old_state, new_state, now=None):
    db.insert("field_change_log", {"entity_type": ENTITY, "entity_id": mandate_id, "data_version": version,
                                   "field_key": key, "old_value": None if old_val is None else str(old_val),
                                   "new_value": None if new_val is None else str(new_val), "old_state": old_state,
                                   "new_state": new_state, "actor_id": actor_id, "at": now or now_iso()})


def _bump(db, mandate_id: int) -> int:
    v = db.scalar("SELECT data_version FROM buyer_mandates WHERE id = ?", (mandate_id,)) + 1
    db.execute("UPDATE buyer_mandates SET data_version = ?, updated_at = ? WHERE id = ?", (v, now_iso(), mandate_id))
    return v


def _set_importance(db, actor_id, mandate_id, dimension, choice, importance, version) -> bool:
    """importance '' = blank (row removed). Returns True if changed."""
    old = db.scalar("SELECT importance FROM buyer_preferences WHERE mandate_id = ? AND dimension = ? AND choice = ?",
                    (mandate_id, dimension, choice))
    new = importance or None
    if new is not None and new not in IMPORTANCE_LABELS:
        raise ValidationError({dimension: "Choose Must have, Prefer, Flexible, Avoid or Not important."})
    if old == new:
        return False
    db.execute("DELETE FROM buyer_preferences WHERE mandate_id = ? AND dimension = ? AND choice = ?",
               (mandate_id, dimension, choice))
    if new:
        db.insert("buyer_preferences", {"mandate_id": mandate_id, "dimension": dimension, "choice": choice,
                                        "importance": new, "updated_by": actor_id, "updated_at": now_iso()})
    _log_change(db, actor_id, mandate_id, version, f"importance:{dimension}" + (f":{choice}" if choice else ""),
                old, new, None, None)
    return True


# -- Creation (§5.6, §7.2) -----------------------------------------------------------
def _validate_contact(data: dict) -> tuple[dict, str | None, dict]:
    errors = {}
    name = (data.get("full_name") or "").strip()
    city = (data.get("city") or "").strip()
    if not name:
        errors["full_name"] = "Contact name is required."
    if not city:
        errors["city"] = "City is required."
    phone, perr = normalize_indian_mobile(data.get("phone"))
    if perr:
        errors["phone"] = perr
    return {"name": name, "city": city, "locality": (data.get("locality") or "").strip()}, phone, errors


def _buyer_team_owner(db) -> int | None:
    """Default routing suggestion (§5.4): buyer team first, else an active super admin. Configurable later."""
    row = db.one("SELECT id FROM users WHERE is_active = 1 AND team = 'buyer' AND role IN ('team_member','team_lead') "
                 "ORDER BY id LIMIT 1") or db.one("SELECT id FROM users WHERE is_active = 1 AND role = 'super_admin' "
                                                 "ORDER BY id LIMIT 1")
    return row["id"] if row else None


def _insert_buyer(db, actor_id, *, contact: dict, phone, data, contact_id, owner_id, source, source_detail,
                  synthetic, evidence, source_tag, values, consent_source, dups, linked) -> int:
    now = now_iso()
    if not contact_id:
        contact_id = db.insert("contacts", {
            "full_name": contact["name"], "phone_normalized": phone, "phone_raw": data.get("phone"),
            "email": (data.get("email") or "").strip().lower() or None, "city": contact["city"],
            "locality": contact["locality"] or None, "is_synthetic": synthetic, "created_by": actor_id,
            "created_at": now, "updated_at": now})
        for d in dups:
            db.insert("duplicate_reviews", {"contact_id": contact_id, "candidate_contact_id": d["id"],
                                            "reason": d["reason"], "created_at": now})
    opp_id = db.insert("opportunities", {
        "kind": "buyer", "contact_id": contact_id, "business_id": None, "stage": "new", "status": "active",
        "primary_owner_id": owner_id, "priority": data.get("priority") if data.get("priority") in T.PRIORITY_RANK
        else "normal", "source": source, "source_detail": source_detail, "is_synthetic": synthetic,
        "created_by": actor_id, "created_at": now, "updated_at": now})
    db.update("opportunities", opp_id, {"ref_code": f"BY-{opp_id:05d}"})
    mandate_id = db.insert("buyer_mandates", {"opportunity_id": opp_id, "created_at": now, "updated_at": now})
    if not values.get("preferred_city"):
        values["preferred_city"] = (contact["city"], "value")
    _apply_values(db, actor_id, mandate_id, values, source=source_tag, evidence=evidence, bump_version=False)
    if contact["locality"]:
        db.insert("buyer_localities", {"mandate_id": mandate_id, "kind": "preferred", "city": contact["city"],
                                       "locality": contact["locality"], "rank_order": 1, "added_by": actor_id,
                                       "added_at": now})
    db.insert("contact_consents", {
        "contact_id": contact_id, "purpose": "service_contact", "channel": "any",
        "granted": 1 if data.get("consent_contact") else 0, "source": consent_source,
        "text_version": "staff-call-v0.1" if actor_id else "public-form-v0.1", "captured_by": actor_id,
        "captured_at": now})
    if data.get("consent_share_criteria"):
        db.insert("contact_consents", {
            "contact_id": contact_id, "purpose": "share_teaser", "channel": "any", "granted": 1,
            "source": consent_source, "text_version": "buyer-criteria-share-v0.1",
            "note": "Buyer allows a suitable portion of their criteria to be shared with sellers/representatives",
            "captured_by": actor_id, "captured_at": now})
    db.insert("stage_history", {"opportunity_id": opp_id, "from_stage": None, "to_stage": "new",
                                "reason": "Lead created", "actor_id": actor_id, "at": now})
    return opp_id


def create_lead(db, actor: dict, data: dict, dedupe_decision: str | None = None,
                first_task_due: str | None = None) -> dict:
    """Staff quick screen. Returns {"opportunity_id": id} or {"duplicates": [...]} when a decision is needed."""
    if not user_can(db, actor, "crm.create"):
        raise PermissionDenied("crm.create")
    data, attempted = _split_privileged(data, allow=frozenset({"owner_id"}))  # staff may pick an owner
    contact, phone, errors = _validate_contact(data)
    source = data.get("source") or ""
    if source not in SOURCE_LABELS:
        errors["source"] = "Choose where this lead came from."
    owner_id = int(data.get("owner_id") or actor["id"])
    if owner_id != actor["id"] and not user_can(db, actor, "crm.assign"):
        errors["owner_id"] = "You can create leads for yourself; ask a team lead to assign others."
    values, ferrors = _parse_fields(data, [f.key for f in FIELDS if f.quick])
    errors.update(ferrors)
    if errors:
        raise ValidationError(errors)

    dups = S.find_duplicates(db, phone, "", "")
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
    synthetic = int(data.get("is_synthetic") or 0)
    with db:
        opp_id = _insert_buyer(
            db, actor["id"], contact=contact, phone=phone, data=data, contact_id=link_contact_id, owner_id=owner_id,
            source=source, source_detail=(data.get("source_note") or "").strip() or None, synthetic=synthetic,
            evidence="buyer_declared", source_tag=f"staff_entry:{source}", values=values,
            consent_source=f"staff_entry:{source}", dups=[] if link_contact_id else dups, linked=link_contact_id)
        _audit_ignored(db, actor["id"], opp_id, attempted, "staff_entry")
        T.add_activity(db, actor["id"], "opportunity", opp_id, "system",
                       f"Buyer lead created from {SOURCE_LABELS[source]}"
                       + (" (linked to existing contact)" if link_contact_id else "")
                       + (" — flagged for duplicate review" if dups and not link_contact_id else ""))
        T.create_task(db, actor, title=f"First call: {contact['name']} (buyer)", assignee_id=owner_id,
                      due_at=first_task_due or T.first_contact_due(FIRST_CONTACT_SLA_MIN), task_type="first_contact",
                      priority="high", entity_type="opportunity", entity_id=opp_id, is_synthetic=synthetic)
        audit.record(db, actor["id"], "buyer.create", "opportunity", opp_id,
                     {"source": source, "linked_contact": link_contact_id, "duplicate_candidates": len(dups)})
    return {"opportunity_id": opp_id}


def submit_respondent_form(db, payload: dict, source: str = "landing_page", is_synthetic: int = 0) -> int:
    """A buyer's own submission (public quick form — the link itself ships in Milestone 4).

    UAT-02: requirements are captured as buyer-declared, a review task enters the queue and NO internal
    approval is granted. UAT-09: privileged fields in the payload are ignored and the attempt is audited.
    Never links to or changes an existing contact automatically; possible duplicates go to review.
    """
    clean, attempted = _split_privileged(payload)
    contact, phone, errors = _validate_contact(clean)
    if source not in SOURCE_LABELS:
        errors["source"] = "Unknown source."
    values, ferrors = _parse_fields(clean, [f.key for f in FIELDS])
    errors.update(ferrors)
    if errors:
        raise ValidationError(errors)
    owner_id = _buyer_team_owner(db)
    dups = S.find_duplicates(db, phone, "", "")
    with db:
        opp_id = _insert_buyer(
            db, None, contact=contact, phone=phone, data=clean, contact_id=None, owner_id=owner_id, source=source,
            source_detail="Respondent submission", synthetic=is_synthetic, evidence="buyer_declared",
            source_tag="public_form", values=values, consent_source="public_form", dups=dups, linked=None)
        _audit_ignored(db, None, opp_id, attempted, "public_form")
        T.add_activity(db, None, "opportunity", opp_id, "system",
                       "Buyer submitted their requirements" + (" — flagged for duplicate review" if dups else ""))
        T.create_task(db, None, title=f"Review buyer submission: {contact['name']}", assignee_id=owner_id,
                      due_at=T.first_contact_due(FIRST_CONTACT_SLA_MIN), task_type="review", priority="high",
                      entity_type="opportunity", entity_id=opp_id, is_synthetic=is_synthetic)
        audit.record(db, None, "buyer.submitted", "opportunity", opp_id, {"source": source, "duplicates": len(dups)})
    return opp_id


def update_fields(db, actor: dict, opp: dict, data: dict, section: str) -> list[str]:
    if not user_can(db, actor, "crm.edit"):
        raise PermissionDenied("crm.edit")
    if section not in dict(SECTIONS):
        raise ValidationError("Unknown section.")
    data, attempted = _split_privileged(data)
    keys = [f.key for f in FIELDS if f.section == section]
    values, errors = _parse_fields(data, keys)
    if errors:
        raise ValidationError(errors)
    mid = opp["mandate_id"]
    with db:
        _audit_ignored(db, actor["id"], opp["id"], attempted, "staff_entry")
        target_version = db.scalar("SELECT data_version FROM buyer_mandates WHERE id = ?", (mid,)) + 1
        changed = _apply_values(db, actor["id"], mid, values, source="staff_entry", evidence="buyer_declared")
        imp_changed = [f.key for f in FIELDS
                       if f.section == section and f.importance and (f.key + "__importance") in data
                       and _set_importance(db, actor["id"], mid, f.key, "", data.get(f.key + "__importance"),
                                           target_version)]
        if imp_changed and not changed:
            _bump(db, mid)  # value changes already bumped the version once
        if changed or imp_changed:
            labels = [BY_KEY[k].label for k in changed] + [f"importance of {BY_KEY[k].label}" for k in imp_changed]
            T.add_activity(db, actor["id"], "opportunity", opp["id"], "data_update", "Updated " + ", ".join(labels))
            audit.record(db, actor["id"], "buyer.update", "opportunity", opp["id"],
                         {"section": section, "fields": changed, "importance": imp_changed})
    return changed + imp_changed


def set_choice_preferences(db, actor: dict, opp: dict, data: dict) -> list[str]:
    """Form fields pref__<dimension>__<choice> = importance or '' (blank)."""
    if not user_can(db, actor, "crm.edit"):
        raise PermissionDenied("crm.edit")
    mid = opp["mandate_id"]
    changed = []
    with db:
        version = db.scalar("SELECT data_version FROM buyer_mandates WHERE id = ?", (mid,)) + 1
        for dim, _label, opts in CHOICE_DIMENSIONS:
            for value, _ in opts:
                key = f"pref__{dim}__{value}"
                if key in data and _set_importance(db, actor["id"], mid, dim, value, data.get(key), version):
                    changed.append(f"{dim}:{value}")
        if changed:
            _bump(db, mid)
            T.add_activity(db, actor["id"], "opportunity", opp["id"], "data_update",
                           f"Updated {len(changed)} format / structure preference(s)")
            audit.record(db, actor["id"], "buyer.preferences", "opportunity", opp["id"], {"changed": changed})
    return changed


# -- Localities ----------------------------------------------------------------------
def add_locality(db, actor, opp, kind: str, city: str, locality: str, note: str | None = None) -> None:
    if not user_can(db, actor, "crm.edit"):
        raise PermissionDenied("crm.edit")
    city, locality = (city or "").strip(), (locality or "").strip()
    if kind not in ("preferred", "excluded"):
        raise ValidationError({"kind": "Choose preferred or excluded."})
    if not city or not locality:
        raise ValidationError({"locality": "City and locality are both required."})
    mid = opp["mandate_id"]
    existing = db.one("SELECT kind FROM buyer_localities WHERE mandate_id = ? AND lower(city) = lower(?) AND "
                      "lower(locality) = lower(?)", (mid, city, locality))
    if existing:
        raise ValidationError({"locality": f"{locality} is already listed as {existing['kind']}."})
    with db:
        rank = (db.scalar("SELECT MAX(rank_order) FROM buyer_localities WHERE mandate_id = ? AND kind = ?",
                          (mid, kind)) or 0) + 1
        db.insert("buyer_localities", {"mandate_id": mid, "kind": kind, "city": city, "locality": locality,
                                       "rank_order": rank, "note": (note or "").strip() or None,
                                       "added_by": actor["id"], "added_at": now_iso()})
        v = _bump(db, mid)
        _log_change(db, actor["id"], mid, v, f"locality:{kind}", None, f"{locality}, {city}", None, None)
        T.add_activity(db, actor["id"], "opportunity", opp["id"], "data_update",
                       f"{'Preferred' if kind == 'preferred' else 'Excluded'} locality added: {locality}, {city}")


def remove_locality(db, actor, opp, locality_id: int) -> None:
    if not user_can(db, actor, "crm.edit"):
        raise PermissionDenied("crm.edit")
    mid = opp["mandate_id"]
    row = db.one("SELECT * FROM buyer_localities WHERE id = ? AND mandate_id = ?", (locality_id, mid))
    if not row:
        raise ValidationError("Locality not found on this buyer.")
    with db:
        db.execute("DELETE FROM buyer_localities WHERE id = ?", (locality_id,))
        _renumber(db, mid, row["kind"])
        v = _bump(db, mid)
        _log_change(db, actor["id"], mid, v, f"locality:{row['kind']}", f"{row['locality']}, {row['city']}", None,
                    None, None)
        T.add_activity(db, actor["id"], "opportunity", opp["id"], "data_update",
                       f"Removed {row['kind']} locality {row['locality']}")


def move_locality(db, actor, opp, locality_id: int, direction: str) -> None:
    if not user_can(db, actor, "crm.edit"):
        raise PermissionDenied("crm.edit")
    mid = opp["mandate_id"]
    rows = localities(db, mid)["preferred"]
    ids = [r["id"] for r in rows]
    if locality_id not in ids:
        raise ValidationError("Only preferred localities are ranked.")
    i = ids.index(locality_id)
    j = i - 1 if direction == "up" else i + 1
    if j < 0 or j >= len(ids):
        return
    ids[i], ids[j] = ids[j], ids[i]
    with db:
        for n, lid in enumerate(ids, start=1):
            db.execute("UPDATE buyer_localities SET rank_order = ? WHERE id = ?", (n, lid))
        v = _bump(db, mid)
        names = {r["id"]: r["locality"] for r in rows}
        _log_change(db, actor["id"], mid, v, "locality:ranking", None, " > ".join(names[x] for x in ids), None, None)


def _renumber(db, mid, kind):
    for n, r in enumerate(db.all("SELECT id FROM buyer_localities WHERE mandate_id = ? AND kind = ? "
                                 "ORDER BY rank_order, id", (mid, kind)), start=1):
        db.execute("UPDATE buyer_localities SET rank_order = ? WHERE id = ?", (n, r["id"]))


# -- Reviewer-only actions -----------------------------------------------------------
def set_funding_review(db, actor, opp, status: str, note: str | None) -> None:
    """Only reviewers set funding status (PRD §7.2 'evidence status is staff-reviewed only')."""
    if not user_can(db, actor, "evidence.review"):
        audit.record(db, actor["id"], "buyer.funding_review", "opportunity", opp["id"], {"status": status},
                     outcome="denied")
        raise PermissionDenied("evidence.review")
    if status not in FUNDING_LABELS:
        raise ValidationError({"status": "Choose a funding status."})
    if status in ("staff_reviewed", "verified", "insufficient") and not (note or "").strip():
        raise ValidationError({"note": "Record what evidence was seen and its limitations."})
    now = now_iso()
    with db:
        db.execute("UPDATE buyer_mandates SET funding_status = ?, funding_note = ?, funding_reviewed_by = ?, "
                   "funding_reviewed_at = ?, updated_at = ? WHERE id = ?",
                   (status, (note or "").strip() or None, actor["id"], now, now, opp["mandate_id"]))
        T.add_activity(db, actor["id"], "opportunity", opp["id"], "approval",
                       f"Funding position marked: {FUNDING_LABELS[status]}")  # evidence detail stays restricted
        audit.record(db, actor["id"], "buyer.funding_review", "opportunity", opp["id"], {"status": status})


def criteria_review_state(mandate: dict) -> str:
    if mandate.get("criteria_reviewed_version") is None:
        return "not_reviewed"
    return "current" if mandate["criteria_reviewed_version"] == mandate["data_version"] else "stale"


def criteria_review_blockers(db, mandate: dict) -> list[str]:
    prov = provenance(db, mandate["id"])
    out = []
    if prov.get("need_type", {}).get("answer_state") != "value":
        out.append("what the buyer wants (need type)")
    if mandate.get("all_in_max") is None:
        out.append("the hard all-in ceiling")
    if not localities(db, mandate["id"])["preferred"] and not mandate.get("preferred_city"):
        out.append("at least one preferred city or locality")
    return out


def confirm_criteria(db, actor, opp) -> None:
    """A reviewer confirms the current requirements as the effective criteria (§7.2A)."""
    if not user_can(db, actor, "evidence.review"):
        raise PermissionDenied("evidence.review")
    mandate = load_mandate(db, opp["mandate_id"])
    missing = criteria_review_blockers(db, mandate)
    if missing:
        raise ValidationError({"criteria": "Cannot confirm yet — missing " + ", ".join(missing) + "."})
    now = now_iso()
    with db:
        db.execute("UPDATE buyer_mandates SET criteria_reviewed_version = ?, criteria_reviewed_by = ?, "
                   "criteria_reviewed_at = ? WHERE id = ?", (mandate["data_version"], actor["id"], now, mandate["id"]))
        T.add_activity(db, actor["id"], "opportunity", opp["id"], "approval",
                       f"Requirements confirmed as effective criteria (data version {mandate['data_version']})")
        audit.record(db, actor["id"], "buyer.criteria_confirmed", "opportunity", opp["id"],
                     {"data_version": mandate["data_version"]})


# -- Completeness & seriousness (§7.3) ----------------------------------------------
def completeness(mandate: dict, prov: dict, locs: dict) -> dict:
    per, missing_material = {}, []
    total = answered = 0
    for key, label in SECTIONS:
        fs = [f for f in FIELDS if f.section == key]
        a = sum(1 for f in fs if prov.get(f.key, {}).get("answer_state") in ("value", "not_applicable"))
        if key == "geography":
            fs_total = len(fs) + 1
            a += 1 if locs["preferred"] else 0
        else:
            fs_total = len(fs)
        per[key] = {"label": label, "answered": a, "total": fs_total}
        total += fs_total
        answered += a
        for f in fs:
            if f.material and prov.get(f.key, {}).get("answer_state") not in ("value", "not_applicable"):
                missing_material.append(f)
    return {"pct": round(100 * answered / total) if total else 0, "answered": answered, "total": total,
            "sections": per, "missing_material": missing_material}


SERIOUSNESS_VERSION = "buyer-seriousness v0.1 (PRD §7.3 proposal, pending founder approval D-21)"


def seriousness_score(db, opp: dict, mandate: dict, prov: dict, locs: dict, prefs: dict) -> dict:
    """Advisory 100-point score. Missing answers earn nothing; it never overrides consent/funding/authority gates."""
    factors = []

    def add(name, pts, mx, reason):
        factors.append({"name": name, "points": pts, "max": mx, "reason": reason})

    def answered(k):
        return prov.get(k, {}).get("answer_state") == "value"

    # 1. Clear acquisition objective and preferred format — 15
    need = mandate.get("need_type")
    clear = need in RESALE_NEEDS or need == "new_salon"
    fmt = any(d in ("gender_format", "business_model", "positioning") and imp in ("must", "prefer", "avoid")
              for (d, _c), imp in prefs.items())
    review = criteria_review_state(mandate) == "current"
    add("Objective & preferred format", (5 if clear else 0) + (5 if fmt else 0) + (5 if review else 0), 15,
        "; ".join([("clear objective" if clear else "objective unclear or exploring"),
                   ("format preference stated" if fmt else "no format preference yet"),
                   ("requirements reviewed" if review else "requirements not (currently) reviewed")]))
    # 2. Defined realistic all-in budget — 20
    ceiling = mandate.get("all_in_max") is not None
    incl = sum(1 for k in ("includes_deposit", "includes_stock", "includes_working_capital") if answered(k))
    consistent = ceiling and (mandate.get("budget_max") is None or mandate["all_in_max"] >= mandate["budget_max"])
    add("Defined all-in budget", (10 if ceiling else 0) + 2 * incl + (4 if consistent and incl == 3 else 0), 20,
        ("all-in ceiling set" if ceiling else "no all-in ceiling") + f"; {incl}/3 inclusions confirmed"
        + ("" if consistent or not ceiling else "; ceiling below the price band — inconsistent"))
    # 3. Funding position documented — 25 (only a reviewer can award the evidence points)
    fs = mandate.get("funding_status")
    ev = {"verified": 15, "staff_reviewed": 10}.get(fs, 0)
    add("Funding position documented", (5 if answered("funding_source") else 0) + (5 if answered("cash_ready") else 0)
        + ev, 25, f"source {'stated' if answered('funding_source') else 'unknown'}; readiness "
                  f"{'stated' if answered('cash_ready') else 'unknown'}; evidence: {FUNDING_LABELS.get(fs, fs)}")
    # 4. Decision authority / stakeholder alignment — 15
    dm = {"yes": 10, "shared": 6}.get(mandate.get("decision_maker"), 0)
    approver = answered("payment_approver")
    dm_label = dict(BY_KEY["decision_maker"].options).get(mandate.get("decision_maker"), "unknown")
    add("Decision authority", dm + (5 if approver else 0), 15,
        f"decision maker: {dm_label}; payment approver {'identified' if approver else 'not identified'}")
    # 5. Acquisition timeframe and responsiveness — 15
    tw = {"30d": 8, "90d": 6, "180d": 4, "later": 1}.get(mandate.get("purchase_window"), 0)
    connected = db.scalar("SELECT COUNT(*) FROM activities WHERE entity_type = 'opportunity' AND entity_id = ? "
                          "AND kind = 'call' AND call_outcome IN ('connected','callback_requested')", (opp["id"],))
    add("Timeframe & responsiveness", tw + (7 if connected else 0), 15,
        f"window {tw}/8; {connected} connected call(s)")
    # 6. Willingness to review deals and complete diligence — 10
    visits = mandate.get("visit_availability") in ("yes", "weekends")
    share = db.scalar("SELECT granted FROM contact_consents WHERE contact_id = ? AND purpose = 'share_teaser' "
                      "ORDER BY captured_at DESC, id DESC LIMIT 1", (opp["contact_id"],))
    add("Willingness to review deals", (5 if visits else 0) + (5 if share else 0), 10,
        ("available for visits" if visits else "visit availability unknown") + "; "
        + ("consents to sharing criteria" if share else "no consent to share criteria"))
    score = sum(f["points"] for f in factors)
    band = "High priority" if score >= 80 else "Qualified – developing" if score >= 60 else "Nurture / needs information"
    last = db.scalar("SELECT MAX(at) FROM field_change_log WHERE entity_type = ? AND entity_id = ?",
                     (ENTITY, mandate["id"]))
    reviewer = None
    if mandate.get("criteria_reviewed_by"):
        reviewer = db.scalar("SELECT full_name FROM users WHERE id = ?", (mandate["criteria_reviewed_by"],))
    return {"score": score, "band": band, "factors": factors, "version": SERIOUSNESS_VERSION,
            "data_updated_at": last or mandate["created_at"], "reviewer": reviewer,
            "reviewed_at": mandate.get("criteria_reviewed_at"),
            "advisory": "Advisory only. Consent, identity, authority and funding gates apply regardless of score."}


# -- Stage rules (§7.1) --------------------------------------------------------------
def _service_consent(db, contact_id) -> bool:
    return bool(db.scalar("SELECT granted FROM contact_consents WHERE contact_id = ? AND purpose = 'service_contact' "
                          "ORDER BY captured_at DESC, id DESC LIMIT 1", (contact_id,)))


def check_transition(db, user, opp, mandate, prov, to: str) -> tuple[bool, str | None]:
    if to not in STAGE_LABELS:
        return False, "Unknown stage"
    if opp["status"] == "closed" and not user_can(db, user, "listing.approve"):
        return False, "Closed opportunities can only be reopened by an approver"
    if to in FUTURE_STAGES:
        return False, FUTURE_STAGES[to]
    if not user_can(db, user, "stage.operational"):
        return False, "You do not have permission to change stages"
    locs = localities(db, mandate["id"])
    if to in ("requirements_captured", "basic_qualified", "funding_review", "qualified_shortlist"):
        missing = criteria_review_blockers(db, mandate)
        if missing:
            return False, "Needs " + ", ".join(missing)
    if to in ("basic_qualified", "funding_review", "qualified_shortlist"):
        if mandate.get("need_type") == "exploring":
            return False, "Buyer is only exploring — use 'Exploring only' or confirm a real need first"
        if prov.get("decision_maker", {}).get("answer_state") != "value":
            return False, "Needs the decision-maker answer"
        if not _service_consent(db, opp["contact_id"]):
            return False, "Needs recorded contact consent"
    if to == "funding_review" and prov.get("cash_ready", {}).get("answer_state") != "value":
        return False, "Needs the buyer's declared cash readiness"
    if to == "qualified_shortlist":
        if not user_can(db, user, "evidence.review"):
            return False, "Only a reviewer can qualify a buyer to shortlist"
        if mandate.get("need_type") not in RESALE_NEEDS:
            return False, "New-salon buyers are not resale buyers — change the need type only with their confirmation"
        if mandate.get("funding_status") not in ("staff_reviewed", "verified"):
            return False, "Funding must be staff-reviewed or verified"
        if criteria_review_state(mandate) != "current":
            return False, "Requirements must be confirmed by a reviewer (and not changed since)"
        if not locs["preferred"]:
            return False, "Needs at least one ranked preferred locality"
    return True, None


def transition_options(db, user, opp, mandate, prov) -> list[dict]:
    out = []
    for code, label in STAGES + HOLDING:
        if code == opp["stage"]:
            continue
        ok, why = check_transition(db, user, opp, mandate, prov, code)
        out.append({"code": code, "label": label, "allowed": ok, "why": why})
    return out


def change_stage(db, actor, opp: dict, to: str, reason: str | None, follow_up_due: str | None = None) -> None:
    mandate = load_mandate(db, opp["mandate_id"])
    prov = provenance(db, mandate["id"])
    ok, why = check_transition(db, actor, opp, mandate, prov, to)
    if not ok:
        audit.record(db, actor["id"], "buyer.stage", "opportunity", opp["id"], {"to": to, "blocked": why},
                     outcome="denied")
        raise ValidationError({"stage": why})
    reason = (reason or "").strip()
    if to in REASON_REQUIRED and not reason:
        raise ValidationError({"reason": f"A reason is required for '{STAGE_LABELS[to]}'."})
    if to in HOLDING_STAGES and not follow_up_due:
        raise ValidationError({"follow_up_due": f"'{STAGE_LABELS[to]}' needs a follow-up date so the buyer is not "
                                                "forgotten."})
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
            T.cancel_open_tasks(db, actor["id"], "opportunity", opp["id"], f"Buyer moved to {STAGE_LABELS[to]}")
        if to in HOLDING_STAGES:
            T.cancel_open_tasks(db, actor["id"], "opportunity", opp["id"], f"Moved to {STAGE_LABELS[to]}")
            T.create_task(db, actor, title=f"{STAGE_LABELS[to]} follow-up: {opp['contact_name']}",
                          assignee_id=opp["primary_owner_id"] or actor["id"], due_at=follow_up_due,
                          task_type="follow_up", priority="low", entity_type="opportunity", entity_id=opp["id"])
        audit.record(db, actor["id"], "buyer.stage", "opportunity", opp["id"],
                     {"from": opp["stage"], "to": to, "reason": reason})


# -- Calls, notes, handoff -----------------------------------------------------------
def log_call(db, actor, opp: dict, outcome: str, note: str, complete_task_id: int | None,
             next_title: str | None, next_due: str | None) -> None:
    if not user_can(db, actor, "crm.log_activity"):
        raise PermissionDenied("crm.log_activity")
    if outcome not in T.CALL_OUTCOME_LABELS:
        raise ValidationError({"outcome": "Choose the call outcome."})
    now = now_iso()
    with db:
        T.add_activity(db, actor["id"], "opportunity", opp["id"], "call", (note or "").strip() or None, outcome)
        db.update("opportunities", opp["id"], {"last_contact_at": now, "updated_at": now})
        if outcome == "do_not_contact":
            change_stage(db, actor, opp, "do_not_contact", (note or "").strip() or "Requested on call")
            return
        if next_title and next_title.strip():
            T.create_task(db, actor, title=next_title, assignee_id=opp["primary_owner_id"] or actor["id"],
                          due_at=next_due, task_type="call" if outcome in S.NO_ANSWER else "follow_up",
                          priority="normal", entity_type="opportunity", entity_id=opp["id"])
        if complete_task_id:
            T.complete_task(db, actor, complete_task_id, f"Call: {T.CALL_OUTCOME_LABELS[outcome]}"
                            + (f" — {note.strip()}" if note and note.strip() else ""))
        if opp["stage"] == "new" and outcome in S.NO_ANSWER | {"wrong_number"}:
            _auto_stage(db, actor, opp, "contact_attempted")
        elif opp["stage"] in ("new", "contact_attempted") and outcome in ("connected", "callback_requested"):
            _auto_stage(db, actor, opp, "connected")
        if db.scalar("SELECT COUNT(*) FROM tasks WHERE entity_type = 'opportunity' AND entity_id = ? "
                     "AND status = 'open'", (opp["id"],)) == 0:
            raise ValidationError({"next_title": "Add a next action — every active buyer needs one."})


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
        for u in db.all("SELECT id, full_name FROM users WHERE is_active = 1"):
            first = u["full_name"].split()[0].lower()
            if f"@{first}" in body.lower() and u["id"] != actor["id"]:
                T.notify(db, u["id"], "mention", f"{actor['full_name']} mentioned you on {opp['ref_code']}",
                         f"/buyers/{opp['id']}")


def assign_owner(db, actor, opp, new_owner_id: int, reason: str) -> None:
    if not user_can(db, actor, "crm.assign"):
        raise PermissionDenied("crm.assign")
    new = db.one("SELECT id, full_name, is_active, role FROM users WHERE id = ?", (new_owner_id,))
    if not new or not new["is_active"] or new["role"] == "finance":
        raise ValidationError({"owner_id": "Choose an active team user."})
    if not (reason or "").strip():
        raise ValidationError({"reason": "Give a reason for the handoff."})
    with db:
        db.update("opportunities", opp["id"], {"primary_owner_id": new_owner_id, "updated_at": now_iso()})
        T.add_activity(db, actor["id"], "opportunity", opp["id"], "assignment",
                       f"Primary owner: {opp['owner_name'] or 'none'} → {new['full_name']}. {reason.strip()}")
        T.notify(db, new_owner_id, "assignment", f"You now own buyer {opp['ref_code']} ({opp['contact_name']})",
                 f"/buyers/{opp['id']}")
        audit.record(db, actor["id"], "buyer.assign", "opportunity", opp["id"],
                     {"from": opp["primary_owner_id"], "to": new_owner_id, "reason": reason})


def timeline(db, opp_id: int) -> list[dict]:
    return S.timeline(db, opp_id)
