"""Seller field dictionary (PRD §6.2, §6.3, §6.3A, Appendix D).

Single source of truth for: intake form rendering, validation, provenance, completeness,
privacy masking, re-review triggers and (from Milestone 4) matching inputs.
`/dictionary` renders this catalogue for founder/PM review.
"""
from __future__ import annotations

from dataclasses import dataclass, field

YES_NO_UNKNOWN = [("yes", "Yes"), ("no", "No")]

ANSWER_STATES = [
    ("value", "Answered"),
    ("unknown", "Unknown"),
    ("not_available", "Not available"),
    ("prefer_to_discuss", "Prefer to discuss"),
    ("not_applicable", "Not applicable"),
]
ANSWER_STATE_LABELS = dict(ANSWER_STATES)

EVIDENCE_STATUSES = [
    ("seller_declared", "Seller-declared"),
    ("staff_reviewed", "Staff-reviewed"),
    ("document_supported", "Document-supported"),
    ("verified", "Verified"),
    ("disputed", "Disputed"),
]
EVIDENCE_LABELS = dict(EVIDENCE_STATUSES) | {"buyer_declared": "Buyer-declared"}


@dataclass(frozen=True)
class FieldDef:
    key: str
    label: str
    section: str
    ftype: str                      # inr | int | pct | enum | text | textarea | date
    options: list = field(default_factory=list)
    privacy: str = "operational"    # operational | restricted
    material: bool = False          # change after approval => re-review; change => re-score (M4)
    matching: bool = False          # input to matching / coverage
    quick: bool = False             # part of quick screen
    help: str = ""
    show_if: tuple | None = None    # (field_key, value) conditional display
    unit: str = ""


SECTIONS = [
    ("quick", "Quick screen"),
    ("ownership", "Ownership & decision"),
    ("profile", "Business profile"),
    ("asset", "Physical asset"),
    ("lease", "Lease & landlord"),
    ("customers", "Customers & demand"),
    ("operations", "Operating summary"),
    ("staff", "Staff & operations"),
    ("liabilities", "Liabilities & obligations"),
    ("reason", "Reason & urgency"),
    ("commercial", "Commercial offer"),
]

F = FieldDef
FIELDS: list[FieldDef] = [
    # Quick screen (§6.2) — city/locality/salon name live on the business record
    F("operating_status", "Operating status", "quick", "enum",
      [("operating", "Operating"), ("non_operating", "Not operating")], quick=True, matching=True, material=True),
    F("ownership_role", "Respondent's role", "quick", "enum",
      [("owner", "Owner"), ("partner", "Partner"), ("authorised_rep", "Authorised representative"),
       ("manager", "Manager"), ("other", "Other")], quick=True, material=True),
    F("sale_purpose", "What does the owner want?", "quick", "enum",
      [("sell_entire", "Sell entire salon"), ("partnership", "Partnership / part stake"),
       ("franchise_transfer", "Franchise transfer"), ("seek_help", "Seeking help / turnaround"),
       ("unsure", "Unsure")], quick=True, matching=True, material=True,
      help="A distressed owner seeking only help must not be treated as a confirmed seller."),
    F("asking_price", "Approximate asking price", "quick", "inr", quick=True, matching=True, material=True,
      help="Net consideration the seller expects. Deposit, stock and fees are captured separately."),
    F("sale_timeline", "Sale timeline", "quick", "enum",
      [("immediate", "Immediately"), ("within_3m", "Within 3 months"), ("3_6m", "3–6 months"),
       ("6_12m", "6–12 months"), ("no_rush", "No fixed timeline")], quick=True, matching=True),
    F("preferred_callback", "Preferred callback time", "quick", "text", quick=True),

    F("legal_name", "Legal / registered business name", "ownership", "text"),
    F("signatory_name", "Who signs the sale?", "ownership", "text"),
    F("owners_count", "Number of owners / partners", "ownership", "int"),
    F("outlets_count", "Outlets operated by this owner", "ownership", "int"),
    F("transfer_authority", "Respondent has authority to transfer?", "ownership", "enum", YES_NO_UNKNOWN,
      matching=True, material=True),
    F("existing_broker", "Already listed with another broker?", "ownership", "enum", YES_NO_UNKNOWN),

    F("operating_years", "Years in operation", "profile", "int", matching=True, unit="years"),
    F("gender_format", "Format", "profile", "enum",
      [("men", "Men"), ("women", "Women"), ("unisex", "Unisex")], matching=True, material=True),
    F("business_model", "Independent or franchise", "profile", "enum",
      [("independent", "Independent"), ("franchise", "Franchise")], matching=True, material=True),
    F("franchise_brand", "Franchise brand", "profile", "text", show_if=("business_model", "franchise"),
      privacy="restricted", help="Can identify the salon; hidden from teaser."),
    F("positioning", "Positioning", "profile", "enum",
      [("luxury", "Luxury"), ("premium", "Premium"), ("mid", "Mid-market"), ("value", "Value")], matching=True),
    F("address_line", "Street address", "profile", "text", privacy="restricted",
      help="Exact address is restricted; the locality is operational."),
    F("landmark", "Nearby landmark", "profile", "text"),
    F("pin_code", "PIN code", "profile", "text", matching=True),

    F("carpet_area_sqft", "Carpet area", "asset", "int", matching=True, unit="sq ft"),
    F("chairs", "Chairs / styling stations", "asset", "int", matching=True, material=True),
    F("wash_stations", "Wash / shampoo stations", "asset", "int", matching=True),
    F("treatment_rooms", "Treatment rooms", "asset", "int", matching=True),
    F("equipment_condition", "Equipment condition", "asset", "enum",
      [("excellent", "Excellent"), ("good", "Good"), ("fair", "Fair"), ("poor", "Poor")], matching=True),
    F("inclusions_note", "Included / excluded assets", "asset", "textarea"),

    F("monthly_rent", "Monthly rent", "lease", "inr", matching=True, material=True),
    F("rent_deposit", "Rent / security deposit", "lease", "inr", matching=True, material=True),
    F("deposit_treatment", "How is the deposit handled in the sale?", "lease", "enum",
      [("included_in_asking", "Included in asking price"), ("buyer_pays_separately", "Buyer pays separately"),
       ("not_applicable", "No deposit")], matching=True, material=True,
      help="Drives the all-in buyer cash requirement. Unknown keeps the total incomplete."),
    F("lease_expiry", "Lease expiry date", "lease", "date", matching=True, material=True),
    F("lease_renewal", "Renewal possible?", "lease", "enum",
      [("yes", "Yes"), ("no", "No"), ("negotiable", "Negotiable")], matching=True),
    F("lease_transfer_status", "Lease transferability", "lease", "enum",
      [("unchecked", "Unchecked"), ("landlord_pending", "Landlord discussion pending"),
       ("confirmed_transferable", "Confirmed transferable"), ("not_permitted", "Not permitted")],
      matching=True, material=True),
    F("rent_escalation_pct", "Annual rent escalation", "lease", "pct", unit="%"),
    F("rent_arrears", "Rent arrears", "lease", "inr", matching=True),

    F("avg_monthly_bills", "Average monthly bills / visits", "customers", "int", matching=True),
    F("avg_ticket", "Average ticket size", "customers", "inr"),
    F("repeat_customer_pct", "Repeat customers", "customers", "int", unit="%"),
    F("prepaid_liabilities", "Outstanding memberships / prepaid services", "customers", "inr", matching=True),

    F("avg_monthly_sales", "Average monthly sales (declared)", "operations", "inr", matching=True, material=True,
      help="Summary claim. Month-by-month figures and evidence go in Financials."),
    F("avg_monthly_expenses", "Average monthly operating expenses (declared)", "operations", "inr",
      matching=True, material=True),

    F("staff_headcount", "Total staff", "staff", "int", matching=True),
    F("key_staff_dependence", "Dependence on key staff", "staff", "enum",
      [("high", "High"), ("medium", "Medium"), ("low", "Low")], matching=True),
    F("owner_involvement", "Owner involvement", "staff", "enum",
      [("full_time", "Full-time"), ("part_time", "Part-time"), ("absentee", "Absentee")], matching=True),
    F("staff_continuity", "Staff expected to stay after sale?", "staff", "enum",
      [("yes", "Yes"), ("partial", "Partially"), ("no", "No")], matching=True),

    F("outstanding_loans", "Outstanding business loans", "liabilities", "inr", matching=True, material=True),
    F("supplier_dues", "Supplier dues", "liabilities", "inr", matching=True),
    F("employee_dues", "Employee dues", "liabilities", "inr", matching=True),
    F("litigation", "Pending disputes / litigation?", "liabilities", "enum", YES_NO_UNKNOWN,
      matching=True, material=True),

    F("sale_reason", "Reason for sale (category)", "reason", "enum",
      [("relocation", "Relocation"), ("new_venture", "New venture"), ("partnership_change", "Partnership change"),
       ("retirement", "Retirement"), ("financial_pressure", "Financial pressure"), ("personal", "Personal"),
       ("other", "Other")]),
    F("sale_reason_note", "Private context (only if volunteered)", "reason", "textarea", privacy="restricted",
      help="Optional. Visible only to the primary owner, approvers and explicit grantees."),
    F("desired_exit_date", "Desired exit date", "reason", "date", matching=True),
    F("transition_support", "Will the owner support transition?", "reason", "enum",
      [("yes", "Yes"), ("no", "No"), ("negotiable", "Negotiable")], matching=True),

    F("negotiable", "Is the price negotiable?", "commercial", "enum", YES_NO_UNKNOWN, matching=True),
    F("negotiable_min_price", "Lowest acceptable price (if volunteered)", "commercial", "inr",
      privacy="restricted", show_if=("negotiable", "yes"),
      help="Never shown to buyers. Used internally to identify near matches."),
    F("sale_structure", "Sale structure", "commercial", "enum",
      [("entity", "Entire business / entity"), ("asset", "Asset sale"), ("partial_stake", "Partial stake"),
       ("management", "Management agreement"), ("franchise_transfer", "Franchise transfer")],
      matching=True, material=True),
    F("stock_value_separate", "Stock / inventory payable separately", "commercial", "inr", matching=True,
      material=True, help="Use 'Not applicable' if stock is included in the asking price."),
    F("franchise_transfer_fee", "Franchise transfer fee", "commercial", "inr", matching=True, material=True,
      show_if=("business_model", "franchise")),
    F("working_capital_estimate", "Working capital / handover cost estimate", "commercial", "inr", matching=True),
    F("expected_handover_weeks", "Expected handover period", "commercial", "int", unit="weeks"),
]

BY_KEY = {f.key: f for f in FIELDS}
MATERIAL_KEYS = {f.key for f in FIELDS if f.material}
RESTRICTED_KEYS = {f.key for f in FIELDS if f.privacy == "restricted"}
MATCHING_KEYS = [f.key for f in FIELDS if f.matching]


def fields_in(section: str) -> list[FieldDef]:
    return [f for f in FIELDS if f.section == section]


def option_label(key: str, value) -> str:
    fd = BY_KEY.get(key)
    if fd and fd.options:
        return dict(fd.options).get(value, str(value))
    return str(value)


def is_applicable(fd: FieldDef, listing: dict) -> bool:
    if not fd.show_if:
        return True
    k, v = fd.show_if
    return listing.get(k) == v


LISTING_CHECKLIST = [
    ("authority_checked", "Seller authority checked"),
    ("mandate_consent", "Sale mandate or consent documented"),
    ("profile_complete", "Business profile complete"),
    ("asking_components", "Asking-price components clear"),
    ("lease_reviewed", "Lease and transferability reviewed"),
    ("asset_scope", "Asset / inventory scope recorded"),
    ("liabilities_noted", "Liabilities noted"),
    ("data_confidence", "Data source and confidence documented"),
    ("teaser_reviewed", "Confidential teaser reviewed"),
    ("media_permissions", "Media permissions"),
    ("disclaimers", "Legal / commercial disclaimers"),
]
CHECKLIST_STATUSES = [
    ("not_requested", "Not requested"), ("pending", "Pending"), ("received", "Received"),
    ("reviewed", "Reviewed"), ("unresolved", "Unresolved"), ("not_applicable", "Not applicable"),
]
