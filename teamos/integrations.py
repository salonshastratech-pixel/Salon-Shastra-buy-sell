"""Admin-configurable integrations (future-ready connector registry).

Every connector is described once in CONNECTORS: its settings, which of them are secret, and its status. Admins set
values in Admin → Integrations; secrets are encrypted with the server key and never shown back (only "set").
Order of precedence when the app reads a setting: server environment / app config → value saved by an admin.
Adding a new connector later = one entry here + the code that uses it; the admin screen follows automatically.
"""
from __future__ import annotations

import secrets as _secrets

from . import audit
from .permissions import user_can
from .timeutil import now_iso

CONNECTORS = [
    {"key": "whatsapp", "name": "WhatsApp Business (Meta Cloud API)", "status": "built",
     "what": "Send approved templates and replies from the company number and each team member's number; receive "
             "customer messages and delivery ticks in the team inbox.",
     "settings": [
         ("WHATSAPP_BUSINESS_ACCOUNT_ID", "WhatsApp Business account ID", False, "From WhatsApp Manager → Account tools."),
         ("WHATSAPP_PHONE_NUMBER_ID", "Company number — Meta phone number ID", False,
          "WhatsApp Manager → Phone numbers → the number's ID (digits)."),
         ("WHATSAPP_DISPLAY_NUMBER", "Company WhatsApp number (as customers see it)", False, "e.g. +91 98xxx xxxxx"),
         ("WHATSAPP_ACCESS_TOKEN", "Permanent access token (system user)", True,
          "Meta Business settings → System users → generate token with whatsapp_business_messaging."),
         ("WHATSAPP_APP_SECRET", "Meta app secret", True, "Used to check that webhook calls really come from Meta."),
         ("WHATSAPP_VERIFY_TOKEN", "Webhook verify token", True, "Any long random text; paste the same in Meta."),
     ]},
    {"key": "email", "name": "E-mail (SMTP)", "status": "built",
     "what": "Morning summaries and staff notifications by e-mail.",
     "settings": [
         ("SMTP_HOST", "SMTP server", False, "e.g. smtp.gmail.com or your provider's host"),
         ("SMTP_PORT", "Port", False, "Usually 587"),
         ("SMTP_USER", "Username", False, ""),
         ("SMTP_PASSWORD", "Password / app password", True, ""),
         ("SMTP_FROM", "Send from address", False, "e.g. team@salonshastra.in"),
     ]},
    {"key": "links", "name": "Customer links (forms & summaries)", "status": "built",
     "what": "The public address used in personal form links and confidential salon summaries.",
     "settings": [("PUBLIC_BASE_URL", "Public address of TeamOS", False, "e.g. https://team.salonshastra.in")]},
    {"key": "ai", "name": "AI writing assistant (Claude)", "status": "built",
     "what": "Better-worded record summaries when someone presses \"Draft summary\". Off by default — the built-in "
             "drafter works without it. Only non-identifying facts are sent (never names, phones, e-mails, addresses "
             "or private notes) and a person always checks the draft before it is saved.",
     "settings": [
         ("AI_ASSISTANT_ENABLED", "Switched on? (yes / no)", False, "Type yes to use Claude for drafts."),
         ("ANTHROPIC_API_KEY", "Anthropic API key", True, "From console.anthropic.com → API keys."),
         ("AI_MODEL", "Model (optional)", False, "Leave empty for claude-opus-5-5."),
     ]},
    {"key": "meta_leads", "name": "Facebook / Instagram lead forms", "status": "planned",
     "what": "New leads from Meta ad forms appear automatically as seller or buyer leads, with the first-call task.",
     "settings": []},
    {"key": "telephony", "name": "Click-to-call & call logging (e.g. Exotel, Knowlarity)", "status": "planned",
     "what": "Call from TeamOS; calls, durations and recordings are logged on the record automatically.",
     "settings": []},
    {"key": "calendar", "name": "Google Calendar", "status": "planned",
     "what": "Salon visits and call-backs appear in each team member's calendar.", "settings": []},
    {"key": "sheets", "name": "Google Sheets export", "status": "planned",
     "what": "Scheduled export of reports to a shared sheet.", "settings": []},
    {"key": "payments", "name": "Payment links (e.g. Razorpay)", "status": "planned",
     "what": "Send fee payment links and record receipts automatically in Admin Finance.", "settings": []},
]
SETTINGS = {k: (label, secret, help_) for c in CONNECTORS for k, label, secret, help_ in c["settings"]}


def _fernet():
    from .files import _fernet as f
    return f()


def overlay(db) -> dict:
    """All admin-saved values, decrypted (used by messaging._cfg). Never rendered to a page."""
    out = {}
    try:
        rows = db.all("SELECT key, value, is_secret FROM integration_settings")
    except Exception:  # table not migrated yet
        return out
    fer = None
    for r in rows:
        if not r["value"]:
            continue
        if r["is_secret"]:
            fer = fer or _fernet()
            if not fer:
                continue
            try:
                out[r["key"]] = fer.decrypt(r["value"].encode()).decode()
            except Exception:  # key rotated / corrupted: treat as not set
                continue
        else:
            out[r["key"]] = r["value"]
    return out


def describe(db) -> dict:
    """{key: 'set' / value} for the admin screen — secrets only ever as 'set'."""
    out = {}
    for r in db.all("SELECT key, value, is_secret FROM integration_settings"):
        if r["value"]:
            out[r["key"]] = "set" if r["is_secret"] else r["value"]
    return out


def save(db, actor, values: dict) -> list[str]:
    """Save the submitted settings. Blank secret = keep the current one; '-' clears a value. Returns changed keys."""
    from .domain.errors import PermissionDenied, ValidationError
    if not user_can(db, actor, "settings.manage"):
        raise PermissionDenied("settings.manage")
    changed = []
    now = now_iso()
    with db:
        for key, raw in values.items():
            if key not in SETTINGS:
                continue
            _label, secret, _h = SETTINGS[key]
            v = (raw or "").strip()
            if secret and v == "":
                continue  # keep
            if v == "-":
                v = ""
            if key == "PUBLIC_BASE_URL" and v and not v.startswith("https://") and not v.startswith("http://localhost"):
                raise ValidationError({key: "The public address must start with https://"})
            if key in ("WHATSAPP_PHONE_NUMBER_ID", "WHATSAPP_BUSINESS_ACCOUNT_ID", "SMTP_PORT") and v and not v.isdigit():
                raise ValidationError({key: "Digits only."})
            stored = v
            if secret and v:
                fer = _fernet()
                if not fer:
                    raise ValidationError({key: "The server encryption key (FILES_KEY) is missing, so secrets cannot "
                                                "be saved here. Set them as server environment variables instead."})
                stored = fer.encrypt(v.encode()).decode()
            ex = db.one("SELECT * FROM integration_settings WHERE key = ?", (key,))
            if ex and (ex["value"] or "") == stored:
                continue
            if ex:
                db.update("integration_settings", ex["id"], {"value": stored, "is_secret": int(secret),
                                                             "updated_by": actor["id"], "updated_at": now})
            else:
                db.insert("integration_settings", {"key": key, "value": stored, "is_secret": int(secret),
                                                   "updated_by": actor["id"], "updated_at": now})
            changed.append(key)
        if changed:
            audit.record(db, actor["id"], "integration.settings_saved", None, None,
                         {"keys": changed})  # names only — never values
    return changed


def set_live(db, actor, on: bool, confirm_text: str = "") -> None:
    """Founder switch: only after typing GO LIVE. Turning off is always allowed."""
    from .domain.errors import PermissionDenied, ValidationError
    if not user_can(db, actor, "settings.manage"):
        raise PermissionDenied("settings.manage")
    if on and (confirm_text or "").strip().upper() != "GO LIVE":
        raise ValidationError({"live": "Type GO LIVE to confirm that real customers will receive messages."})
    now = now_iso()
    with db:
        ex = db.one("SELECT * FROM integration_settings WHERE key = 'WHATSAPP_LIVE_APPROVED'")
        val = "1" if on else ""
        if ex:
            db.update("integration_settings", ex["id"], {"value": val, "updated_by": actor["id"], "updated_at": now})
        else:
            db.insert("integration_settings", {"key": "WHATSAPP_LIVE_APPROVED", "value": val, "is_secret": 0,
                                               "updated_by": actor["id"], "updated_at": now})
        audit.record(db, actor["id"], "integration.whatsapp_live" if on else "integration.whatsapp_test_only",
                     None, None, {})


def new_verify_token() -> str:
    return _secrets.token_urlsafe(24)
