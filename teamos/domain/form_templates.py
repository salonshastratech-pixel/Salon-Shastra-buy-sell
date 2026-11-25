"""Versioned customer form templates (PRD §5.6, FR-009/010/043/047).

Every question is a canonical field from seller_fields.py or buyer_fields.py, so a form answer always
lands on exactly one known field. Templates are versioned: the version is stored on every invitation
and submission, so a reviewer can always see which wording the respondent answered.

Changing a template's questions = bump its version. Customer forms can be shown in Telugu (teamos/i18n.py, M8); answers are stored the same way.
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

# Guided photos of the premises: one slot each, taken straight from the phone camera (shrunk before upload).
PHOTO_TYPES = [("photo_front", "Shop front from across the road"), ("photo_street", "The street / road outside"),
               ("photo_reception", "Reception / waiting area"), ("photo_stations", "Styling chairs / stations"),
               ("photo_wash", "Hair-wash area"), ("photo_rooms", "Treatment / facial rooms"),
               ("photo_washroom", "Washroom")]
PHOTO_KEYS = {k for k, _ in PHOTO_TYPES}

DOC_TYPES = {
    "seller": PHOTO_TYPES + [
        ("lease_agreement", "Rent agreement"), ("rent_receipt", "Recent rent receipt"),
        ("maintenance_receipt", "Recent maintenance (CAM) receipt"), ("electricity_bill", "Recent electricity bill"),
        ("pos_report", "Sales / billing report (last 6 months)"), ("licence_doc", "Trade licence / GST certificate"),
        ("equipment_list", "Equipment list or bills"), ("other", "Anything else you want to share")],
    "buyer": [("funding_proof", "Proof of funds (bank statement, loan sanction letter)"),
              ("other", "Other supporting document")],
}

IMPORTANCE_SHORT = [("must", "Must"), ("prefer", "Prefer"), ("flexible", "Fine either way"), ("avoid", "Avoid")]

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
        "sections": [],  # filled below from SELLER_STEPS (every seller field appears exactly once)
        "docs": [k for k, _ in DOC_TYPES["seller"]], "extras": [],
    },
    "buyer_deep": {
        "version": "buyer-deep v1", "kind": "buyer", "public": False, "expiry_days": 14,
        "title": "Your salon acquisition requirements",
        "intro": "These answers help us find salons that genuinely fit. You can save and come back later using the "
                 "same link. If something doesn't matter to you, say so — that is different from leaving it blank.",
        "contact": [],
        "sections": [],  # filled below from BUYER_STEPS
        "docs": [k for k, _ in DOC_TYPES["buyer"]], "extras": ["localities", "choice_prefs", "share_consent"],
    },
    "targeted": {
        "version": "targeted v1", "kind": None, "public": False, "expiry_days": 7,
        "title": "A few more details, please",
        "intro": "We only need the specific information below. Each question says why we are asking.",
        "contact": [], "sections": [], "docs": [], "extras": [],
    },
}

SELLER_STEPS = [
    ("about", "About you and the sale", ["ownership_role", "sale_purpose", "sale_timeline", "transfer_authority",
                                         "signatory_name", "owners_count", "outlets_count", "existing_broker",
                                         "sale_reason", "sale_reason_note", "desired_exit_date",
                                         "transition_support", "preferred_callback"]),
    ("where", "Where is your salon?", ["address_line", "landmark", "pin_code", "maps_link", "floor_level",
                                        "area_type", "frontage_ft", "parking", "signage_visible"]),
    ("inside", "Your salon inside", ["operating_status", "started_year", "operating_years", "gender_format",
                                     "business_model", "franchise_brand", "positioning", "carpet_area_sqft", "chairs",
                                     "wash_stations", "treatment_rooms", "washrooms", "ac_type",
                                     "equipment_condition", "equipment_age_years", "inclusions_note"]),
    ("costs", "Rent and monthly costs", ["monthly_rent", "maintenance_monthly", "gst_on_rent", "rent_deposit",
                                         "deposit_treatment", "lease_expiry", "lease_renewal",
                                         "lease_transfer_status", "rent_escalation_pct", "rent_arrears",
                                         "electricity_monthly", "monthly_salaries"]),
    ("sales", "Customers and sales", ["avg_monthly_sales", "avg_monthly_expenses", "avg_monthly_bills",
                                      "avg_ticket", "repeat_customer_pct", "top_services", "google_rating",
                                      "booking_software", "prepaid_liabilities"]),
    ("team", "Your team", ["staff_headcount", "key_staff_dependence", "owner_involvement", "staff_continuity"]),
    ("papers", "Licences and dues", ["legal_name", "trade_licence", "gst_registered", "shop_establishment",
                                     "outstanding_loans", "supplier_dues", "employee_dues", "litigation"]),
    ("price", "Your price", ["asking_price", "negotiable", "negotiable_min_price", "sale_structure",
                             "stock_value_separate", "franchise_transfer_fee", "working_capital_estimate",
                             "expected_handover_weeks"]),
]
BUYER_STEPS = [
    ("about", "About you", ["need_type", "investor_type", "decision_maker", "salon_background", "involvement"]),
    ("budget", "Your budget", ["all_in_max", "budget_min", "budget_max", "includes_deposit", "includes_stock",
                               "includes_working_capital", "max_monthly_rent", "renovation_budget"]),
    ("money", "Your funds", ["finance_dependence", "funding_source", "cash_ready", "cash_ready_amount"]),
    ("where", "Where", ["preferred_city", "relocate_willing", "max_commute_km"]),
    ("salon", "The salon you want", ["chairs_min", "chairs_max", "area_min_sqft", "parking_need",
                                     "staff_continuity_need", "clientele_note"]),
    ("business", "Business and lease", ["min_monthly_sales", "min_operating_years", "turnaround_ok",
                                        "min_lease_years_left", "liability_tolerance"]),
    ("timing", "Timing and decision", ["purchase_window", "visit_availability", "transition_support_need",
                                       "payment_approver", "other_partners", "decision_timeline", "must_haves",
                                       "deal_breakers"]),
]


def _steps(steps, catalogue):
    placed = [k for _, _, keys in steps for k in keys]
    rest = [f.key for f in catalogue if f.key not in placed]
    return [(k, label, keys) for k, label, keys in steps] + ([("more", "Anything else", rest)] if rest else [])


TEMPLATES["seller_deep"]["sections"] = _steps(SELLER_STEPS, SF.FIELDS)
TEMPLATES["seller_deep"]["version"] = "seller-deep v2"
TEMPLATES["buyer_deep"]["sections"] = _steps(BUYER_STEPS, BF.FIELDS)
TEMPLATES["buyer_deep"]["version"] = "buyer-deep v2"

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
        "owners_count": "How many owners / partners?",
        "outlets_count": "How many salons do you run in total?",
        "sale_reason": "Main reason for selling",
        "desired_exit_date": "By when would you like to hand over?",
        "transition_support": "Will you help the new owner for a few weeks after the sale?",
        "preferred_callback": "Best time for us to call you",
        "address_line": "Salon address",
        "maps_link": "Google Maps link of the salon (optional)",
        "floor_level": "Which floor is the salon on?",
        "area_type": "What kind of area is it in?",
        "frontage_ft": "Width of the shop front (approx.)",
        "parking": "Parking for customers",
        "operating_status": "Is the salon running today?",
        "started_year": "In which year did the salon start?",
        "operating_years": "How many years has it been running?",
        "gender_format": "Who are your customers?",
        "business_model": "Your own brand or a franchise?",
        "franchise_brand": "Franchise brand name",
        "positioning": "Price level of your salon",
        "carpet_area_sqft": "Salon size",
        "chairs": "Number of styling chairs",
        "wash_stations": "Number of hair-wash stations",
        "treatment_rooms": "Number of treatment / facial rooms",
        "equipment_condition": "Condition of chairs and equipment",
        "inclusions_note": "What comes with the salon? (furniture, products, AC, etc.)",
        "monthly_rent": "Monthly rent",
        "maintenance_monthly": "Monthly maintenance charges",
        "rent_deposit": "Advance / security deposit paid to the landlord",
        "deposit_treatment": "Is this advance included in your price?",
        "lease_expiry": "When does your rent agreement end?",
        "lease_renewal": "Can the agreement be renewed?",
        "lease_transfer_status": "Will the landlord allow the new owner to continue?",
        "rent_escalation_pct": "Rent increase every year",
        "rent_arrears": "Any rent pending to the landlord?",
        "avg_monthly_sales": "Average sales per month",
        "avg_monthly_expenses": "Average total expenses per month",
        "avg_monthly_bills": "Customers / bills per month",
        "avg_ticket": "Average bill amount",
        "repeat_customer_pct": "Regular / repeat customers",
        "prepaid_liabilities": "Unused memberships / packages customers have paid for",
        "staff_headcount": "How many staff?",
        "key_staff_dependence": "Does the business depend on one or two key staff?",
        "owner_involvement": "How much do you work in the salon yourself?",
        "staff_continuity": "Will the staff stay with the new owner?",
        "legal_name": "Registered business name (if any)",
        "outstanding_loans": "Any business loan still to be repaid?",
        "supplier_dues": "Any amount due to suppliers?",
        "employee_dues": "Any salary / dues pending to staff?",
        "litigation": "Any legal case or dispute?",
        "asking_price": "Your expected price",
        "negotiable": "Is the price negotiable?",
        "sale_structure": "How would you like to sell?",
        "stock_value_separate": "Value of products / stock (if extra to the price)",
        "franchise_transfer_fee": "Franchise transfer fee (if any)",
        "working_capital_estimate": "Money the buyer needs to run the first months (estimate)",
        "expected_handover_weeks": "How many weeks to hand over?",
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
        "investor_type": "You are buying as",
        "involvement": "Will you run the salon yourself?",
        "budget_min": "Salon price — from",
        "budget_max": "Salon price — up to",
        "max_monthly_rent": "Highest monthly rent + maintenance you are comfortable with",
        "renovation_budget": "Money set aside for renovation",
        "finance_dependence": "Will you need a loan?",
        "funding_source": "Where will the money come from?",
        "cash_ready": "When will the money be ready?",
        "cash_ready_amount": "Amount ready today",
        "preferred_city": "City",
        "relocate_willing": "Open to another city?",
        "max_commute_km": "How far are you willing to travel daily?",
        "consider_takeover": "Would you also consider taking over a running salon?",
        "chairs_min": "At least how many styling chairs?",
        "chairs_max": "At most how many chairs?",
        "area_min_sqft": "Minimum salon size",
        "parking_need": "Customer parking",
        "clientele_note": "What kind of customers / services do you want to focus on?",
        "min_monthly_sales": "Minimum sales per month you expect",
        "min_operating_years": "Salon should be running for at least",
        "turnaround_ok": "Open to a salon that is currently in loss?",
        "min_lease_years_left": "Rent agreement should have at least",
        "liability_tolerance": "OK with some pending dues if priced in?",
        "purchase_window": "When do you want to buy?",
        "visit_availability": "When can you visit salons?",
        "other_partners": "Anyone else involved in the decision?",
        "decision_timeline": "How long will you take to decide?",
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
    "maintenance_monthly": "Common-area / building maintenance you pay every month, apart from rent.",
    "maps_link": "Open Google Maps, tap your salon's location, press Share and paste the link here. Kept private.",
    "address_line": "Kept private. Buyers only see the area until you agree to an introduction.",
    "monthly_rent": "Write like 45000 or 45k.",
    "started_year": "For example 2018.",
    "frontage_ft": "A rough guess is fine — for example 15.",
    "google_rating": "For example 4.5 (320 reviews). Leave empty if you're not sure.",
    "electricity_monthly": "A typical month is fine.",
    "monthly_salaries": "All staff together, per month.",
    "inclusions_note": "List what the buyer gets: chairs, mirrors, wash units, AC, products, software…",
    "max_monthly_rent": "Rent plus maintenance (CAM) charges per month.",
    "parking_need": "How important is parking for your customers?",
}
