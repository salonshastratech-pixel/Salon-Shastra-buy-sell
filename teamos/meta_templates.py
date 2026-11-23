"""Meta (WhatsApp) template rules: format, category, lint and the cross-check with the templates Meta has approved.

Live sending only ever uses a template that Meta has APPROVED and whose approved wording matches ours (same text,
same placeholders in the same order). Everything here is deterministic and testable without Meta.
"""
from __future__ import annotations

import json
import re

from . import messaging

PLACEHOLDER = messaging.PLACEHOLDER
# Meta's category for each built-in template. Re-engagement ("are you still exploring?") and offers are MARKETING
# under Meta's rules; follow-ups on the customer's own enquiry / deal are UTILITY. "REPLY" = only ever sent as a free
# reply inside the 24-hour window (no template needed).
META_CATEGORY = {
    "deep_form_link": "UTILITY", "targeted_request": "UTILITY", "teaser_share": "UTILITY", "enquiry_ack": "UTILITY",
    "callback_request": "UTILITY", "incomplete_form": "UTILITY", "visit_confirm": "UTILITY",
    "call_thanks_seller": "UTILITY", "call_thanks_buyer": "UTILITY", "callback_confirm": "UTILITY",
    "docs_checklist": "UTILITY", "funds_proof": "UTILITY", "listing_live": "UTILITY", "shortlist_update": "UTILITY",
    "next_steps": "UTILITY", "inactive_followup": "MARKETING", "market_update": "MARKETING", "optout_confirm": "REPLY",
}
# Example values Meta asks for when a template is submitted.
SAMPLES = {"name": "Ravi", "sender": "Harika", "link": "https://team.salonshastra.in/f/i/abc123",
           "expires": "30 Nov 2026", "when": "Sat 30 Nov, 11:00 AM", "area": "Kondapur"}
MARKETING_WORDS = re.compile(r"\b(offer|discount|deal of|limited time|new salons available|still exploring|"
                             r"hurry|best price|free)\b", re.I)
MAX_BODY = 1024


def meta_name(key: str) -> str:
    """Meta template names: lowercase letters, digits and underscores."""
    return re.sub(r"[^a-z0-9_]", "_", key.lower())[:512]


def to_meta(body: str) -> tuple[str, list[str]]:
    """'Hello {name}, this is {sender}' -> ('Hello {{1}}, this is {{2}}', ['name', 'sender'])."""
    order = messaging.ordered_placeholders(body)
    pos = {k: i + 1 for i, k in enumerate(order)}
    return PLACEHOLDER.sub(lambda m: "{{%d}}" % pos[m.group(1)], body), order


def lint(key: str, body: str, category: str | None) -> list[str]:
    """Problems Meta would reject or re-categorise, in plain words."""
    issues = []
    text = (body or "").strip()
    if len(text) > MAX_BODY:
        issues.append(f"Too long for WhatsApp ({len(text)} characters; Meta allows {MAX_BODY}).")
    if PLACEHOLDER.match(text):
        issues.append("Starts with a placeholder — Meta rejects templates that begin with a variable.")
    if re.search(r"\{[a-z_]+\}\s*$", text):
        issues.append("Ends with a placeholder — Meta rejects templates that end with a variable.")
    if re.search(r"\{[a-z_]+\}\s*\{[a-z_]+\}", text):
        issues.append("Two placeholders side by side — put words between them.")
    n_vars = len(PLACEHOLDER.findall(text))
    words = len(PLACEHOLDER.sub("", text).split())
    if n_vars and words < 3 * n_vars + 2:
        issues.append("Too many placeholders for the amount of text — Meta may reject it as unclear.")
    if category == "UTILITY" and MARKETING_WORDS.search(text):
        issues.append("Sounds promotional — Meta will treat it as MARKETING (needs marketing consent).")
    if category != "REPLY" and "stop" not in text.lower():
        issues.append("No opt-out line (e.g. 'Reply STOP to opt out').")
    if re.search(r"[\n\t]| {5,}", text):
        issues.append("Contains line breaks/tabs or long spaces — keep the body on one line for Meta.")
    return issues


def clean_param(v) -> str:
    """Meta rejects parameters with new lines, tabs or more than 4 spaces in a row."""
    return re.sub(r" {4,}", "   ", re.sub(r"[\r\n\t]+", " ", str(v))).strip()


def normalise(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip().lower()


def fetch_meta_templates(config) -> list[dict]:
    """Read the templates in Salon Shastra's WhatsApp Business account (needs account ID + token)."""
    import urllib.request
    waba = messaging._cfg("WHATSAPP_BUSINESS_ACCOUNT_ID", config)
    token = messaging._cfg("WHATSAPP_ACCESS_TOKEN", config)
    if not (waba and token):
        raise ValueError("Fill in the WhatsApp Business account ID and access token in Integrations first.")
    url = (f"https://graph.facebook.com/{messaging.GRAPH_VERSION}/{waba}/message_templates"
           "?fields=name,status,category,language,components&limit=250")
    out = []
    while url:
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode())
        out.extend(data.get("data", []))
        url = (data.get("paging") or {}).get("next")
    return out


def _meta_body(t: dict) -> str:
    for c in t.get("components") or []:
        if (c.get("type") or "").upper() == "BODY":
            return c.get("text") or ""
    return ""


def cross_check(db, actor_id: int | None, meta_templates: list[dict]) -> dict:
    """Compare every local template with Meta's list. Only APPROVED + same wording becomes usable live."""
    from .timeutil import now_iso
    by_name = {}
    for m in meta_templates:
        by_name.setdefault(m.get("name"), []).append(m)
    counts = {"approved": 0, "mismatch": 0, "pending": 0, "rejected": 0, "missing": 0}
    now = now_iso()
    with db:
        for t in db.all("SELECT * FROM message_templates"):
            if META_CATEGORY.get(t["key"]) == "REPLY":
                continue
            name = t["provider_name"] or meta_name(t["key"])
            cands = by_name.get(name) or []
            m = next((c for c in cands if (c.get("language") or "").split("_")[0] == (t["language"] or "en")), None) \
                or (cands[0] if cands else None)
            upd = {"meta_checked_at": now}
            if not m:
                upd.update(meta_status="NOT_FOUND", meta_body=None, meta_issue=f"No template named '{name}' in Meta.",
                           status="approved_local" if t["status"] == "provider_approved" else t["status"])
                counts["missing"] += 1
            else:
                status = (m.get("status") or "").upper()
                mbody = _meta_body(m)
                ours, _ = to_meta(t["body"])
                upd.update(meta_status=status, meta_body=mbody, meta_category=(m.get("category") or "").upper() or None,
                           provider_name=name, language=m.get("language") or t["language"])
                if status != "APPROVED":
                    upd.update(meta_issue=f"Meta status is {status.lower()}.",
                               status="approved_local" if t["status"] == "provider_approved" else t["status"])
                    counts["rejected" if status in ("REJECTED", "DISABLED", "PAUSED") else "pending"] += 1
                elif normalise(mbody) != normalise(ours):
                    upd.update(meta_issue="Wording differs from the text Meta approved — fix one so they match.",
                               status="approved_local" if t["status"] == "provider_approved" else t["status"])
                    counts["mismatch"] += 1
                else:
                    upd.update(meta_issue=None, status="provider_approved")
                    counts["approved"] += 1
            db.update("message_templates", t["id"], upd)
        from . import audit
        audit.record(db, actor_id, "templates.meta_cross_check", None, None, counts)
    return counts
