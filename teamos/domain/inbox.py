"""WhatsApp team inbox, consent-guarded sending, opt-out, webhooks and reviewed batches (PRD §10; Milestone 7).

Every outbound message passes one gate (`check_send`) — template approved and fully filled (UAT-29), contact reachable,
not opted out (UAT-23), the right consent for the purpose (service vs marketing, UAT-30), frequency caps — and a
collision check that warns when a colleague is already talking to this person (UAT-44). Batches re-run the gate for
every recipient at the moment of sending (UAT-45). Timeouts are 'uncertain' and need a person to resolve them; they are
never resent automatically (UAT-25). Webhook events are processed once, however often the provider repeats them (UAT-24).
"""
from __future__ import annotations

import json
from datetime import timedelta

from flask import current_app, has_app_context

from .. import audit, messaging
from ..permissions import user_can
from ..timeutil import now_iso, now_utc, to_iso
from . import tasks as T
from .common import normalize_indian_mobile
from .errors import PermissionDenied, ValidationError

SERVICE_WINDOW_H = 24          # provider's customer-service window for free-text replies
COLLISION_MIN = 30             # warn if a colleague messaged this person in the last 30 minutes
DAILY_CAP = 3                  # max outbound messages per contact per 24 h (proposal, D-43)
MARKETING_DAYS = 7             # max one marketing message per contact per 7 days (proposal, D-43)
OPT_OUT_WORDS = {"stop", "unsubscribe", "stop messages", "opt out", "optout"}
OPT_IN_WORDS = {"start", "unstop"}
STATUS_RANK = {"queued": 0, "provider_accepted": 1, "sent": 2, "delivered": 3, "read": 4}
STATUS_LABELS = {"queued": "Queued", "provider_accepted": "Accepted by provider", "sent": "Sent", "delivered": "Delivered",
                 "read": "Read", "failed": "Failed", "uncertain": "Uncertain — needs review", "suppressed": "Suppressed",
                 "received": "Received"}


class CollisionWarning(Exception):
    def __init__(self, message):
        self.message = message
        super().__init__(message)


def _config():
    return current_app.config if has_app_context() else None


# -- Templates --------------------------------------------------------------------------------
def ensure_templates(db) -> None:
    """Add any built-in template that is not in the database yet (edits made by an admin are kept), and give
    untouched built-ins their Meta category and Meta name."""
    from .. import meta_templates as MT
    rows = {r["key"]: r for r in db.all("SELECT * FROM message_templates")}
    missing = {k: v for k, v in messaging.TEMPLATES.items() if k not in rows}
    stale = [r for k, r in rows.items() if k in MT.META_CATEGORY and not r.get("meta_category")]
    if not missing and not stale:
        return
    now = now_iso()
    with db:
        for key, (name, cat, body) in missing.items():
            db.insert("message_templates", {"key": key, "name": name, "category": cat, "body": body,
                                            "status": "approved_local", "created_at": now, "updated_at": now,
                                            "meta_category": MT.META_CATEGORY.get(key), "provider_name": MT.meta_name(key),
                                            "notes": "Internal wording approved for testing; needs Meta approval "
                                                     "before live use."})
        for r in stale:
            upd = {"meta_category": MT.META_CATEGORY[r["key"]], "provider_name": r["provider_name"] or MT.meta_name(r["key"])}
            builtin = messaging.TEMPLATES.get(r["key"])
            if builtin and not r["created_by"] and r["body"] == builtin[2]:
                upd["category"] = builtin[1]  # e.g. the re-engagement follow-up is MARKETING under Meta's rules
            db.update("message_templates", r["id"], upd)


def usable_templates(db, live: bool) -> list[dict]:
    """Templates staff may pick: approved internally in test mode; once live, only Meta-approved with matching text."""
    ensure_templates(db)
    rows = db.all("SELECT * FROM message_templates WHERE status IN ('approved_local','provider_approved') ORDER BY name")
    rows = [r for r in rows if r["key"] != "optout_confirm"]  # system reply, never chosen by hand
    if live:
        rows = [r for r in rows if r["status"] == "provider_approved" and r["meta_status"] == "APPROVED"
                and not r["meta_issue"]]
    return rows


def template(db, key: str) -> dict | None:
    ensure_templates(db)
    return db.one("SELECT * FROM message_templates WHERE key = ?", (key,))


def template_problems(t: dict | None, variables: dict, live: bool) -> list[str]:
    """UAT-29: a template with missing placeholders (or not approved) cannot be dispatched."""
    if not t:
        return ["Unknown template"]
    out = []
    if t["status"] in ("draft", "paused", "rejected"):
        out.append(f"Template '{t['name']}' is {t['status']} — it must be approved before use")
    if live and (t["status"] != "provider_approved" or (t.get("meta_status") or "") != "APPROVED"):
        out.append(f"Template '{t['name']}' is not confirmed as approved by Meta (use 'Check with Meta')")
    elif live and t.get("meta_issue"):
        out.append(f"Template '{t['name']}': {t['meta_issue']}")
    elif live and not (t.get("provider_name") or "").strip():
        out.append(f"Template '{t['name']}' has no Meta template name recorded")
    missing = [p for p in messaging.placeholders(t["body"]) if not str(variables.get(p) or "").strip()]
    if missing:
        out.append("Missing values for: " + ", ".join("{" + m + "}" for m in missing))
    return out


def save_template(db, actor, key: str, name: str, category: str, body: str, status: str,
                  provider_name: str | None = None) -> None:
    if not user_can(db, actor, "settings.manage"):
        raise PermissionDenied("settings.manage")
    key = (key or "").strip().lower()
    if not key.replace("_", "").isalnum() or not (name or "").strip() or not (body or "").strip():
        raise ValidationError({"template": "Key (letters, digits, _), name and text are required."})
    if category not in ("service", "marketing") or status not in ("draft", "approved_local", "paused"):
        raise ValidationError({"template": "Choose a valid category and status."})
    if "stop" not in body.lower():
        raise ValidationError({"body": "Every customer template must tell people how to opt out (e.g. 'Reply STOP')."})
    from .. import meta_templates as MT
    provider_name = MT.meta_name((provider_name or "").strip() or key)
    now = now_iso()
    with db:
        ex = db.one("SELECT * FROM message_templates WHERE key = ?", (key,))
        if ex:
            changed = ex["body"] != body.strip() or (ex["provider_name"] or "") != provider_name
            db.update("message_templates", ex["id"], {
                "name": name.strip(), "category": category, "body": body.strip(), "status": status,
                "version": ex["version"] + (ex["body"] != body.strip()), "provider_name": provider_name,
                "meta_category": "MARKETING" if category == "marketing" else (ex["meta_category"] or "UTILITY"),
                "updated_at": now,
                # any wording change needs Meta's approval again before live use
                **({"meta_status": None, "meta_issue": "Changed after the last check with Meta — check again."}
                   if changed and ex["meta_status"] else {})})
        else:
            db.insert("message_templates", {"key": key, "name": name.strip(), "category": category, "body": body.strip(),
                                            "status": status, "provider_name": provider_name,
                                            "meta_category": "MARKETING" if category == "marketing" else "UTILITY",
                                            "created_by": actor["id"], "created_at": now,
                                            "updated_at": now})
        audit.record(db, actor["id"], "template.saved", None, None, {"key": key, "status": status})


# -- Which number a message goes from ------------------------------------------------------------
def sender_number(db, actor, conv: dict | None = None, free_text: bool = False) -> str | None:
    """Replies go from the number the customer wrote to (WhatsApp's 24-hour window belongs to that number).
    New template messages go from the sender's own WhatsApp number if they have one, else the company number (None)."""
    if free_text and conv and conv.get("business_number_id"):
        return conv["business_number_id"]
    if actor:
        return db.scalar("SELECT wa_phone_number_id FROM users WHERE id = ?", (actor["id"],)) or None
    return None


def number_label(db, number_id: str | None) -> str:
    if not number_id:
        return "the company WhatsApp number"
    u = db.one("SELECT full_name, wa_number FROM users WHERE wa_phone_number_id = ?", (number_id,))
    if u:
        return f"{u['full_name'].split()[0]}'s WhatsApp ({u['wa_number'] or 'number id …' + number_id[-4:]})"
    return f"WhatsApp number id …{number_id[-4:]}"


def set_team_number(db, actor, user_id: int, wa_number: str | None, number_id: str | None) -> None:
    """Admin: connect a team member's WhatsApp Business number (registered in Salon Shastra's Meta account)."""
    if not user_can(db, actor, "settings.manage"):
        raise PermissionDenied("settings.manage")
    u = db.one("SELECT * FROM users WHERE id = ? AND role <> 'finance'", (user_id,))
    if not u:
        raise ValidationError({"user": "Choose a team member."})
    wa_number = (wa_number or "").strip()
    number_id = (number_id or "").strip()
    if not wa_number and not number_id:
        norm, nid = None, None  # disconnect: this person's messages go from the company number again
    else:
        norm, err = normalize_indian_mobile(wa_number)
        if err or not norm:
            raise ValidationError({"wa_number": "Enter the WhatsApp number as a 10-digit Indian mobile."})
        if number_id and (not number_id.isdigit() or len(number_id) < 6):
            raise ValidationError({"number_id": "Enter Meta's phone number ID (digits only, from WhatsApp Manager / API setup)."})
        nid = number_id or None  # number saved now; the Meta ID is added once the number is in Meta's account
        # (no "? IS NOT NULL": PostgreSQL cannot tell the type of a bare parameter)
        clash = db.scalar("SELECT full_name FROM users WHERE id <> ? AND (wa_phone_number_id = ? OR wa_number = ?)",
                          (user_id, nid or "-", norm))
        if clash:
            raise ValidationError({"number_id": f"That number is already connected to {clash}."})
    with db:
        db.update("users", user_id, {"wa_number": norm, "wa_phone_number_id": nid})
        audit.record(db, actor["id"], "whatsapp.team_number", "user", user_id,
                     {"number": norm, "number_id": nid, "previous": u.get("wa_phone_number_id")})


# -- Conversations ----------------------------------------------------------------------------
def conversation_for(db, phone: str, contact_id: int | None = None) -> dict:
    conv = db.one("SELECT * FROM conversations WHERE phone = ?", (phone,))
    if conv:
        if contact_id and not conv["contact_id"]:
            db.update("conversations", conv["id"], {"contact_id": contact_id, "updated_at": now_iso()})
            conv["contact_id"] = contact_id
        return conv
    now = now_iso()
    cid = db.insert("conversations", {"phone": phone, "contact_id": contact_id, "status": "open", "created_at": now,
                                      "updated_at": now})
    return db.one("SELECT * FROM conversations WHERE id = ?", (cid,))


def _latest(db, contact_id, purpose):
    return db.one("SELECT * FROM contact_consents WHERE contact_id = ? AND purpose = ? ORDER BY captured_at DESC, id DESC "
                  "LIMIT 1", (contact_id, purpose))


def opted_out(db, contact_id) -> bool:
    c = _latest(db, contact_id, "service_contact")
    return bool(c and not c["granted"] and (c["source"] or "").startswith("whatsapp_optout"))


def check_send(db, actor, contact_id: int, category: str) -> tuple[list[str], list[str]]:
    """(blocking reasons, warnings) for messaging this contact now."""
    blocks, warns = [], []
    contact = db.one("SELECT * FROM contacts WHERE id = ?", (contact_id,))
    if not contact or not contact["phone_normalized"]:
        return ["no mobile number"], []
    if contact["do_not_contact"]:
        blocks.append("the contact asked not to be contacted")
    if opted_out(db, contact_id):
        blocks.append("the contact opted out of messages (replied STOP)")
    purpose = "marketing" if category == "marketing" else "service_contact"
    c = _latest(db, contact_id, purpose)
    if not c or not c["granted"]:
        blocks.append("no marketing consent" if purpose == "marketing" else "no consent to be contacted")
    since = to_iso(now_utc() - timedelta(hours=24))
    conv = db.one("SELECT * FROM conversations WHERE phone = ?", (contact["phone_normalized"],))
    if conv:
        n = db.scalar("SELECT COUNT(*) FROM messages WHERE conversation_id = ? AND direction = 'out' AND created_at >= ? "
                      "AND status NOT IN ('failed','suppressed')", (conv["id"], since))
        if n >= DAILY_CAP:
            blocks.append(f"already messaged {n} times in the last 24 hours (limit {DAILY_CAP})")
        if category == "marketing" and db.scalar(
                "SELECT COUNT(*) FROM messages m JOIN message_templates t ON t.key = m.template_key WHERE "
                "m.conversation_id = ? AND t.category = 'marketing' AND m.created_at >= ? AND m.status NOT IN "
                "('failed','suppressed')", (conv["id"], to_iso(now_utc() - timedelta(days=MARKETING_DAYS)))):
            blocks.append(f"already received a marketing message in the last {MARKETING_DAYS} days")
        recent = to_iso(now_utc() - timedelta(minutes=COLLISION_MIN))
        if actor and conv["last_outbound_by"] and conv["last_outbound_by"] != actor["id"] and \
                (conv["last_outbound_at"] or "") >= recent:
            who = db.scalar("SELECT full_name FROM users WHERE id = ?", (conv["last_outbound_by"],))
            warns.append(f"{who} messaged this person {_ago(conv['last_outbound_at'])}")
        if actor and conv["assigned_to"] and conv["assigned_to"] != actor["id"] and conv["status"] == "open":
            who = db.scalar("SELECT full_name FROM users WHERE id = ?", (conv["assigned_to"],))
            warns.append(f"this conversation is assigned to {who}")
    return blocks, warns


def _ago(iso):
    from ..timeutil import relative
    return relative(iso)


def send(db, actor, *, contact_id: int, opportunity_id: int | None = None, template_key: str | None = None,
         variables: dict | None = None, text: str | None = None, stored_variables: dict | None = None,
         invitation_id: int | None = None, batch_id: int | None = None, acknowledge: bool = False,
         system: bool = False) -> dict:
    """Send one WhatsApp message through the active provider. Returns the stored message row.

    `variables` go to the provider; `stored_variables` (if given) replace them in the stored copy, so secret links
    are never kept in the message log."""
    if actor and not user_can(db, actor, "crm.log_activity"):
        raise PermissionDenied("crm.log_activity")  # e.g. Finance can never message customers
    cfg = _config()
    status = messaging.whatsapp_status(cfg)
    contact = db.one("SELECT * FROM contacts WHERE id = ?", (contact_id,))
    if not contact or not contact["phone_normalized"]:
        raise ValidationError({"send": "This contact has no mobile number."})
    conv = conversation_for(db, contact["phone_normalized"], contact_id)
    variables = dict(variables or {})
    t = None
    if text is not None:  # free text: only as a reply inside the customer-service window
        if not text.strip():
            raise ValidationError({"text": "Write a message."})
        window_ok = conv["last_inbound_at"] and conv["last_inbound_at"] >= to_iso(now_utc() - timedelta(hours=SERVICE_WINDOW_H))
        if not window_ok:
            raise ValidationError({"text": "Free text is only allowed within 24 hours of the customer's last message. "
                                           "Use an approved template instead."})
        category, body, stored = "service", text.strip()[:2000], text.strip()[:2000]
        blocks, warns = check_send(db, actor, contact_id, category)
        blocks = [b for b in blocks if not b.startswith("no consent")]  # replying to the customer's own message
    else:
        t = template(db, template_key)
        probs = template_problems(t, variables, status["live"])
        if probs:
            raise ValidationError({"template": "Cannot send: " + "; ".join(probs) + "."})
        category = t["category"]
        body = messaging.render(t["body"], variables)
        stored = messaging.render(t["body"], {**variables, **(stored_variables or {})})
        blocks, warns = check_send(db, actor, contact_id, category)
    if blocks:
        audit.record(db, actor["id"] if actor else None, "message.blocked", "contact", contact_id,
                     {"reasons": blocks, "template": template_key}, outcome="denied")
        raise ValidationError({"send": "Not sent: " + "; ".join(blocks) + "."})
    if warns and not acknowledge and not system:
        raise CollisionWarning("Heads-up: " + "; ".join(warns) + ". Send anyway?")
    prov = messaging.provider(cfg)
    now = now_iso()
    nid = sender_number(db, actor, conv, free_text=text is not None)
    try:
        from .. import meta_templates as MT
        res = prov.send(contact["phone_normalized"], body, template_key, from_number_id=nid,
                        template_name=(t["provider_name"] or MT.meta_name(t["key"])) if t else None,
                        language=t["language"] if t else "en",
                        params=[MT.clean_param(variables.get(p, "")) for p in messaging.ordered_placeholders(t["body"])]
                        if t else None)
    except messaging.ProviderTimeout as e:
        res = {"status": "uncertain", "detail": f"No answer from the provider ({e}). It may or may not have been sent — "
                                                "check before sending again."}
    with db:
        mid = db.insert("messages", {
            "conversation_id": conv["id"], "direction": "out", "kind": "text" if text is not None else "template",
            "template_key": template_key, "body": stored, "status": res["status"], "provider": prov.name,
            "provider_message_id": res.get("provider_message_id"), "error": None if res["status"] != "failed"
            else res.get("detail"), "sent_by": actor["id"] if actor else None, "opportunity_id": opportunity_id,
            "invitation_id": invitation_id, "batch_id": batch_id, "created_at": now, "status_at": now,
            "from_number_id": nid})
        db.update("conversations", conv["id"], {"last_message_at": now, "last_outbound_at": now,
                                                "last_outbound_by": actor["id"] if actor else None, "updated_at": now,
                                                "status": "open", "unread": 0, "business_number_id": nid})
        if opportunity_id:
            db.execute("INSERT INTO conversation_links (conversation_id, opportunity_id, linked_by, linked_at) "
                       "SELECT ?, ?, ?, ? WHERE NOT EXISTS (SELECT 1 FROM conversation_links WHERE conversation_id = ? "
                       "AND opportunity_id = ?)", (conv["id"], opportunity_id, actor["id"] if actor else None, now,
                                                   conv["id"], opportunity_id))
        if res["status"] == "uncertain" and actor:
            T.notify(db, actor["id"], "message", "A WhatsApp send is uncertain — please check before resending",
                     f"/inbox/{conv['id']}")
        audit.record(db, actor["id"] if actor else None, "message.sent", "conversation", conv["id"],
                     {"message": mid, "status": res["status"], "template": template_key, "provider": prov.name,
                      "provider_message_id": res.get("provider_message_id")})
    return {**db.one("SELECT * FROM messages WHERE id = ?", (mid,)), "detail": res.get("detail"), "warnings": warns}


def reply_unmatched(db, actor, conv_id: int, text: str) -> dict:
    """Free-text reply to someone who wrote in from a number not yet in the CRM (e.g. "I saw your ad").

    Allowed only inside the 24-hour window the customer opened, never after they said STOP, and within the daily cap.
    Templates (outbound-first messages) still need a linked record with consent."""
    if not user_can(db, actor, "crm.log_activity"):
        raise PermissionDenied("crm.log_activity")
    conv = db.one("SELECT * FROM conversations WHERE id = ?", (conv_id,))
    if not conv or conv["contact_id"]:
        raise ValidationError({"text": "Use the normal reply box for linked conversations."})
    if not (text or "").strip():
        raise ValidationError({"text": "Write a message."})
    if not (conv["last_inbound_at"] and conv["last_inbound_at"] >= to_iso(now_utc() - timedelta(hours=SERVICE_WINDOW_H))):
        raise ValidationError({"text": "This person last wrote more than 24 hours ago. Link or create their record first, "
                                       "then use an approved template."})
    last_in = db.scalar("SELECT body FROM messages WHERE conversation_id = ? AND direction = 'in' ORDER BY id DESC LIMIT 1",
                        (conv_id,))
    if (last_in or "").strip().lower().rstrip(".!") in OPT_OUT_WORDS:
        raise ValidationError({"text": "Not sent: they asked to stop receiving messages."})
    n = db.scalar("SELECT COUNT(*) FROM messages WHERE conversation_id = ? AND direction = 'out' AND created_at >= ? AND "
                  "status NOT IN ('failed','suppressed')", (conv_id, to_iso(now_utc() - timedelta(hours=24))))
    if n >= DAILY_CAP:
        raise ValidationError({"text": f"Not sent: already messaged {n} times in the last 24 hours (limit {DAILY_CAP})."})
    body = text.strip()[:2000]
    prov = messaging.provider(_config())
    nid = conv.get("business_number_id")
    try:
        res = prov.send(conv["phone"], body, None, from_number_id=nid)
    except messaging.ProviderTimeout as e:
        res = {"status": "uncertain", "detail": f"No answer from the provider ({e}). Check before sending again."}
    now = now_iso()
    with db:
        mid = db.insert("messages", {
            "conversation_id": conv_id, "direction": "out", "kind": "text", "template_key": None, "body": body,
            "status": res["status"], "provider": prov.name, "provider_message_id": res.get("provider_message_id"),
            "error": res.get("detail") if res["status"] == "failed" else None, "sent_by": actor["id"],
            "created_at": now, "status_at": now, "from_number_id": nid})
        db.update("conversations", conv_id, {"last_message_at": now, "last_outbound_at": now, "last_outbound_by": actor["id"],
                                             "updated_at": now, "status": "open", "unread": 0,
                                             "assigned_to": conv["assigned_to"] or actor["id"]})
        audit.record(db, actor["id"], "message.sent", "conversation", conv_id,
                     {"message": mid, "status": res["status"], "unmatched": True, "provider": prov.name})
    return {**db.one("SELECT * FROM messages WHERE id = ?", (mid,)), "detail": res.get("detail")}


def resolve_uncertain(db, actor, message_id: int, outcome: str) -> None:
    if not user_can(db, actor, "crm.log_activity"):
        raise PermissionDenied("crm.log_activity")
    m = db.one("SELECT * FROM messages WHERE id = ?", (message_id,))
    if not m or m["status"] != "uncertain":
        raise ValidationError("Only uncertain messages need resolving.")
    if outcome not in ("sent", "failed"):
        raise ValidationError("Choose whether it reached the customer.")
    with db:
        db.update("messages", message_id, {"status": outcome, "resolved_by": actor["id"], "resolved_at": now_iso(),
                                           "status_at": now_iso()})
        audit.record(db, actor["id"], "message.resolved", "message", message_id, {"outcome": outcome})


# -- Inbound, opt-out, status updates -------------------------------------------------------------
def _contact_for_phone(db, phone):
    rows = db.all("SELECT id FROM contacts WHERE phone_normalized = ? OR alt_phone_normalized = ?", (phone, phone))
    return rows[0]["id"] if len(rows) == 1 else None   # 0 or ambiguous → unmatched queue, never guessed (UAT-43)


def record_inbound(db, phone_raw: str, text: str, provider_message_id: str, at: str | None = None,
                   provider: str = messaging.PROVIDER_NAME, business_number_id: str | None = None) -> dict | None:
    phone, err = normalize_indian_mobile(phone_raw if str(phone_raw).startswith("+") else "+" + str(phone_raw))
    phone = phone or ("+" + str(phone_raw).lstrip("+"))
    if provider_message_id and db.scalar("SELECT id FROM messages WHERE provider_message_id = ?", (provider_message_id,)):
        return None  # duplicate delivery of the same inbound message
    now = at or now_iso()
    contact_id = _contact_for_phone(db, phone)
    with db:
        conv = conversation_for(db, phone, contact_id)
        mid = db.insert("messages", {"conversation_id": conv["id"], "direction": "in", "kind": "text",
                                     "body": (text or "")[:4000], "status": "received", "provider": provider,
                                     "provider_message_id": provider_message_id, "created_at": now, "status_at": now})
        upd = {"last_message_at": now, "last_inbound_at": now, "status": "open", "unread": conv["unread"] + 1,
               "updated_at": now}
        if business_number_id:
            upd["business_number_id"] = business_number_id
            owner = db.scalar("SELECT id FROM users WHERE wa_phone_number_id = ? AND is_active = 1", (business_number_id,))
            if owner and not conv["assigned_to"]:
                upd["assigned_to"] = owner  # a customer who writes to Harika's number lands with Harika
                conv["assigned_to"] = owner
        db.update("conversations", conv["id"], upd)
        word = (text or "").strip().lower().rstrip(".!")
        if conv["contact_id"] and word in OPT_OUT_WORDS:
            opt_out(db, conv["contact_id"], conv, provider)
        elif conv["contact_id"] and word in OPT_IN_WORDS:
            db.insert("contact_consents", {"contact_id": conv["contact_id"], "purpose": "service_contact", "channel":
                                           "whatsapp", "granted": 1, "source": "whatsapp_optin", "text_version": "START",
                                           "note": "Customer replied START", "captured_at": now})
        recipients = {conv["assigned_to"]} if conv["assigned_to"] else {
            r["primary_owner_id"] for r in db.all("SELECT o.primary_owner_id FROM opportunities o WHERE o.contact_id = ? "
                                                  "AND o.status <> 'closed'", (conv["contact_id"],))} if conv["contact_id"] else set()
        if not recipients:
            recipients = {r["id"] for r in db.all("SELECT id FROM users WHERE role IN ('super_admin','team_lead') "
                                                  "AND is_active = 1")}
        name = db.scalar("SELECT full_name FROM contacts WHERE id = ?", (conv["contact_id"],)) if conv["contact_id"] else phone
        for uid in recipients - {None}:
            T.notify(db, uid, "message", f"New WhatsApp message from {name}", f"/inbox/{conv['id']}")
    return db.one("SELECT * FROM messages WHERE id = ?", (mid,))


def opt_out(db, contact_id: int, conv: dict, provider: str) -> None:
    """UAT-23/45: stop future outreach everywhere, cancel queued batch sends, tell the owner, confirm to the customer."""
    now = now_iso()
    for purpose in ("service_contact", "marketing"):
        db.insert("contact_consents", {"contact_id": contact_id, "purpose": purpose, "channel": "any", "granted": 0,
                                       "source": "whatsapp_optout", "text_version": "STOP",
                                       "note": "Customer replied STOP on WhatsApp", "captured_at": now})
    db.execute("UPDATE batch_recipients SET status = 'suppressed', reason = 'opted out' WHERE contact_id = ? AND "
               "status = 'pending'", (contact_id,))
    for o in db.all("SELECT id, primary_owner_id, ref_code FROM opportunities WHERE contact_id = ? AND status <> 'closed'",
                    (contact_id,)):
        T.add_activity(db, None, "opportunity", o["id"], "system", "Customer opted out of WhatsApp messages (STOP)")
        if o["primary_owner_id"]:
            T.create_task(db, None, title=f"Customer opted out on WhatsApp — review how to proceed ({o['ref_code']})",
                          assignee_id=o["primary_owner_id"], due_at=T.first_contact_due(24 * 60), task_type="review",
                          priority="normal", entity_type="opportunity", entity_id=o["id"])
    res = messaging.provider(_config()).send(conv["phone"], messaging.TEMPLATES["optout_confirm"][2], "optout_confirm")
    db.insert("messages", {"conversation_id": conv["id"], "direction": "out", "kind": "system",
                           "template_key": "optout_confirm", "body": messaging.TEMPLATES["optout_confirm"][2],
                           "status": res.get("status", "failed"), "provider": provider,
                           "provider_message_id": res.get("provider_message_id"), "created_at": now, "status_at": now})
    audit.record(db, None, "consent.opt_out", "contact", contact_id, {"channel": "whatsapp"})


def apply_status(db, provider_message_id: str, status: str, error: str | None = None) -> bool:
    m = db.one("SELECT * FROM messages WHERE provider_message_id = ?", (provider_message_id,))
    if not m:
        return False
    if status == "failed":
        db.update("messages", m["id"], {"status": "failed", "error": (error or "Provider reported failure")[:300],
                                        "status_at": now_iso()})
        return True
    if status in STATUS_RANK and STATUS_RANK.get(status, 0) > STATUS_RANK.get(m["status"], -1):
        db.update("messages", m["id"], {"status": status, "status_at": now_iso()})   # only ever moves forward
        return True
    return False


def ingest_webhook(db, payload: dict, provider: str = "meta-cloud") -> dict:
    """Store each event once (provider event id is unique) and process it; failures stay visible for retry."""
    stats = {"new": 0, "duplicate": 0, "failed": 0}
    events = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            v = change.get("value", {})
            to_number = (v.get("metadata") or {}).get("phone_number_id")
            names = {c.get("wa_id"): (c.get("profile") or {}).get("name") for c in v.get("contacts", [])}
            for msg in v.get("messages", []):
                events.append((msg.get("id"), "message", {**msg, "_to_number_id": to_number,
                                                          "_profile_name": names.get(msg.get("from"))}))
            for st in v.get("statuses", []):
                events.append((f"{st.get('id')}:{st.get('status')}", "status", st))
    for event_id, kind, body in events:
        if not event_id:
            continue
        ex = db.one("SELECT * FROM webhook_events WHERE event_id = ?", (event_id,))
        if ex and ex["status"] in ("processed", "ignored"):
            stats["duplicate"] += 1
            continue
        if not ex:
            db.insert("webhook_events", {"provider": provider, "event_id": event_id, "kind": kind,
                                         "payload": json.dumps(body), "received_at": now_iso()})
            ex = db.one("SELECT * FROM webhook_events WHERE event_id = ?", (event_id,))
        process_event(db, ex)  # new, or a previously failed event being redelivered
        failed = db.scalar("SELECT status FROM webhook_events WHERE id = ?", (ex["id"],)) == "failed"
        stats["failed" if failed else "new"] += 1
    return stats


def process_event(db, ev: dict) -> None:
    body = json.loads(ev["payload"])
    try:
        if ev["kind"] == "message":
            from datetime import datetime, timezone
            ts = body.get("timestamp")
            at = to_iso(datetime.fromtimestamp(int(ts), tz=timezone.utc)) if ts else None
            text = (body.get("text") or {}).get("body", "[non-text message]")
            msg = record_inbound(db, body.get("from", ""), text, body.get("id"), at, provider=ev["provider"],
                                 business_number_id=body.get("_to_number_id"))
            if msg:   # a chat from a Meta ad (or any new chat, if switched on) becomes a lead — once
                from . import wa_leads
                raw = str(body.get("from", ""))
                phone, _e = normalize_indian_mobile(raw if raw.startswith("+") else "+" + raw)
                with db:
                    wa_leads.on_inbound(db, phone or ("+" + raw.lstrip("+")), text, body.get("referral"),
                                        body.get("_profile_name"), at, msg["conversation_id"])
            outcome = "processed"
        else:
            err = (body.get("errors") or [{}])[0].get("title") if body.get("errors") else None
            outcome = "processed" if apply_status(db, body.get("id"), body.get("status"), err) else "ignored"
        db.update("webhook_events", ev["id"], {"status": outcome, "attempts": ev["attempts"] + 1,
                                               "processed_at": now_iso(), "error": None})
    except Exception as e:  # noqa: BLE001 — keep the event and its error for a visible retry
        db.update("webhook_events", ev["id"], {"status": "failed", "attempts": ev["attempts"] + 1,
                                               "error": f"{type(e).__name__}: {e}"[:500]})


def retry_failed_events(db) -> str:
    n = 0
    for ev in db.all("SELECT * FROM webhook_events WHERE status = 'failed' AND attempts < 5"):
        process_event(db, ev)
        n += 1
    return f"retried {n} webhook event(s)"


# -- Staff actions on conversations -------------------------------------------------------------
def assign(db, actor, conv_id: int, user_id: int | None, note: str | None = None) -> None:
    if not user_can(db, actor, "crm.log_activity"):
        raise PermissionDenied("crm.log_activity")
    conv = db.one("SELECT * FROM conversations WHERE id = ?", (conv_id,))
    if not conv:
        raise ValidationError("Conversation not found.")
    if conv["assigned_to"] and conv["assigned_to"] != actor["id"] and user_id != actor["id"] \
            and not user_can(db, actor, "crm.assign"):
        raise PermissionDenied("crm.assign")
    u = db.one("SELECT id, full_name FROM users WHERE id = ? AND is_active = 1 AND role <> 'finance'", (user_id,)) \
        if user_id else None
    with db:
        db.update("conversations", conv_id, {"assigned_to": u["id"] if u else None, "updated_at": now_iso()})
        db.insert("messages", {"conversation_id": conv_id, "direction": "out", "kind": "system", "status": "suppressed",
                               "provider": "internal", "body": f"Handed to {u['full_name'] if u else 'nobody'} by "
                               f"{actor['full_name']}" + (f": {note.strip()}" if note and note.strip() else ""),
                               "sent_by": actor["id"], "created_at": now_iso()})
        if u and u["id"] != actor["id"]:
            T.notify(db, u["id"], "assignment", f"{actor['full_name']} handed you a WhatsApp conversation",
                     f"/inbox/{conv_id}")


def link(db, actor, conv_id: int, opportunity_id: int) -> None:
    if not user_can(db, actor, "crm.edit"):
        raise PermissionDenied("crm.edit")
    conv = db.one("SELECT * FROM conversations WHERE id = ?", (conv_id,))
    opp = db.one("SELECT id, contact_id, ref_code FROM opportunities WHERE id = ?", (opportunity_id,))
    if not conv or not opp:
        raise ValidationError("Choose a buyer or seller record to link.")
    contact_phone = db.scalar("SELECT phone_normalized FROM contacts WHERE id = ?", (opp["contact_id"],))
    with db:
        if not conv["contact_id"]:
            db.update("conversations", conv_id, {"contact_id": opp["contact_id"], "updated_at": now_iso()})
        db.execute("INSERT INTO conversation_links (conversation_id, opportunity_id, linked_by, linked_at) SELECT ?, ?, ?, ? "
                   "WHERE NOT EXISTS (SELECT 1 FROM conversation_links WHERE conversation_id = ? AND opportunity_id = ?)",
                   (conv_id, opportunity_id, actor["id"], now_iso(), conv_id, opportunity_id))
        T.add_activity(db, actor["id"], "opportunity", opportunity_id, "system",
                       f"WhatsApp conversation with {conv['phone']} linked to this record"
                       + ("" if contact_phone == conv["phone"] else " (different number from the contact's mobile)"))
        audit.record(db, actor["id"], "conversation.linked", "conversation", conv_id, {"opportunity": opportunity_id})


def set_status(db, actor, conv_id: int, status: str) -> None:
    if not user_can(db, actor, "crm.log_activity"):
        raise PermissionDenied("crm.log_activity")
    if status not in ("open", "closed"):
        raise ValidationError("Unknown status.")
    db.update("conversations", conv_id, {"status": status, "unread": 0, "updated_at": now_iso()})


# -- Batches ---------------------------------------------------------------------------------------
AUDIENCES = {
    "buyers_active": ("Active buyers", "o.kind = 'buyer' AND o.status = 'active'"),
    "buyers_nurture": ("Buyers on hold / exploring", "o.kind = 'buyer' AND o.status = 'holding'"),
    "sellers_active": ("Active sellers", "o.kind = 'seller' AND o.status = 'active'"),
    "sellers_nurture": ("Sellers in nurture", "o.kind = 'seller' AND o.status = 'holding'"),
}


SYSTEM_ONLY_TEMPLATES = {"optout_confirm"}


def batch_allowed(t) -> bool:
    """Batches send the same values to everyone, so personal links (forms, teasers) can never go in a batch."""
    return bool(t) and t["key"] not in SYSTEM_ONLY_TEMPLATES and "link" not in messaging.placeholders(t["body"])


def create_batch(db, actor, name: str, template_key: str, audience: str, variables: dict) -> int:
    if not user_can(db, actor, "crm.log_activity"):
        raise PermissionDenied("crm.log_activity")
    if audience not in AUDIENCES:
        raise ValidationError({"audience": "Choose who should receive it."})
    t = template(db, template_key)
    if t and not batch_allowed(t):
        raise ValidationError({"template": "This template carries a personal link or is system-only, so it can't go in a "
                                           "batch. Send it from each person's record instead."})
    probs = [p for p in template_problems(t, {**variables, "name": "x", "sender": "x"},
                                          messaging.whatsapp_status(_config())["live"])]
    if probs:
        raise ValidationError({"template": "; ".join(probs)})
    if not (name or "").strip():
        raise ValidationError({"name": "Name the batch."})
    now = now_iso()
    with db:
        bid = db.insert("batches", {"name": name.strip()[:120], "template_key": template_key,
                                    "audience": json.dumps({"key": audience, "variables": variables}), "status": "draft",
                                    "created_by": actor["id"], "created_at": now, "updated_at": now})
        for r in db.all("SELECT DISTINCT o.contact_id, MIN(o.id) AS opp_id FROM opportunities o WHERE "
                        + AUDIENCES[audience][1] + " GROUP BY o.contact_id"):
            db.insert("batch_recipients", {"batch_id": bid, "contact_id": r["contact_id"], "opportunity_id": r["opp_id"]})
        audit.record(db, actor["id"], "batch.created", "batch", bid, {"audience": audience, "template": template_key})
    return bid


def batch_preview(db, batch_id: int) -> list[dict]:
    b = db.one("SELECT * FROM batches WHERE id = ?", (batch_id,))
    t = template(db, b["template_key"])
    rows = db.all("SELECT r.*, c.full_name, c.phone_normalized FROM batch_recipients r JOIN contacts c ON c.id = r.contact_id "
                  "WHERE batch_id = ? ORDER BY c.full_name", (batch_id,))
    for r in rows:
        if r["status"] == "pending":
            blocks, _ = check_send(db, None, r["contact_id"], t["category"])
            r["eligible"], r["why"] = not blocks, "; ".join(blocks)
        else:
            r["eligible"], r["why"] = False, r["reason"] or r["status"]
    return rows


def approve_batch(db, actor, batch_id: int) -> None:
    """Bulk outreach needs a reviewer (team lead / admin); the creator cannot approve their own batch."""
    if not user_can(db, actor, "crm.assign"):
        raise PermissionDenied("crm.assign")
    b = db.one("SELECT * FROM batches WHERE id = ?", (batch_id,))
    if not b or b["status"] != "draft":
        raise ValidationError("Only a draft batch can be approved.")
    if b["created_by"] == actor["id"] and not user_can(db, actor, "settings.manage"):
        raise ValidationError("Someone other than the creator must approve a batch.")
    with db:
        db.update("batches", batch_id, {"status": "approved", "approved_by": actor["id"], "approved_at": now_iso(),
                                        "updated_at": now_iso()})
        audit.record(db, actor["id"], "batch.approved", "batch", batch_id, {})


def send_batch(db, actor, batch_id: int) -> dict:
    """UAT-45: every recipient is re-checked at send time; anyone who opted out since approval is suppressed."""
    if not user_can(db, actor, "crm.assign"):
        raise PermissionDenied("crm.assign")
    b = db.one("SELECT * FROM batches WHERE id = ?", (batch_id,))
    if not b or b["status"] != "approved":
        raise ValidationError("Only an approved batch can be sent.")
    from flask import current_app, has_app_context

    from . import automations as A
    held = A.bulk_send_blockers(db, current_app.config if has_app_context() else None)
    if held:
        raise ValidationError(" ".join(held))
    variables = json.loads(b["audience"]).get("variables", {})
    counts = {"sent": 0, "suppressed": 0, "failed": 0}
    for r in db.all("SELECT r.*, c.full_name FROM batch_recipients r JOIN contacts c ON c.id = r.contact_id "
                    "WHERE batch_id = ? AND r.status = 'pending'", (batch_id,)):
        try:
            m = send(db, actor, contact_id=r["contact_id"], opportunity_id=r["opportunity_id"],
                     template_key=b["template_key"], variables={**variables, "name": r["full_name"].split()[0],
                                                                "sender": actor["full_name"].split()[0]},
                     batch_id=batch_id, system=True)
            st = "sent" if m["status"] not in ("failed",) else "failed"
            db.update("batch_recipients", r["id"], {"status": st, "message_id": m["id"]})
            counts[st] += 1
        except ValidationError as e:
            db.update("batch_recipients", r["id"], {"status": "suppressed",
                                                    "reason": " ".join(str(v) for v in e.errors.values())[:300]})
            counts["suppressed"] += 1
    counts["suppressed"] += db.scalar("SELECT COUNT(*) FROM batch_recipients WHERE batch_id = ? AND status = 'suppressed' "
                                      "AND reason = 'opted out'", (batch_id,))
    with db:
        db.update("batches", batch_id, {"status": "sent", "sent_at": now_iso(), "counts": json.dumps(counts),
                                        "updated_at": now_iso()})
        audit.record(db, actor["id"], "batch.sent", "batch", batch_id, counts)
    return counts
