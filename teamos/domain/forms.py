"""Forms & data requests (PRD §5.6, §10.2, §10.4; FR-009/010/011/013/043/047; Milestone 4).

Lifecycle of a personal link (state model §5.6, restricted to what we can actually observe):
  sent → opened (our server served the page) → started (first draft saved) → submitted
       → accepted | more_info_required          (or revoked / expired at any open point)
Delivery/read are never claimed: copy-link has no delivery signal, and the M4 WhatsApp provider is a mock.

Answers never write straight into a CRM record. A submission holds the raw answers; a staff reviewer
compares them with the current values and accepts field by field. Reviewer-only fields (funding status,
qualification, approvals) cannot be set by any form — attempts are dropped and audited (UAT-09, UAT-42).
"""
from __future__ import annotations

import hashlib
import json
import secrets
from datetime import timedelta

from .. import audit, files, messaging
from ..permissions import can_view_restricted_seller, user_can
from ..timeutil import fmt_ist, now_iso, now_utc, to_iso
from . import buyer_fields as BFD
from . import buyers as B
from . import form_templates as FT
from . import sellers as S
from . import tasks as T
from .errors import PermissionDenied, ValidationError

OPEN_STATES = ("sent", "opened", "started")
STATUS_LABELS = {"sent": "Sent", "opened": "Opened", "started": "In progress", "submitted": "Submitted — needs review",
                 "accepted": "Reviewed", "more_info_required": "More information requested", "revoked": "Revoked",
                 "expired": "Expired"}
RESPONDENT_STATE_KEYS = {k for k, _ in FT.RESPONDENT_STATES}
PUBLIC_LIMIT_PER_HOUR = 5
CHASE_AFTER_HOURS = 48


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def hash_ip(ip: str | None, secret: str) -> str:
    return hashlib.sha256(f"{secret}:{ip or ''}".encode()).hexdigest()[:32]


def load_opportunity(db, opp_id: int) -> dict | None:
    return db.one("SELECT o.*, c.full_name AS contact_name, c.phone_normalized, c.do_not_contact, "
                  "b.name AS salon_name FROM opportunities o JOIN contacts c ON c.id = o.contact_id "
                  "LEFT JOIN salon_businesses b ON b.id = o.business_id WHERE o.id = ?", (opp_id,))


def opp_path(opp: dict) -> str:
    return f"/{'buyers' if opp['kind'] == 'buyer' else 'sellers'}/{opp['id']}"


# -- Sending -------------------------------------------------------------------------
def send_blockers(db, opp: dict, channel: str) -> list[str]:
    out = []
    if opp["status"] == "closed":
        out.append("the opportunity is closed")
    if opp.get("do_not_contact"):
        out.append("the contact asked not to be contacted")
    consent = db.scalar("SELECT granted FROM contact_consents WHERE contact_id = ? AND purpose = 'service_contact' "
                        "ORDER BY captured_at DESC, id DESC LIMIT 1", (opp["contact_id"],))
    if not consent:
        out.append("no recorded consent to contact")
    if channel == "whatsapp_mock" and not opp.get("phone_normalized"):
        out.append("no mobile number")
    return out


def create_invitation(db, actor: dict, opp_id: int, template_key: str, channel: str,
                      field_keys: list[str] | None = None, doc_types: list[str] | None = None,
                      purpose: str | None = None, link_base: str = "") -> dict:
    if not user_can(db, actor, "crm.log_activity"):
        raise PermissionDenied("crm.log_activity")
    opp = load_opportunity(db, opp_id)
    if not opp:
        raise ValidationError("Opportunity not found.")
    kind = opp["kind"]
    if template_key not in ("seller_deep", "buyer_deep", "targeted"):
        raise ValidationError({"template": "Choose a form."})
    if template_key != "targeted" and FT.TEMPLATES[template_key]["kind"] != kind:
        raise ValidationError({"template": f"That form is for {FT.TEMPLATES[template_key]['kind']}s."})
    if channel not in ("copy_link", "whatsapp_mock"):
        raise ValidationError({"channel": "Choose how to send the link."})
    allowed = {f.key for f in FT.requestable_fields(kind)}
    field_keys = [k for k in (field_keys or []) if k]
    bad = [k for k in field_keys if k not in allowed]
    if bad:
        raise ValidationError({"fields": "These cannot be requested from a customer: " + ", ".join(bad)})
    doc_types = [d for d in (doc_types or []) if d]
    if template_key == "targeted" and not field_keys and not doc_types:
        raise ValidationError({"fields": "Pick at least one question or document to request."})
    blockers = send_blockers(db, opp, channel)
    if blockers:
        audit.record(db, actor["id"], "form.send_blocked", "opportunity", opp_id, {"reasons": blockers},
                     outcome="denied")
        raise ValidationError({"send": "Cannot send: " + "; ".join(blockers) + "."})
    t = FT.build(template_key, kind, field_keys, doc_types)
    token = secrets.token_urlsafe(32)
    now = now_iso()
    expires = to_iso(now_utc() + timedelta(days=t.get("expiry_days", 7)))
    link = f"{link_base.rstrip('/')}/f/i/{token}"
    comm = None
    with db:
        if template_key != "targeted":  # one live deep link per record: older ones stop working
            for old in db.all("SELECT id FROM form_invitations WHERE opportunity_id = ? AND template_key = ? AND "
                              "status IN ('sent','opened','started')", (opp_id, template_key)):
                db.update("form_invitations", old["id"], {"status": "revoked", "closed_at": now})
        inv_id = db.insert("form_invitations", {
            "token_hash": _hash(token), "template_key": template_key, "template_version": t["version"],
            "kind": kind, "opportunity_id": opp_id, "contact_id": opp["contact_id"],
            "field_keys": json.dumps(field_keys) if template_key == "targeted" else None,
            "doc_types": json.dumps(doc_types) if template_key == "targeted" else None,
            "purpose": (purpose or "").strip()[:500] or None, "channel": channel, "status": "sent",
            "expires_at": expires, "created_by": actor["id"], "created_at": now})
        label = "detailed form" if template_key != "targeted" else f"request for {len(field_keys)} detail(s)" + \
            (f" and {len(doc_types)} document(s)" if doc_types else "")
        if channel == "whatsapp_mock":
            comm = _send_mock_whatsapp(db, actor, opp, inv_id, template_key, link, expires)
            how = ("via WhatsApp — MOCK provider, nothing was actually sent" if comm["status"] == "provider_accepted"
                   else f"via WhatsApp — FAILED ({comm['detail']})")
        else:
            how = "as a link for staff to share"
        T.add_activity(db, actor["id"], "opportunity", opp_id, "system",
                       f"Personal {label} link created {how} (expires {fmt_ist(expires, '%d %b %Y')})")
        audit.record(db, actor["id"], "form.invitation_created", "opportunity", opp_id,
                     {"invitation_id": inv_id, "template": t["version"], "channel": channel,
                      "fields": field_keys, "docs": doc_types,
                      "provider_message_id": comm and comm.get("provider_message_id")})
    return {"invitation_id": inv_id, "token": token, "link": link, "communication": comm}


def _send_mock_whatsapp(db, actor, opp, inv_id, template_key, link, expires) -> dict:
    mtpl = "deep_form_link" if template_key != "targeted" else "targeted_request"
    variables = {"name": opp["contact_name"].split()[0], "sender": actor["full_name"].split()[0],
                 "expires": fmt_ist(expires, "%d %b %Y")}
    result = messaging.provider().send_template(opp.get("phone_normalized"), mtpl, {**variables, "link": link})
    body = messaging.render(mtpl, {**variables, "link": "[personal link]"})  # the secret token is never stored
    cid = db.insert("communications", {
        "contact_id": opp["contact_id"], "opportunity_id": opp["id"], "invitation_id": inv_id, "channel": "whatsapp",
        "provider": messaging.PROVIDER_NAME, "template_key": mtpl, "body": body, "status": result["status"],
        "provider_message_id": result.get("provider_message_id"), "detail": result.get("detail"),
        "sent_by": actor["id"], "created_at": now_iso()})
    return {"id": cid, **result}


def revoke(db, actor, inv_id: int) -> None:
    if not user_can(db, actor, "crm.log_activity"):
        raise PermissionDenied("crm.log_activity")
    inv = db.one("SELECT * FROM form_invitations WHERE id = ?", (inv_id,))
    if not inv or inv["status"] not in OPEN_STATES:
        raise ValidationError("Only open links can be revoked.")
    with db:
        db.update("form_invitations", inv_id, {"status": "revoked", "closed_at": now_iso()})
        T.add_activity(db, actor["id"], "opportunity", inv["opportunity_id"], "system", "Form link revoked")
        audit.record(db, actor["id"], "form.invitation_revoked", "opportunity", inv["opportunity_id"],
                     {"invitation_id": inv_id})


# -- Respondent side -----------------------------------------------------------------
def resolve(db, token: str) -> dict | None:
    if not token or len(token) > 100:
        return None
    inv = db.one("SELECT * FROM form_invitations WHERE token_hash = ?", (_hash(token),))
    if inv and inv["status"] in OPEN_STATES and inv["expires_at"] < now_iso():
        db.update("form_invitations", inv["id"], {"status": "expired", "closed_at": now_iso()})
        inv["status"] = "expired"
    return inv


def template_for(inv: dict) -> dict:
    return FT.build(inv["template_key"], inv["kind"], json.loads(inv["field_keys"] or "[]"),
                    json.loads(inv["doc_types"] or "[]"))


def mark_opened(db, inv: dict) -> None:
    if inv["status"] == "sent":
        db.update("form_invitations", inv["id"], {"status": "opened", "opened_at": now_iso()})
        inv["status"] = "opened"


def draft_for(db, inv: dict) -> dict | None:
    return db.one("SELECT * FROM form_submissions WHERE invitation_id = ? AND status = 'draft' ORDER BY id DESC LIMIT 1",
                  (inv["id"],))


def prefill(db, inv: dict) -> dict:
    """Current non-restricted declared values, so the respondent corrects rather than retypes."""
    opp = load_opportunity(db, inv["opportunity_id"])
    out = {}
    if inv["kind"] == "seller":
        listing = db.one("SELECT * FROM seller_listings WHERE opportunity_id = ?", (opp["id"],))
        prov = S.provenance(db, listing["id"])
        for f in S.FIELDS:
            if f.privacy == "restricted":
                continue
            p = prov.get(f.key)
            if p and p["answer_state"] == "value" and listing.get(f.key) is not None:
                out[f.key] = {"value": str(listing[f.key]), "state": "value"}
            elif p and p["answer_state"] in RESPONDENT_STATE_KEYS:
                out[f.key] = {"value": None, "state": p["answer_state"]}
    else:
        m = db.one("SELECT * FROM buyer_mandates WHERE opportunity_id = ?", (opp["id"],))
        prov = B.provenance(db, m["id"])
        for f in BFD.FIELDS:
            p = prov.get(f.key)
            if p and p["answer_state"] == "value" and m.get(f.key) is not None:
                out[f.key] = {"value": str(m[f.key]), "state": "value"}
            elif p and p["answer_state"] in RESPONDENT_STATE_KEYS:
                out[f.key] = {"value": None, "state": p["answer_state"]}
        for (dim, choice), imp in B.preferences(db, m["id"]).items():
            out[f"importance:{dim}" if not choice else f"pref:{dim}:{choice}"] = {"value": imp}
        locs = B.localities(db, m["id"])
        out["localities"] = {"preferred": [l["locality"] for l in locs["preferred"]],
                             "excluded": [l["locality"] for l in locs["excluded"]]}
    return out


def collect_answers(t: dict, form) -> tuple[dict, dict, list[str]]:
    """Validate a respondent's form post. Returns (answers, errors, ignored_privileged_keys)."""
    kind = t["kind"]
    answers, errors = {}, {}
    for key in FT.template_field_keys(t):
        fd = FT.field_def(kind, key)
        raw = (form.get(key) or "").strip()
        state = form.get(key + "__state") or ""
        if raw:
            try:
                S._parse_one(fd, raw)
            except ValueError as e:
                errors[key] = str(e)
                continue
            answers[key] = {"value": raw[:2000], "state": "value"}
        elif state in RESPONDENT_STATE_KEYS:
            answers[key] = {"value": None, "state": state}
        if getattr(fd, "importance", False):
            imp = form.get(key + "__importance") or ""
            if imp in BFD.IMPORTANCE_LABELS:
                answers[f"importance:{key}"] = {"value": imp}
    if "choice_prefs" in t["extras"]:
        for dim, _label, opts in BFD.CHOICE_DIMENSIONS:
            for v, _ in opts:
                imp = form.get(f"pref__{dim}__{v}") or ""
                if imp in BFD.IMPORTANCE_LABELS:
                    answers[f"pref:{dim}:{v}"] = {"value": imp}
    if "localities" in t["extras"]:
        def lines(name):
            seen, out = set(), []
            for part in (form.get(name) or "").replace(",", "\n").splitlines():
                p = part.strip()[:60]
                if p and p.lower() not in seen:
                    seen.add(p.lower())
                    out.append(p)
            return out[:10]
        pref, excl = lines("localities_preferred"), lines("localities_excluded")
        if pref or excl:
            answers["localities"] = {"preferred": pref, "excluded": excl}
    ignored = sorted(k for k in form.keys() if k in BFD.PRIVILEGED_KEYS and str(form.get(k) or "").strip())
    return answers, errors, ignored


def save_response(db, inv: dict, form, uploads: dict, ip_hash: str | None, user_agent: str | None,
                  final: bool) -> dict:
    """Save a draft (final=False) or submit (final=True). `uploads` = {doc_type: (bytes, filename)}."""
    if inv["status"] not in OPEN_STATES:
        raise ValidationError("This link is no longer active.")
    t = template_for(inv)
    answers, errors, ignored = collect_answers(t, form)
    if errors:
        raise ValidationError(errors)
    share = "share_consent" in t["extras"] and bool(form.get("consent_share_criteria"))
    now = now_iso()
    with db:
        draft = draft_for(db, inv)
        payload = {"answers": json.dumps(answers), "ignored_fields": json.dumps(ignored) if ignored else None,
                   "consent": json.dumps({"share_teaser": share}) if "share_consent" in t["extras"] else None,
                   "ip_hash": ip_hash, "user_agent": (user_agent or "")[:200], "updated_at": now}
        if draft:
            db.update("form_submissions", draft["id"], payload)
            sid = draft["id"]
        else:
            sid = db.insert("form_submissions", {**payload, "invitation_id": inv["id"], "template_key": t["key"],
                                                 "template_version": t["version"], "kind": t["kind"],
                                                 "opportunity_id": inv["opportunity_id"],
                                                 "contact_id": inv["contact_id"], "status": "draft",
                                                 "created_at": now})
        stored = []
        for doc_type, (data, filename) in uploads.items():
            if doc_type not in t["docs"] or not data:
                continue
            try:
                meta = files.store(data, filename)
            except files.UploadRejected as e:
                raise ValidationError({f"doc__{doc_type}": str(e)})
            db.insert("documents", {**meta, "opportunity_id": inv["opportunity_id"], "submission_id": sid,
                                    "invitation_id": inv["id"], "doc_type": doc_type, "uploaded_via": "form",
                                    "created_at": now})
            stored.append(doc_type)
        if ignored:
            audit.record(db, None, "form.privileged_field_ignored", "opportunity", inv["opportunity_id"],
                         {"fields": ignored, "submission_id": sid}, outcome="denied")
        if not final:
            if inv["status"] in ("sent", "opened"):
                db.update("form_invitations", inv["id"], {"status": "started", "started_at": now})
            return {"submission_id": sid, "documents": stored}
        has_docs = db.scalar("SELECT COUNT(*) FROM documents WHERE submission_id = ?", (sid,))
        if not answers and not has_docs:
            raise ValidationError({"_": "Please answer at least one question or attach a document before submitting."})
        opp = load_opportunity(db, inv["opportunity_id"])
        if share:  # a permission the respondent gives directly — recorded in the consent ledger as given
            db.insert("contact_consents", {"contact_id": inv["contact_id"], "purpose": "share_teaser",
                                           "channel": "any", "granted": 1, "source": f"form:{t['version']}",
                                           "text_version": FT.CONSENT_TEXT["share_teaser"][0], "captured_at": now})
        owner = opp["primary_owner_id"] or inv["created_by"]
        tid = T.create_task(db, None, title=f"Review form answers: {opp['contact_name']} ({opp['ref_code']})",
                            assignee_id=owner, due_at=T.first_contact_due(24 * 60), task_type="review",
                            priority="high", entity_type="opportunity", entity_id=opp["id"])
        db.update("form_submissions", sid, {"status": "submitted", "submitted_at": now, "review_task_id": tid})
        db.update("form_invitations", inv["id"], {"status": "submitted", "submitted_at": now,
                                                  "started_at": inv.get("started_at") or now})
        n_docs = has_docs
        T.add_activity(db, None, "opportunity", opp["id"], "system",
                       f"Customer submitted {t['version']}: {len(answers)} answer(s)"
                       + (f", {n_docs} document(s)" if n_docs else "") + " — waiting for staff review")
        T.notify(db, owner, "form_submitted", f"{opp['contact_name']} submitted a form ({opp['ref_code']})",
                 f"/forms/submissions/{sid}")
        audit.record(db, None, "form.submitted", "opportunity", opp["id"],
                     {"submission_id": sid, "template": t["version"], "answers": len(answers), "documents": n_docs})
    return {"submission_id": sid, "documents": stored}


# -- Public quick forms (FR-009 / FR-010) -------------------------------------------
def public_rate_limited(db, ip_hash: str) -> bool:
    since = to_iso(now_utc() - timedelta(hours=1))
    return db.scalar("SELECT COUNT(*) FROM public_form_hits WHERE ip_hash = ? AND at >= ? AND outcome = 'submitted'",
                     (ip_hash, since)) >= PUBLIC_LIMIT_PER_HOUR


def record_public_hit(db, ip_hash: str, form_key: str, outcome: str) -> None:
    db.insert("public_form_hits", {"ip_hash": ip_hash, "form": form_key, "outcome": outcome, "at": now_iso()})


def submit_public(db, form_key: str, form, ip_hash: str, user_agent: str | None, is_synthetic: int = 0) -> int:
    t = FT.build(form_key)
    if not t.get("public"):
        raise ValidationError("Unknown form.")
    answers, errors, ignored = collect_answers(t, form)
    contact = {k: (form.get(k) or "").strip()[:200] for k in t["contact"]}
    for k in t["contact"]:
        if k in FT.CONTACT_REQUIRED and not contact.get(k):
            errors[k] = "Required."
    if not form.get("consent_contact"):
        errors["consent_contact"] = "Please confirm we may contact you about this enquiry."
    if errors:
        raise ValidationError(errors)
    payload = {**contact, "consent_contact": "1", "consent_version": FT.CONSENT_TEXT["service_contact"][0]}
    for k, a in answers.items():
        if FT.field_def(t["kind"], k):
            payload[k] = a["value"] or ""
            if a["state"] != "value":
                payload[k + "__state"] = a["state"]
    for k in ignored:  # pass through so the domain layer audits the attempt against the new record
        payload[k] = form.get(k)
    if t["kind"] == "buyer" and form.get("consent_share_criteria"):
        payload["consent_share_criteria"] = "1"
    with db:
        if t["kind"] == "seller":
            opp_id = S.submit_respondent_form(db, payload, source="landing_page", is_synthetic=is_synthetic)
            if ignored:
                audit.record(db, None, "form.privileged_field_ignored", "opportunity", opp_id,
                             {"fields": ignored, "form": form_key}, outcome="denied")
        else:
            opp_id = B.submit_respondent_form(db, payload, source="landing_page", is_synthetic=is_synthetic)
        task = db.one("SELECT id FROM tasks WHERE entity_type = 'opportunity' AND entity_id = ? AND task_type = 'review' "
                      "ORDER BY id DESC LIMIT 1", (opp_id,))
        db.insert("form_submissions", {
            "invitation_id": None, "template_key": form_key, "template_version": t["version"], "kind": t["kind"],
            "opportunity_id": opp_id, "contact_id": db.scalar("SELECT contact_id FROM opportunities WHERE id = ?",
                                                              (opp_id,)),
            "status": "submitted", "answers": json.dumps({**answers, "_contact": contact}),
            "ignored_fields": json.dumps(ignored) if ignored else None,
            "consent": json.dumps({"service_contact": True,
                                   "share_teaser": bool(form.get("consent_share_criteria"))}),
            "ip_hash": ip_hash, "user_agent": (user_agent or "")[:200], "created_at": now_iso(),
            "updated_at": now_iso(), "submitted_at": now_iso(), "review_task_id": task and task["id"]})
        record_public_hit(db, ip_hash, form_key, "submitted")
    return opp_id


# -- Staff review (FR-011 / FR-047) --------------------------------------------------
def _display(kind, key, value):
    fd = FT.field_def(kind, key)
    if value is None or value == "":
        return None
    if fd and fd.ftype == "inr":
        from .common import format_inr
        try:
            return format_inr(S._parse_one(fd, str(value)))
        except ValueError:
            return str(value)
    if fd and fd.options:
        return dict(fd.options).get(str(value), str(value))
    return str(value)


def review_rows(db, sub: dict) -> list[dict]:
    answers = json.loads(sub["answers"] or "{}")
    kind = sub["kind"]
    opp = load_opportunity(db, sub["opportunity_id"])
    rows = []
    if kind == "seller":
        rec = db.one("SELECT * FROM seller_listings WHERE opportunity_id = ?", (opp["id"],))
        prov = S.provenance(db, rec["id"])
        prefs, locs = {}, None
    else:
        rec = db.one("SELECT * FROM buyer_mandates WHERE opportunity_id = ?", (opp["id"],))
        prov = B.provenance(db, rec["id"])
        prefs = B.preferences(db, rec["id"])
        locs = B.localities(db, rec["id"])
    for key, a in answers.items():
        if key == "_contact":
            continue
        if key == "localities":
            have = {l["locality"].lower() for l in (locs["preferred"] + locs["excluded"])} if locs else set()
            for kk in ("preferred", "excluded"):
                for name in a.get(kk, []):
                    rows.append({"key": f"locality:{kk}:{name}", "label": f"{kk.title()} locality",
                                 "submitted": name, "current": "already listed" if name.lower() in have else None,
                                 "changed": name.lower() not in have})
            continue
        if key.startswith("pref:") or key.startswith("importance:"):
            parts = key.split(":")
            dim, choice = parts[1], (parts[2] if len(parts) > 2 else "")
            label = (BFD.CHOICE_BY_KEY[dim][0] + " — " + BFD.option_label(dim, choice)) if choice else \
                f"How much it matters: {BFD.BY_KEY[dim].label}"
            cur = prefs.get((dim, choice))
            rows.append({"key": key, "label": label, "submitted": BFD.IMPORTANCE_LABELS.get(a["value"]),
                         "current": BFD.IMPORTANCE_LABELS.get(cur) if cur else None, "changed": cur != a["value"]})
            continue
        fd = FT.field_def(kind, key)
        if not fd:
            continue
        cur_state = prov.get(key, {}).get("answer_state")
        cur_val = rec.get(key)
        sub_disp = _display(kind, key, a["value"]) if a["state"] == "value" else dict(FT.RESPONDENT_STATES).get(a["state"])
        cur_disp = _display(kind, key, cur_val) if cur_state == "value" else (
            S.ANSWER_STATE_LABELS.get(cur_state) if cur_state else None)
        try:
            same = (a["state"] == cur_state and (a["state"] != "value" or S._parse_one(fd, a["value"]) == cur_val))
        except ValueError:
            same = False
        rows.append({"key": key, "label": fd.label, "submitted": sub_disp, "current": cur_disp, "changed": not same,
                     "material": fd.material})
    return rows


def apply_review(db, actor: dict, sub_id: int, accepted: list[str], action: str, note: str | None) -> dict:
    """action: apply | reject | more_info | recorded (public submissions already created their record)."""
    if not user_can(db, actor, "crm.edit"):
        raise PermissionDenied("crm.edit")
    sub = db.one("SELECT * FROM form_submissions WHERE id = ?", (sub_id,))
    if not sub or sub["status"] != "submitted":
        raise ValidationError("This submission is not waiting for review.")
    if action not in ("apply", "reject", "more_info", "recorded"):
        raise ValidationError("Choose what to do with the answers.")
    if action in ("reject", "more_info") and not (note or "").strip():
        raise ValidationError({"note": "Add a short note explaining the decision."})
    opp = load_opportunity(db, sub["opportunity_id"])
    answers = json.loads(sub["answers"] or "{}")
    accepted = [k for k in accepted if k]
    source = f"form:{sub['template_version']}#{sub_id}"
    applied: list[str] = []
    now = now_iso()
    with db:
        if action == "apply" and accepted:
            applied = _apply(db, actor, sub, opp, answers, set(accepted), source)
        answerable = len([r for r in review_rows(db, sub)]) if action == "apply" else 0
        outcome = {"apply": "applied" if applied and len(applied) >= answerable else
                   "partially_applied" if applied else "rejected",
                   "reject": "rejected", "more_info": "more_info_required", "recorded": "recorded"}[action]
        db.update("form_submissions", sub_id, {"status": "reviewed", "review_outcome": outcome,
                                               "applied_fields": json.dumps(applied), "review_note": (note or "").strip()
                                               or None, "reviewed_by": actor["id"], "reviewed_at": now})
        if sub["invitation_id"]:
            db.update("form_invitations", sub["invitation_id"], {
                "status": "more_info_required" if action == "more_info" else "accepted", "closed_at": now})
        _close_review_task(db, actor, sub, f"Form reviewed: {outcome.replace('_', ' ')}")
        T.add_activity(db, actor["id"], "opportunity", opp["id"], "data_update",
                       f"Form answers reviewed ({sub['template_version']}): {outcome.replace('_', ' ')}"
                       + (f" — {len(applied)} field(s) updated" if applied else "")
                       + (f". {note.strip()}" if note and note.strip() else ""))
        audit.record(db, actor["id"], "form.reviewed", "opportunity", opp["id"],
                     {"submission_id": sub_id, "outcome": outcome, "applied": applied})
    return {"outcome": outcome, "applied": applied}


def _close_review_task(db, actor, sub, outcome_text):
    tid = sub.get("review_task_id")
    task = db.one("SELECT * FROM tasks WHERE id = ? AND status = 'open'", (tid,)) if tid else None
    if not task:
        return
    if T._requires_next_action(db, task):
        T.complete_task(db, actor, task["id"], outcome_text, next_title="Follow up after reviewing form answers",
                        next_due=to_iso(now_utc() + timedelta(days=1)))
    else:
        T.complete_task(db, actor, task["id"], outcome_text)


def _apply(db, actor, sub, opp, answers, accepted: set, source: str) -> list[str]:
    field_keys = [k for k in answers if k in accepted and FT.field_def(sub["kind"], k)]
    data = {}
    for k in field_keys:
        a = answers[k]
        data[k] = a["value"] or ""
        data[k + "__state"] = a["state"]
    applied = []
    if sub["kind"] == "seller":
        listing_id = db.scalar("SELECT id FROM seller_listings WHERE opportunity_id = ?", (opp["id"],))
        values, errors = S._parse_fields(data, field_keys)
        if errors:
            raise ValidationError(errors)
        changed = S._apply_values(db, actor, listing_id, values, source=source)
        if changed:
            S._flag_re_review_if_material(db, actor, S.load(db, opp["id"]), set(changed))
        return changed
    mandate = db.one("SELECT * FROM buyer_mandates WHERE opportunity_id = ?", (opp["id"],))
    mid = mandate["id"]
    values, errors = B._parse_fields(data, field_keys)
    if errors:
        raise ValidationError(errors)
    target = mandate["data_version"] + 1
    applied += B._apply_values(db, actor["id"], mid, values, source=source, evidence="buyer_declared")
    imp_changed = False
    for k in accepted:
        if k.startswith("pref:") or k.startswith("importance:"):
            parts = k.split(":")
            dim, choice = parts[1], (parts[2] if len(parts) > 2 else "")
            if k in answers and B._set_importance(db, actor["id"], mid, dim, choice, answers[k]["value"], target):
                applied.append(k)
                imp_changed = True
    if imp_changed and len(applied) == len([k for k in applied if k.startswith(("pref:", "importance:"))]):
        B._bump(db, mid)
    bo = B.load(db, opp["id"])
    for k in accepted:
        if k.startswith("locality:"):
            _, kk, name = k.split(":", 2)
            city = mandate.get("preferred_city") or "Hyderabad"
            try:
                B.add_locality(db, actor, bo, kk, city, name, "From customer form")
                applied.append(k)
            except ValidationError:
                pass  # already listed
    return applied


# -- Documents (FR-013) -----------------------------------------------------------------
def can_view_documents(db, user: dict, opp: dict) -> bool:
    if not user_can(db, user, "crm.view"):
        return False
    if opp["kind"] == "seller":
        return can_view_restricted_seller(db, user, opp)
    return B.can_view_funding_evidence(db, user, opp)


def documents_for(db, opp_id: int) -> list[dict]:
    return db.all("SELECT d.*, u.full_name AS reviewer_name FROM documents d LEFT JOIN users u ON u.id = d.reviewed_by "
                  "WHERE d.opportunity_id = ? ORDER BY d.created_at DESC", (opp_id,))


def open_document(db, user: dict, doc_id: int) -> tuple[dict, bytes]:
    doc = db.one("SELECT * FROM documents WHERE id = ?", (doc_id,))
    if not doc:
        raise ValidationError("Document not found.")
    opp = load_opportunity(db, doc["opportunity_id"])
    if not can_view_documents(db, user, opp):
        audit.record(db, user["id"], "document.open", "document", doc_id, {"opportunity": opp["id"]}, outcome="denied")
        raise PermissionDenied("document.view")
    data = files.read(doc["stored_name"])
    audit.record(db, user["id"], "document.open", "document", doc_id, {"opportunity": opp["id"]})
    return doc, data


def review_document(db, actor, doc_id: int, status: str, note: str | None) -> None:
    if not user_can(db, actor, "evidence.review"):
        raise PermissionDenied("evidence.review")
    if status not in ("reviewed", "rejected", "pending"):
        raise ValidationError("Unknown status.")
    doc = db.one("SELECT * FROM documents WHERE id = ?", (doc_id,))
    if not doc:
        raise ValidationError("Document not found.")
    with db:
        db.update("documents", doc_id, {"review_status": status, "review_note": (note or "").strip() or None,
                                        "reviewed_by": actor["id"], "reviewed_at": now_iso()})
        T.add_activity(db, actor["id"], "opportunity", doc["opportunity_id"], "data_update",
                       f"Document '{doc['doc_type'].replace('_', ' ')}' marked {status}")
        audit.record(db, actor["id"], "document.review", "document", doc_id, {"status": status})


# -- Background job: chase and expire (§10.4 "Form not started / incomplete") ----------
def chase_and_expire(db) -> str:
    now = now_iso()
    expired = 0
    for inv in db.all("SELECT id FROM form_invitations WHERE status IN ('sent','opened','started') AND expires_at < ?",
                      (now,)):
        db.update("form_invitations", inv["id"], {"status": "expired", "closed_at": now})
        expired += 1
    cutoff = to_iso(now_utc() - timedelta(hours=CHASE_AFTER_HOURS))
    chased = 0
    for inv in db.all("SELECT i.*, o.primary_owner_id, o.ref_code, o.status AS opp_status, c.full_name AS contact_name "
                      "FROM form_invitations i JOIN opportunities o ON o.id = i.opportunity_id "
                      "JOIN contacts c ON c.id = i.contact_id WHERE i.status IN ('sent','opened','started') "
                      "AND i.chased_at IS NULL AND i.created_at < ?", (cutoff,)):
        if inv["opp_status"] == "closed":
            continue
        with db:
            state = STATUS_LABELS[inv["status"]].lower()
            T.create_task(db, None, title=f"Form not completed ({state}): {inv['contact_name']} ({inv['ref_code']})",
                          assignee_id=inv["primary_owner_id"] or inv["created_by"], due_at=now, task_type="follow_up",
                          priority="normal", entity_type="opportunity", entity_id=inv["opportunity_id"])
            db.update("form_invitations", inv["id"], {"chased_at": now})
        chased += 1
    return f"chased {chased}, expired {expired}"
