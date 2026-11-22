"""Versioned customer form templates (PRD §5.6, FR-009/010/043/047).

Every question is a canonical field from seller_fields.py or buyer_fields.py, so a form answer always
lands on exactly one known field. Templates are versioned: the version is stored on every invitation
and submission, so a reviewer can always see which wording the respondent answered.

Changing a template's questions = bump its version. English only for now (Telugu is P1, FR-052).
"""
from __future__ import annotations

from . import buyer_fields as BF
from . import seller_fields as SF

CONSENT_TEXT = {
    "service_contact": ("public-form-consent-v0.1",
                        "I agree that Salon Shastra may contact me about this enquiry by phone or WhatsApp."),
    "share_teaser": ("buyer-criteria-share-v0.1",
                     "Salon Shastra may share a suitable summary of my requirements with sellers or their "
                     "representatives. My name and number are not shared without my permission."),
}

DOC_TYPES = {
    "seller": [("lease_agreement", "Lease / rent agreement"), ("pos_report", "Sales / POS report (last 6 months)"),
               ("rent_receipt", "Recent rent receipt"), ("other", "Other supporting document")],
    "buyer": [("funding_proof", "Proof of funds (bank statement, loan sanction letter)"),
              ("other", "Other supporting document")],
}

# Respondent-friendly answer states (the staff intake has the full list).
RESPONDENT_STATES = [("unknown", "I don't know"), ("prefer_to_discuss", "Prefer to discuss on a call"),
                     ("not_applicable", "Not applicable")]

SELLER_CONTACT = ["full_name", "phone", "email", "salon_name", "city", "locality"]
BUYER_CONTACT = ["full_name", "phone", "email", "city", "locality"]
CONTACT_LABELS = {"full_name": "Your name", "phone": "Mobile number", "email": "Email (optional)",
                  "salon_name": "Salon name", "city": "City", "locality": "Area / locality"}
CONTACT_REQUIRED = {"full_name", "phone", "city", "salon_name"}

TEMPLATES: dict[str, dict] = {
    "seller_quick": {
        "version": "seller-quick v1", "kind": "seller", "public": True,
        "title": "Thinking of selling your salon?",
        "intro": "A few quick questions so the right person from Salon Shastra can call you. It takes about 2 minutes.",
        "contact": SELLER_CONTACT,
        "sections": [("quick", "About the salon", [f.key for f in SF.FIELDS if f.quick and f.key != "preferred_callback"]
                      + ["preferred_callback"])],
        "docs": [], "extras": [],
    },
    "buyer_quick": {
        "version": "buyer-quick v1", "kind": "buyer", "public": True,
        "title": "Looking to buy or invest in a salon?",
        "intro": "Tell us what you are looking for. A member of the Salon Shastra team will call you to understand more.",
        "contact": BUYER_CONTACT,
        "sections": [("quick", "What you are looking for", [f.key for f in BF.FIELDS if f.quick
                                                            and f.key != "preferred_city"])],
        "docs": [], "extras": ["share_consent"],
    },
    "seller_deep": {
        "version": "seller-deep v1", "kind": "seller", "public": False, "expiry_days": 14,
        "title": "Salon details for your sale",
        "intro": "These answers help us present your salon accurately to serious buyers. You can save and come back "
                 "later using the same link. If you don't know an answer, choose \"I don't know\" — never guess.",
        "contact": [],
        "sections": [(k, label, [f.key for f in SF.fields_in(k)]) for k, label in SF.SECTIONS],
        "docs": [k for k, _ in DOC_TYPES["seller"]], "extras": [],
    },
    "buyer_deep": {
        "version": "buyer-deep v1", "kind": "buyer", "public": False, "expiry_days": 14,
        "title": "Your salon acquisition requirements",
        "intro": "These answers help us find salons that genuinely fit. You can save and come back later using the "
                 "same link. If something doesn't matter to you, say so — that is different from leaving it blank.",
        "contact": [],
        "sections": [(k, label, [f.key for f in BF.fields_in(k)]) for k, label in BF.SECTIONS],
        "docs": [k for k, _ in DOC_TYPES["buyer"]], "extras": ["localities", "choice_prefs", "share_consent"],
    },
    "targeted": {
        "version": "targeted v1", "kind": None, "public": False, "expiry_days": 7,
        "title": "A few more details, please",
        "intro": "We only need the specific information below. Each question says why we are asking.",
        "contact": [], "sections": [], "docs": [], "extras": [],
    },
}

PURPOSES = {  # "Show exact field and purpose" (§10.4) — default purpose shown next to a targeted question
    "rent_deposit": "Buyers need the full cash requirement; the deposit is often paid separately.",
    "deposit_treatment": "Tells buyers whether the deposit is inside or on top of your asking price.",
    "lease_expiry": "Buyers check how many years remain on the lease.",
    "lease_transfer_status": "A lease that cannot be transferred blocks most sales.",
    "chairs": "Buyers filter by salon size.",
    "avg_monthly_sales": "Used to judge the salon's earning potential.",
    "avg_monthly_expenses": "Needed with sales to estimate the monthly surplus.",
    "all_in_max": "We only show you salons whose total cash requirement fits.",
    "cash_ready": "Sellers prioritise buyers who can move quickly.",
    "decision_maker": "We need to know who will approve the purchase.",
}


def field_def(kind: str, key: str):
    return (SF.BY_KEY if kind == "seller" else BF.BY_KEY).get(key)


def catalogue(kind: str):
    return SF.FIELDS if kind == "seller" else BF.FIELDS


def requestable_fields(kind: str) -> list:
    """Fields staff may include in a targeted request (all canonical, none reviewer-only)."""
    return [f for f in catalogue(kind) if f.key not in BF.PRIVILEGED_KEYS]


def build(template_key: str, kind: str | None = None, field_keys: list[str] | None = None,
          doc_types: list[str] | None = None) -> dict:
    """Return the concrete template (targeted requests are assembled from the chosen fields)."""
    t = dict(TEMPLATES[template_key])
    if template_key == "targeted":
        t["kind"] = kind
        keys = [k for k in (field_keys or []) if field_def(kind, k)]
        t["sections"] = [("targeted", "Requested information", keys)] if keys else []
        t["docs"] = [d for d in (doc_types or []) if d in dict(DOC_TYPES[kind])]
    t["key"] = template_key
    return t


def template_field_keys(t: dict) -> list[str]:
    return [k for _, _, keys in t["sections"] for k in keys]


# Customer-facing wording where the staff label talks about "the respondent" / "the owner".
RESPONDENT_LABELS = {
    "seller": {
        "ownership_role": "Your role in the salon",
        "sale_purpose": "What would you like to do?",
        "transfer_authority": "Do you have the authority to transfer the salon?",
        "sale_reason_note": "Anything else about why you are selling? (optional)",
        "negotiable_min_price": "Lowest price you would accept (optional)",
        "signatory_name": "Who would sign the sale agreement?",
        "existing_broker": "Is the salon already listed with another broker?",
    },
    "buyer": {
        "need_type": "What are you looking for?",
        "decision_maker": "Are you the decision maker?",
        "salon_background": "Your salon / beauty-industry background",
        "all_in_max": "Maximum TOTAL cash you can put in",
        "includes_deposit": "Does that total already cover a rent deposit?",
        "includes_stock": "…and stock / inventory?",
        "includes_working_capital": "…and working capital?",
        "staff_continuity_need": "Should the existing staff stay?",
        "transition_support_need": "Do you need the seller's help during handover?",
        "payment_approver": "Who will approve the payment?",
        "must_haves": "Your must-haves",
        "deal_breakers": "Your deal-breakers",
    },
}


def label(kind: str, key: str) -> str:
    fd = field_def(kind, key)
    return RESPONDENT_LABELS.get(kind, {}).get(key) or (fd.label if fd else key)

# Help text shown to customers. Staff guidance in the field dictionaries (`help=`) is internal and is
# deliberately NOT shown on customer forms.
RESPONDENT_HELP = {
    "asking_price": "The price you expect for the business. The rent deposit and stock are asked separately.",
    "all_in_max": "Price + rent deposit + stock + working capital + any transfer fee — the total you can put in.",
    "rent_deposit": "The security deposit paid to your landlord.",
    "deposit_treatment": "Is the deposit part of your asking price, or would the buyer pay it on top?",
    "stock_value_separate": "Choose 'Not applicable' if stock is included in your asking price.",
    "need_type": "Opening a brand-new salon is different from buying one — choose what fits.",
    "avg_monthly_sales": "A rough monthly average is fine. We will ask for supporting reports separately.",
}
