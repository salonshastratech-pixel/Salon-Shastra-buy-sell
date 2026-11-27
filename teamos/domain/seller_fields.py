"""Seller field dictionary (PRD §6.2, §6.3, §6.3A, Appendix D).

Single source of truth for: intake form rendering, validation, provenance, completeness,
privacy masking, re-review triggers and (from Milestone 4) matching inputs.
`/dictionary` renders this catalogue for founder/PM review.
"""
from __future__ import annotations

from dataclasses import dataclass, field

YES_NO_UNKNOWN = [("yes", "Yes"), ("no", "No")]
LICENCE = [("yes", "Yes, have it"), ("applied", "Applied / renewing"), ("no", "No")]
# Shared vocabulary: the buyer form offers exactly these options, so matching compares like with like.
FLOOR_LEVELS = [("ground", "Ground floor"), ("first", "First floor"), ("upper", "Second floor or higher"),
                ("mall", "Inside a mall"), ("basement", "Basement")]
AREA_TYPES = [("main_road", "Main road / high street"), ("market", "Market / shopping street"),
              ("mall", "Mall"), ("commercial", "Commercial complex / office area"),
              ("residential", "Residential colony"), ("apartment", "Inside an apartment community")]
PARKING = [("car_and_bike", "Cars and two-wheelers"), ("bike_only", "Two-wheelers only"), ("none", "No parking")]

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
    show_if: tuple | None = None    # ask only when: (key, value or (values…)), or a list of such — any one is enough
    unit: str = ""
    also_if: tuple | None = None    # and ALSO this must hold: (key, value or (values…)); '' means "not answered yet"

    def conditions(self) -> list[tuple[str, frozenset]]:
        if not self.show_if:
            return []
        conds = self.show_if if isinstance(self.show_if, list) else [self.show_if]
        return [(k, frozenset(v if isinstance(v, (tuple, list, set, frozenset)) else (v,))) for k, v in conds]

    @property
    def show_attr(self) -> str:
        """For the page's script: 'sale_purpose=sell_entire|partnership;negotiable=yes' (any one condition)."""
        return ";".join(f"{k}={'|'.join(sorted(v))}" for k, v in self.conditions())

    def also_conditions(self) -> list[tuple[str, frozenset]]:
        if not self.also_if:
            return []
        k, v = self.also_if
        return [(k, frozenset(v if isinstance(v, (tuple, list, set, frozenset)) else (v,)))]

    @property
    def also_attr(self) -> str:
        return ";".join(f"{k}={'|'.join(sorted(v))}" for k, v in self.also_conditions())


SECTIONS = [
    ("quick", "Quick screen"),
    ("ownership", "Ownership & decision"),
    ("profile", "Business profile"),
    ("premises", "Location & premises"),
    ("asset", "Physical asset"),
    ("lease", "Lease & landlord"),
    ("customers", "Customers & demand"),
    ("operations", "Operating summary"),
    ("staff", "Staff & operations"),
    ("licences", "Licences & registrations"),
    ("liabilities", "Liabilities & obligations"),
    ("reason", "Reason & urgency"),
    ("commercial", "Commercial offer"),
]

F = FieldDef
# What the owner wants (sale_purpose) decides whether sale questions are asked at all
SELLING_PURPOSES = ("sell_entire", "partnership", "franchise_transfer")
SELLING = ("sale_purpose", SELLING_PURPOSES)
# trading figures (sales, bills, salaries…) are asked unless the salon is said to be not operating
TRADING = ("operating_status", ("operating", ""))
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
      help="A distressed owner seeking only help must not be treated as a confirmed seller. Sale questions "
           "(price, timeline, reason, terms) appear only once they want to sell."),
    F("asking_price", "Approximate asking price", "quick", "inr", quick=True, matching=True, material=True,
      help="Net consideration the seller expects. Deposit, stock and fees are captured separately.", show_if=SELLING),
    F("sale_timeline", "Sale timeline", "quick", "enum",
      [("immediate", "Immediately"), ("within_3m", "Within 3 months"), ("3_6m", "3–6 months"),
       ("6_12m", "6–12 months"), ("no_rush", "No fixed timeline")], quick=True, matching=True, show_if=SELLING),
    F("preferred_callback", "Preferred callback time", "quick", "text", quick=True),

    F("legal_name", "Legal / registered business name", "ownership", "text"),
    F("signatory_name", "Who signs the sale?", "ownership", "text", show_if=SELLING),
    F("owners_count", "Number of owners / partners", "ownership", "int"),
    F("outlets_count", "Outlets operated by this owner", "ownership", "int"),
    F("transfer_authority", "Respondent has authority to transfer?", "ownership", "enum", YES_NO_UNKNOWN,
      matching=True, material=True, show_if=SELLING),
    F("existing_broker", "Already listed with another broker?", "ownership", "enum", YES_NO_UNKNOWN,
      show_if=SELLING),

    F("operating_years", "Years in operation", "profile", "int", matching=True, unit="years"),
    F("started_year", "Year the salon started", "profile", "int", help="e.g. 2018. Used with years in operation."),
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
    F("maps_link", "Google Maps location link", "profile", "text", privacy="restricted",
      help="Exact location — restricted like the address. Ask the owner to share their location on WhatsApp."),

    F("floor_level", "Floor", "premises", "enum", FLOOR_LEVELS, matching=True, material=True),
    F("area_type", "Type of area", "premises", "enum", AREA_TYPES, matching=True),
    F("frontage_ft", "Shop front width", "premises", "int", unit="ft",
      help="Visibility from the road matters to most buyers."),
    F("parking", "Parking for customers", "premises", "enum", PARKING, matching=True),
    F("signage_visible", "Signboard visible from the main road?", "premises", "enum", YES_NO_UNKNOWN),
    F("washrooms", "Washrooms inside the salon", "premises", "int"),
    F("ac_type", "Air conditioning", "premises", "enum",
      [("central", "Central AC"), ("split", "Split / window ACs"), ("none", "No AC")]),

    F("carpet_area_sqft", "Carpet area", "asset", "int", matching=True, unit="sq ft"),
    F("chairs", "Chairs / styling stations", "asset", "int", matching=True, material=True),
    F("wash_stations", "Wash / shampoo stations", "asset", "int", matching=True),
    F("treatment_rooms", "Treatment rooms", "asset", "int", matching=True),
    F("equipment_condition", "Equipment condition", "asset", "enum",
      [("excellent", "Excellent"), ("good", "Good"), ("fair", "Fair"), ("poor", "Poor")], matching=True),
    F("equipment_age_years", "Age of the main equipment", "asset", "int", unit="years"),
    F("inclusions_note", "Included / excluded assets", "asset", "textarea"),

    F("monthly_rent", "Monthly rent", "lease", "inr", matching=True, material=True),
    F("maintenance_monthly", "Monthly maintenance (CAM) charges", "lease", "inr", matching=True,
      help="Paid to the building / landlord on top of rent. Buyers compare rent + maintenance."),
    F("gst_on_rent", "Is GST charged on the rent?", "lease", "enum", YES_NO_UNKNOWN),
    F("rent_deposit", "Rent advance / security deposit", "lease", "inr", matching=True, material=True),
    F("deposit_treatment", "How is the deposit handled in the sale?", "lease", "enum",
      [("included_in_asking", "Included in asking price"), ("buyer_pays_separately", "Buyer pays separately"),
       ("not_applicable", "No deposit")], matching=True, material=True,
      help="Drives the all-in buyer cash requirement. Unknown keeps the total incomplete.", show_if=SELLING),
    F("lease_expiry", "Lease expiry date", "lease", "date", matching=True, material=True),
    F("lease_renewal", "Renewal possible?", "lease", "enum",
      [("yes", "Yes"), ("no", "No"), ("negotiable", "Negotiable")], matching=True),
    F("lease_transfer_status", "Lease transferability", "lease", "enum",
      [("unchecked", "Unchecked"), ("landlord_pending", "Landlord discussion pending"),
       ("confirmed_transferable", "Confirmed transferable"), ("not_permitted", "Not permitted")],
      matching=True, material=True, show_if=SELLING),
    F("rent_escalation_pct", "Annual rent escalation", "lease", "pct", unit="%"),
    F("rent_arrears", "Rent arrears", "lease", "inr", matching=True),

    F("avg_monthly_bills", "Average monthly bills / visits", "customers", "int", matching=True, show_if=TRADING),
    F("avg_ticket", "Average ticket size", "customers", "inr", show_if=TRADING),
    F("repeat_customer_pct", "Repeat customers", "customers", "int", unit="%", show_if=TRADING),
    F("prepaid_liabilities", "Outstanding memberships / prepaid services", "customers", "inr", matching=True),
    F("top_services", "Main services (what customers come for)", "customers", "text"),
    F("google_rating", "Google rating and number of reviews", "customers", "text", help="e.g. 4.5 (320 reviews)"),
    F("booking_software", "Billing / booking software used", "customers", "text"),

    F("avg_monthly_sales", "Average monthly sales (declared)", "operations", "inr", matching=True, material=True,
      help="Summary claim. Month-by-month figures and evidence go in Financials.", show_if=TRADING),
    F("avg_monthly_expenses", "Average monthly operating expenses (declared)", "operations", "inr",
      matching=True, material=True, show_if=TRADING),
    F("electricity_monthly", "Average monthly electricity bill", "operations", "inr"),
    F("monthly_salaries", "Total staff salaries per month", "operations", "inr", show_if=TRADING),

    F("staff_headcount", "Total staff", "staff", "int", matching=True),
    F("key_staff_dependence", "Dependence on key staff", "staff", "enum",
      [("high", "High"), ("medium", "Medium"), ("low", "Low")], matching=True, show_if=TRADING),
    F("owner_involvement", "Owner involvement", "staff", "enum",
      [("full_time", "Full-time"), ("part_time", "Part-time"), ("absentee", "Absentee")], matching=True, show_if=TRADING),
    F("staff_continuity", "Staff expected to stay after sale?", "staff", "enum",
      [("yes", "Yes"), ("partial", "Partially"), ("no", "No")], matching=True, show_if=SELLING, also_if=TRADING),

    F("trade_licence", "Trade licence", "licences", "enum", LICENCE),
    F("gst_registered", "GST registration", "licences", "enum", LICENCE),
    F("shop_establishment", "Shop & establishment registration", "licences", "enum", LICENCE),

    F("outstanding_loans", "Outstanding business loans", "liabilities", "inr", matching=True, material=True),
    F("supplier_dues", "Supplier dues", "liabilities", "inr", matching=True),
    F("employee_dues", "Employee dues", "liabilities", "inr", matching=True),
    F("litigation", "Pending disputes / litigation?", "liabilities", "enum", YES_NO_UNKNOWN,
      matching=True, material=True),

    F("sale_reason", "Reason for sale (category)", "reason", "enum",
      [("relocation", "Relocation"), ("new_venture", "New venture"), ("partnership_change", "Partnership change"),
       ("retirement", "Retirement"), ("financial_pressure", "Financial pressure"), ("personal", "Personal"),
       ("other", "Other")], show_if=SELLING),
    F("sale_reason_note", "Private context (only if volunteered)", "reason", "textarea", privacy="restricted",
      help="Optional. Visible only to the primary owner, approvers and explicit grantees.", show_if=SELLING),
    F("desired_exit_date", "Desired exit date", "reason", "date", matching=True, show_if=SELLING),
    F("transition_support", "Will the owner support transition?", "reason", "enum",
      [("yes", "Yes"), ("no", "No"), ("negotiable", "Negotiable")], matching=True, show_if=SELLING),

    F("negotiable", "Is the price negotiable?", "commercial", "enum", YES_NO_UNKNOWN, matching=True, show_if=SELLING),
    F("negotiable_min_price", "Lowest acceptable price (if volunteered)", "commercial", "inr",
      privacy="restricted", show_if=("negotiable", "yes"),
      help="Never shown to buyers. Used internally to identify near matches."),
    F("sale_structure", "Sale structure", "commercial", "enum",
      [("entity", "Entire business / entity"), ("asset", "Asset sale"), ("partial_stake", "Partial stake"),
       ("management", "Management agreement"), ("franchise_transfer", "Franchise transfer")],
      matching=True, material=True, show_if=SELLING),
    F("stock_value_separate", "Stock / inventory payable separately", "commercial", "inr", matching=True,
      material=True, help="Use 'Not applicable' if stock is included in the asking price.", show_if=SELLING),
    F("franchise_transfer_fee", "Franchise transfer fee", "commercial", "inr", matching=True, material=True,
      show_if=("business_model", "franchise")),
    F("working_capital_estimate", "Working capital / handover cost estimate", "commercial", "inr", matching=True, show_if=SELLING),
    F("expected_handover_weeks", "Expected handover period", "commercial", "int", unit="weeks", show_if=SELLING),
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


def is_applicable(fd: FieldDef, rec: dict, defs: dict | None = None) -> bool:
    """Should this question be asked for this record? Any one of its conditions is enough, and the question it
    depends on must itself be asked (e.g. 'lowest price' needs 'negotiable = yes', which needs a sale)."""
    defs = BY_KEY if defs is None else defs

    def holds(k, vals):
        parent = defs.get(k)
        return (rec.get(k) or "") in vals and (parent is None or is_applicable(parent, rec, defs))
    if any(not holds(k, v) for k, v in fd.also_conditions()):
        return False
    conds = fd.conditions()
    return not conds or any(holds(k, v) for k, v in conds)


def drop_not_applicable(values: dict, current: dict, defs: dict | None = None, gate_keys=None) -> dict:
    """Answers for questions that do not apply are not saved — but only when what decides it has actually been
    answered (e.g. the owner said 'seeking help'). If the deciding answer is still blank, the value is kept. Nothing
    already stored is erased."""
    defs = BY_KEY if defs is None else defs
    merged = {**current, **{k: v[0] if isinstance(v, tuple) else v for k, v in values.items()}}
    out = {}
    for k, v in values.items():
        fd = defs.get(k)
        deciders = [c for c, _v in (fd.conditions() + fd.also_conditions())] if fd else []
        if fd and any(merged.get(c) not in (None, "") for c in deciders) and not is_applicable(fd, merged, defs):
            continue
        out[k] = v
    return out


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
