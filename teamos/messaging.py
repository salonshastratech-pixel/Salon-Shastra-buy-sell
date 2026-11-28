"""Outbound/inbound messaging providers (PRD §10; Milestone 7).

WhatsApp:
  * MockWhatsAppProvider — default. Records exactly what would be sent; nothing leaves the server.
  * MetaCloudProvider    — official Meta WhatsApp Cloud API adapter. Used ONLY when every credential is configured
                           AND WHATSAPP_LIVE_APPROVED=1. It has not been run against Meta from this codebase yet
                           (no approved account), so the integration page reports it honestly (UAT-48).
E-mail:
  * OutboxEmailProvider  — default. Messages stay in the outbox marked "held" (no mail server configured).
  * SmtpEmailProvider    — used when SMTP_HOST etc. are configured.

A provider acknowledgement is never treated as delivery; a timeout is "uncertain" and is never blindly resent
(§10.3, UAT-25).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import uuid

PROVIDER_NAME = "mock-whatsapp"
PROVIDER_LABEL = "TEST-ONLY mock WhatsApp provider — no message leaves this server"
GRAPH_VERSION = "v21.0"

# Starting template library (PRD §10.4). Seeded into message_templates as LOCAL drafts/approvals; a live account must
# get each one approved by Meta and record its provider name before live use. Text never promises a deal, a return or
# buyer availability.
TEMPLATES = {
    "deep_form_link": ("Detailed form link", "service",
                       "Hello {name}, this is {sender} from Salon Shastra. As discussed, please share your details "
                       "securely using this personal link: {link} . It expires on {expires}. Reply STOP to opt out."),
    "targeted_request": ("Specific details request", "service",
                         "Hello {name}, this is {sender} from Salon Shastra. To move forward we need a few specific "
                         "details. Please use this personal link: {link} (expires {expires}). Reply STOP to opt out."),
    "teaser_share": ("Confidential salon summary", "service",
                     "Hello {name}, this is {sender} from Salon Shastra. Here is a confidential summary of a salon that "
                     "may fit your requirements: {link} (valid till {expires}). Reply STOP to opt out."),
    "enquiry_ack": ("Enquiry acknowledgement", "service",
                    "Hello {name}, thank you for contacting Salon Shastra. {sender} from our team will call you shortly. "
                    "Reply STOP to opt out."),
    "callback_request": ("Call-back request", "service",
                         "Hello {name}, this is {sender} from Salon Shastra. We tried to reach you about your enquiry. "
                         "When is a good time to call? Reply STOP to opt out."),
    "incomplete_form": ("Incomplete form reminder", "service",
                        "Hello {name}, a gentle reminder from Salon Shastra: your form is not complete yet. You can "
                        "continue with the same link we shared earlier. Reply STOP to opt out."),
    "visit_confirm": ("Visit confirmation", "service",
                      "Hello {name}, this is {sender} from Salon Shastra confirming your visit on {when}. Reply here if "
                      "you need to change it. Reply STOP to opt out."),
    "inactive_followup": ("Polite follow-up", "marketing",
                          "Hello {name}, this is {sender} from Salon Shastra checking in. Are you still exploring your "
                          "salon plans? Reply STOP to opt out."),
    "call_thanks_seller": ("After call — seller thank-you", "service",
                           "Hello {name}, this is {sender} from Salon Shastra. Thank you for speaking with me today. As "
                           "discussed, we handle your salon sale confidentially: your salon's name is never shared without "
                           "your approval. Save this number to reach me directly. Reply STOP to opt out."),
    "call_thanks_buyer": ("After call — buyer thank-you", "service",
                          "Hello {name}, this is {sender} from Salon Shastra. Thank you for your time today. I will "
                          "shortlist salons that fit your budget and preferred areas and share confidential summaries "
                          "with you. Save this number to reach me directly. Reply STOP to opt out."),
    "callback_confirm": ("Call-back confirmation", "service",
                         "Hello {name}, this is {sender} from Salon Shastra. As requested, I will call you back on "
                         "{when}. Reply here if another time suits you better. Reply STOP to opt out."),
    "docs_checklist": ("Seller documents checklist", "service",
                       "Hello {name}, this is {sender} from Salon Shastra. Thank you for the details. To verify your "
                       "salon and move ahead, please keep these ready: rent agreement, last 6 months' sales summary and "
                       "the equipment list. Reply STOP to opt out."),
    "funds_proof": ("Buyer proof-of-funds request", "service",
                    "Hello {name}, this is {sender} from Salon Shastra. To show you serious options first, please keep "
                    "proof of funds ready (bank statement or loan sanction letter). I will share a secure link to upload "
                    "it. Reply STOP to opt out."),
    "listing_live": ("Listing is live (seller)", "service",
                     "Hello {name}, this is {sender} from Salon Shastra. Your salon is now listed confidentially with us. "
                     "We will speak to you before any detail is shared with a buyer. Reply STOP to opt out."),
    "shortlist_update": ("Shortlist in progress (buyer)", "service",
                         "Hello {name}, this is {sender} from Salon Shastra. I am shortlisting salons in {area} within "
                         "your budget and will send confidential summaries soon. Reply STOP to opt out."),
    "next_steps": ("After call — next steps", "service",
                   "Hello {name}, this is {sender} from Salon Shastra. Thank you for your time today. I will follow up "
                   "with the next steps as discussed. Reply STOP to opt out."),
    "optout_confirm": ("Opt-out confirmation", "service",
                       "You will not receive further messages from Salon Shastra. Reply START if you change your mind."),
    "market_update": ("Market update (marketing)", "marketing",
                      "Hello {name}, Salon Shastra has new salons available in {area}. Reply YES if you would like a "
                      "call. Reply STOP to opt out."),
}
PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")


def placeholders(body: str) -> list[str]:
    return sorted(set(PLACEHOLDER.findall(body or "")))


def ordered_placeholders(body: str) -> list[str]:
    """Placeholders in order of first appearance = Meta's {{1}}, {{2}} … parameter order for the approved template."""
    return list(dict.fromkeys(PLACEHOLDER.findall(body or "")))


def render(body_or_key: str, variables: dict) -> str:
    body = TEMPLATES[body_or_key][2] if body_or_key in TEMPLATES else body_or_key
    return PLACEHOLDER.sub(lambda m: str(variables.get(m.group(1), m.group(0))), body)


class ProviderTimeout(Exception):
    """The provider did not answer in time: the message MAY have been sent (status 'uncertain')."""


class MockWhatsAppProvider:
    name = PROVIDER_NAME
    label = PROVIDER_LABEL
    is_live = False

    def send(self, to_phone: str, body: str, template: str | None = None, **_live_only) -> dict:
        if not to_phone:
            return {"status": "failed", "detail": "No phone number"}
        return {"status": "provider_accepted", "provider_message_id": f"mock-{uuid.uuid4().hex[:16]}",
                "detail": PROVIDER_LABEL}

    # backwards-compatible helper used by earlier milestones
    def send_template(self, to_phone: str, template_key: str, variables: dict) -> dict:
        if template_key not in TEMPLATES:
            return {"status": "failed", "detail": f"Unknown template {template_key}"}
        return self.send(to_phone, render(template_key, variables), template_key)


class MetaCloudProvider:
    """Official WhatsApp Cloud API (graph.facebook.com). Templates must be approved by Meta; free text only inside the
    24-hour customer-service window (checked by the caller)."""
    name = "meta-cloud"
    is_live = True

    def __init__(self, phone_number_id: str, token: str, timeout: float = 10):
        self.phone_number_id, self.token, self.timeout = phone_number_id, token, timeout
        self.label = f"Meta WhatsApp Cloud API (number id …{phone_number_id[-4:]})"

    def send(self, to_phone: str, body: str, template: str | None = None, template_name: str | None = None,
             language: str = "en", params: list[str] | None = None, from_number_id: str | None = None) -> dict:
        import socket
        import urllib.error
        import urllib.request
        if template_name:
            payload = {"messaging_product": "whatsapp", "to": to_phone.lstrip("+"), "type": "template",
                       "template": {"name": template_name, "language": {"code": language}}}
            if params:
                payload["template"]["components"] = [{"type": "body", "parameters": [{"type": "text", "text": p}
                                                                                      for p in params]}]
        else:
            payload = {"messaging_product": "whatsapp", "to": to_phone.lstrip("+"), "type": "text",
                       "text": {"body": body}}
        req = urllib.request.Request(
            f"https://graph.facebook.com/{GRAPH_VERSION}/{from_number_id or self.phone_number_id}/messages",
            data=json.dumps(payload).encode(), method="POST",
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                data = json.loads(r.read().decode())
            return {"status": "provider_accepted", "provider_message_id": data["messages"][0]["id"],
                    "detail": "Accepted by Meta (not yet delivered)"}
        except (socket.timeout, TimeoutError) as e:
            raise ProviderTimeout(str(e))
        except urllib.error.HTTPError as e:
            return {"status": "failed", "detail": f"Meta error {e.code}: {e.read().decode()[:300]}"}
        except urllib.error.URLError as e:
            if isinstance(e.reason, (socket.timeout, TimeoutError)):
                raise ProviderTimeout(str(e.reason))
            return {"status": "failed", "detail": f"Network error: {e.reason}"}


WA_SETTINGS = ["WHATSAPP_PHONE_NUMBER_ID", "WHATSAPP_ACCESS_TOKEN", "WHATSAPP_APP_SECRET", "WHATSAPP_VERIFY_TOKEN"]


def _graph(method: str, path: str, token: str, timeout: float = 15) -> tuple[bool, dict]:
    """One Graph API call with the saved token (never shown). Returns (ok, json or {'error': text})."""
    import urllib.error
    import urllib.request
    req = urllib.request.Request(f"https://graph.facebook.com/{GRAPH_VERSION}/{path}", method=method,
                                 data=b"" if method == "POST" else None,
                                 headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return True, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read().decode()).get("error", {})
            return False, {"error": f"{err.get('message', e.code)} (code {err.get('code', e.code)})"}
        except Exception:  # noqa: BLE001
            return False, {"error": f"HTTP {e.code}"}
    except Exception as e:  # noqa: BLE001 — network trouble is reported, never raised to the page
        return False, {"error": str(e)[:200]}


def connect_and_check(config=None) -> list[tuple[str, bool, str]]:
    """Admin button: make sure Meta sends this WhatsApp account's messages to our app (subscribed_apps), and report
    the number's state. Only uses the saved IDs and token; returns readable lines (label, ok, detail)."""
    token = _cfg("WHATSAPP_ACCESS_TOKEN", config)
    waba = _cfg("WHATSAPP_BUSINESS_ACCOUNT_ID", config)
    pid = _cfg("WHATSAPP_PHONE_NUMBER_ID", config)
    if not (token and waba and pid):
        return [("Settings", False, "Fill in the WhatsApp Business account ID, phone number ID and access token first.")]
    out = []
    ok, num = _graph("GET", f"{pid}?fields=display_phone_number,verified_name,name_status,quality_rating,"
                            "platform_type,code_verification_status,status", token)
    if ok:
        plat = num.get("platform_type")
        out.append(("Number", True, f"{num.get('display_phone_number')} · {num.get('verified_name')} · name "
                    f"{(num.get('name_status') or '').lower().replace('_', ' ') or '—'} · quality "
                    f"{(num.get('quality_rating') or '—').lower()}"))
        out.append(("On the Cloud API", plat == "CLOUD_API",
                    "Yes" if plat == "CLOUD_API" else f"No ({plat or 'unknown'}) — the number is registered elsewhere "
                    "(e.g. another provider's system or the WhatsApp Business app). It must be moved to the Cloud API."))
    else:
        out.append(("Number", False, num["error"] + " — check the phone number ID and that the token has "
                    "whatsapp_business_management and whatsapp_business_messaging."))
    ok, sub = _graph("POST", f"{waba}/subscribed_apps", token)
    out.append(("Messages sent to TeamOS", ok and sub.get("success", False),
                "Yes — this WhatsApp account's messages now come to TeamOS" if ok and sub.get("success")
                else (sub.get("error") or "Meta did not confirm")))
    ok, apps = _graph("GET", f"{waba}/subscribed_apps", token)
    if ok:
        names = [(a.get("whatsapp_business_api_data") or {}).get("name") or (a.get("whatsapp_business_api_data") or {}).get("id")
                 for a in apps.get("data", [])]
        out.append(("Apps receiving this account's messages", True, ", ".join(n for n in names if n) or "none"))
    return out


def send_test(to_phone: str, config=None) -> dict:
    """The admin's own test - goes through Meta for real once the settings are filled in, even before 'live'
    (nothing reaches customers). Inside 24 h of the admin messaging the number: a text; otherwise Meta's
    hello_world template."""
    missing = [k for k in ("WHATSAPP_PHONE_NUMBER_ID", "WHATSAPP_ACCESS_TOKEN") if not _cfg(k, config)]
    if missing:
        return MockWhatsAppProvider().send(to_phone, "TeamOS test message", None)
    p = MetaCloudProvider(_cfg("WHATSAPP_PHONE_NUMBER_ID", config), _cfg("WHATSAPP_ACCESS_TOKEN", config))
    res = p.send(to_phone, "✅ TeamOS test message — Salon Shastra WhatsApp is connected.", None)
    if res["status"] == "failed" and ("131047" in res.get("detail", "") or "re-engagement" in res.get("detail", "").lower()
                                      or "24 hours" in res.get("detail", "")):
        res = p.send(to_phone, "", None, template_name="hello_world", language="en_US")
    return res


def _cfg(key, config=None):
    """Server config / environment first (operators can always override), then values saved by an admin in
    Admin → Integrations (secrets decrypted only here)."""
    if config is not None and config.get(key) not in (None, ""):
        return config.get(key)
    if os.environ.get(key):
        return os.environ.get(key)
    return _admin_setting(key)


def _admin_setting(key):
    try:
        from flask import g, has_app_context
        if not has_app_context():
            return None
        if "_integration_overlay" not in g:
            from .db import get_db
            from .integrations import overlay
            g._integration_overlay = overlay(get_db())
        return g._integration_overlay.get(key) or None
    except Exception:  # never let settings lookup break a page
        return None


def setting(key):
    """Current value of an integration setting for this request (app config → env → admin-saved)."""
    from flask import current_app, has_app_context
    return _cfg(key, current_app.config if has_app_context() else None)


def public_base(request) -> str:
    """Address used in customer links: the configured public address, else the address of this request."""
    return (setting("PUBLIC_BASE_URL") or request.host_url).rstrip("/")


def whatsapp_status(config=None) -> dict:
    missing = [k for k in WA_SETTINGS if not _cfg(k, config)]
    approved = _cfg("WHATSAPP_LIVE_APPROVED", config) == "1"
    live = not missing and approved
    if live:
        label = "LIVE — Meta WhatsApp Cloud API"
        note = "Messages are sent to real customers. Only Meta-approved templates can start a conversation."
    elif not missing:
        label = "Configured but NOT approved for live use — test-only mock in use"
        note = "Credentials are present, but WHATSAPP_LIVE_APPROVED is not set. Nothing is sent to customers."
    else:
        label = "Test-only — mock provider (not connected to WhatsApp)"
        note = "No Meta account is connected. Every 'sent' message is a simulation recorded for testing."
    return {"live": live, "label": label, "note": note, "missing": missing, "approved_flag": approved,
            "provider": "meta-cloud" if live else PROVIDER_NAME}


def provider(config=None):
    st = whatsapp_status(config)
    if st["live"]:
        return MetaCloudProvider(_cfg("WHATSAPP_PHONE_NUMBER_ID", config), _cfg("WHATSAPP_ACCESS_TOKEN", config))
    return MockWhatsAppProvider()


def verify_signature(app_secret: str | None, raw_body: bytes, header: str | None) -> bool:
    """Meta signs webhook bodies with X-Hub-Signature-256: sha256=<hmac>. Reject anything unsigned or wrong."""
    if not app_secret or not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header[7:])


# -- E-mail ---------------------------------------------------------------------------------
class OutboxEmailProvider:
    name = "outbox"
    is_live = False
    label = "Test-only — e-mails are kept in the outbox (no mail server configured)"

    def send(self, to_addr, subject, body) -> dict:
        return {"status": "held", "detail": self.label}


class SmtpEmailProvider:
    name = "smtp"
    is_live = True

    def __init__(self, host, port, user, password, sender):
        self.host, self.port, self.user, self.password, self.sender = host, int(port or 587), user, password, sender
        self.label = f"SMTP via {host}"

    def send(self, to_addr, subject, body) -> dict:
        import smtplib
        from email.message import EmailMessage
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = self.sender, to_addr, subject
        msg.set_content(body)
        try:
            if self.port == 465:  # implicit TLS (Hostinger, Zoho, …)
                conn = smtplib.SMTP_SSL(self.host, self.port, timeout=15)
            else:
                conn = smtplib.SMTP(self.host, self.port, timeout=15)
            with conn as s:
                if self.port != 465:
                    s.starttls()
                if self.user:
                    s.login(self.user, self.password)
                s.send_message(msg)
            return {"status": "sent"}
        except Exception as e:  # noqa: BLE001 — provider failures must be visible, not crash the job
            return {"status": "failed", "detail": str(e)[:300]}


def email_provider(config=None):
    host = _cfg("SMTP_HOST", config)
    # a username without its password is not usable yet — keep e-mails in the outbox instead of failing them
    if host and _cfg("SMTP_FROM", config) and (not _cfg("SMTP_USER", config) or _cfg("SMTP_PASSWORD", config)):
        return SmtpEmailProvider(host, _cfg("SMTP_PORT", config), _cfg("SMTP_USER", config),
                                 _cfg("SMTP_PASSWORD", config), _cfg("SMTP_FROM", config))
    return OutboxEmailProvider()
