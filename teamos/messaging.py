"""Outbound messaging provider layer (PRD §10; live adapter is Milestone 7).

Milestone 4 ships ONLY a mock WhatsApp provider for UAT-41(mock): it records exactly what would have been
sent, to whom, from which record and with which template, and returns a mock provider message ID.
Nothing leaves this server. The status recorded is "provider_accepted" — never "delivered" or "read",
because a provider acknowledgement is not delivery proof (§10.3) and a mock cannot know either.
"""
from __future__ import annotations

import uuid

PROVIDER_NAME = "mock-whatsapp"
PROVIDER_LABEL = "TEST-ONLY mock WhatsApp provider — no message leaves this server"

# Local drafts pending Meta approval. Text must not promise a deal, return or buyer availability (§10.4).
TEMPLATES = {
    "deep_form_link": "Hello {name}, this is {sender} from Salon Shastra. As discussed, please share your details "
                      "securely using this personal link: {link} . It expires on {expires}. Reply STOP to opt out.",
    "teaser_share": "Hello {name}, this is {sender} from Salon Shastra. Here is a confidential summary of a salon that may "
                    "fit your requirements: {link} (valid till {expires}). Reply STOP to opt out.",
    "targeted_request": "Hello {name}, this is {sender} from Salon Shastra. To move forward we need a few specific "
                        "details. Please use this personal link: {link} (expires {expires}). Reply STOP to opt out.",
}


class MockWhatsAppProvider:
    name = PROVIDER_NAME
    label = PROVIDER_LABEL
    is_live = False

    def send_template(self, to_phone: str, template_key: str, variables: dict) -> dict:
        if template_key not in TEMPLATES:
            return {"status": "failed", "detail": f"Unknown template {template_key}"}
        if not to_phone:
            return {"status": "failed", "detail": "No phone number"}
        return {"status": "provider_accepted", "provider_message_id": f"mock-{uuid.uuid4().hex[:16]}",
                "detail": PROVIDER_LABEL}


def provider():
    return MockWhatsAppProvider()


def render(template_key: str, variables: dict) -> str:
    return TEMPLATES[template_key].format(**variables)
