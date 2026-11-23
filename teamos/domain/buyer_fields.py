"""Buyer / investor mandate dictionary (PRD §7.2, §7.2A).

Single source of truth for buyer requirement capture: form rendering, validation, provenance,
completeness and (from Milestone 5) matching inputs. Option values deliberately reuse the seller
vocabulary (format, model, positioning, sale structure) so the Match Desk compares like with like.

Two kinds of preference are represented:
* Scalar criteria (e.g. minimum chairs) — a value in buyer_mandates + an importance row with choice ''.
* Multi-choice dimensions (e.g. format) — one importance row per option (Must / Prefer / Flexible /
  Avoid / Not important). No row = blank, which is NOT the same as "not important".
"""
from __future__ import annotations

from dataclasses import dataclass

from .seller_fields import BY_KEY as SELLER_FIELDS
from .seller_fields import FieldDef

YES_NO = [("yes", "Yes"), ("no", "No")]

IMPORTANCE = [
    ("must", "Must have"),
    ("prefer", "Prefer"),
    ("flexible", "Flexible"),
    ("avoid", "Avoid"),
    ("not_important", "Not important"),
]
IMPORTANCE_LABELS = dict(IMPORTANCE)

NEED_TYPES = [
    ("acquire_operating", "Buy an operating salon"),
    ("franchise", "Acquire a franchise outlet"),
    ("partnership", "Partnership / part stake"),
    ("passive_invest", "Invest passively"),
    ("new_salon", "Open a NEW salon (not a resale)"),
    ("exploring", "Exploring options"),
]
# Need types that are acquisitions of an existing salon and may later be matched to listings.
RESALE_NEEDS = {"acquire_operating", "franchise", "partnership", "passive_invest"}


@dataclass(frozen=True)
class BuyerFieldDef(FieldDef):
    importance: bool = False        # buyer states how much this criterion matters


SECTIONS = [
    ("intent", "Identity & intent"),
    ("budget", "Budget & all-in ceiling"),
    ("funding", "Funding (as declared)"),
    ("geography", "Geography"),
    ("salon", "Salon & scale"),
    ("business", "Business, lease & risk"),
    ("timing", "Timing & involvement"),
    ("authority", "Authority & constraints"),
]

B = BuyerFieldDef
FIELDS: list[BuyerFieldDef] = [
    B("need_type", "What does the buyer want?", "intent", "enum", NEED_TYPES, quick=True, matching=True,
      material=True, help="'Open a new salon' is a separate need: never matched to resale listings without the "
                          "buyer's explicit confirmation."),
    B("investor_type", "Buyer type", "intent", "enum",
      [("individual", "Individual"), ("partnership", "Partnership"), ("company", "Company"),
       ("investor_group", "Investor group")]),
    B("decision_maker", "Is the respondent the decision maker?", "intent", "enum",
      [("yes", "Yes — sole decision maker"), ("shared", "Shared with partners/family"), ("no", "No")],
      quick=True, material=True),
    B("salon_background", "Salon / beauty-industry background", "intent", "enum",
      [("operator", "Has run a salon"), ("industry_other", "Industry, not as owner"), ("none", "None")],
      matching=True),

    B("all_in_max", "Hard maximum ALL-IN cash outlay", "budget", "inr", quick=True, matching=True, material=True,
      help="Total cash the buyer can put in: price + deposit + stock + working capital + transfer fees. "
           "This is a hard ceiling for matching."),
    B("budget_min", "Acquisition price — from", "budget", "inr", matching=True),
    B("budget_max", "Acquisition price — up to", "budget", "inr", matching=True, material=True),
    B("includes_deposit", "Does the ceiling already cover a rent deposit?", "budget", "enum", YES_NO, matching=True),
    B("includes_stock", "…cover stock / inventory?", "budget", "enum", YES_NO, matching=True),
    B("includes_working_capital", "…cover working capital?", "budget", "enum", YES_NO, matching=True),
    B("finance_dependence", "Depends on a loan or outside finance?", "budget", "enum",
      [("none", "No — own funds"), ("partial", "Partly"), ("full", "Fully")], matching=True, material=True),

    B("funding_source", "Source of funds", "funding", "enum",
      [("self", "Self-funded"), ("loan", "Loan"), ("investor_group", "Investor group"), ("mixed", "Mixed")],
      matching=True),
    B("cash_ready", "When is the money available?", "funding", "enum",
      [("now", "Ready now"), ("within_30d", "Within 30 days"), ("planned", "Planned / not yet arranged")],
      matching=True, material=True),
    B("cash_ready_amount", "Amount ready now", "funding", "inr", matching=True),

    B("preferred_city", "Preferred city", "geography", "text", quick=True, matching=True, material=True,
      help="Ranked localities and exclusions are captured below."),
    B("relocate_willing", "Willing to relocate / operate in another city?", "geography", "enum", YES_NO,
      matching=True),
    B("max_commute_km", "Maximum acceptable commute", "geography", "int", matching=True, unit="km"),

    B("chairs_min", "Minimum chairs / stations", "salon", "int", matching=True, material=True, importance=True),
    B("chairs_max", "Maximum chairs / stations", "salon", "int", matching=True),
    B("area_min_sqft", "Minimum carpet area", "salon", "int", matching=True, unit="sq ft", importance=True),
    B("parking_need", "Customer parking needed?", "salon", "enum",
      [("car", "Car parking needed"), ("bike", "Two-wheeler parking is enough"), ("not_needed", "Not needed")],
      matching=True, importance=True),
    B("staff_continuity_need", "Existing staff should stay?", "salon", "enum",
      [("yes", "Yes"), ("partial", "Key staff at least"), ("no", "Not needed")], matching=True, importance=True),
    B("clientele_note", "Target clientele / service strengths", "salon", "textarea"),

    B("min_monthly_sales", "Minimum monthly sales", "business", "inr", matching=True, importance=True),
    B("min_operating_years", "Minimum years in operation", "business", "int", matching=True, unit="years",
      importance=True),
    B("turnaround_ok", "Open to a turnaround / loss-making salon?", "business", "enum", YES_NO, matching=True),
    B("max_monthly_rent", "Maximum monthly rent + maintenance", "business", "inr", matching=True, importance=True,
      help="Compared with the salon's rent plus maintenance (CAM) charges."),
    B("min_lease_years_left", "Minimum lease remaining", "business", "int", matching=True, unit="years",
      importance=True),
    B("renovation_budget", "Budget for renovation / refit", "business", "inr", matching=True),
    B("liability_tolerance", "Tolerance for existing liabilities", "business", "enum",
      [("none", "None"), ("low", "Low"), ("moderate", "Moderate")], matching=True),

    B("purchase_window", "Wants to buy within", "timing", "enum",
      [("30d", "30 days"), ("90d", "90 days"), ("180d", "180 days"), ("later", "Later / no fixed time")],
      quick=True, matching=True, material=True),
    B("involvement", "Operating involvement", "timing", "enum",
      [("owner_operator", "Owner-operator"), ("partly", "Partly involved"), ("absentee", "Absentee investor")],
      matching=True),
    B("visit_availability", "Available for site visits?", "timing", "enum",
      [("yes", "Yes, weekdays"), ("weekends", "Weekends only"), ("no", "Not yet")], matching=True),
    B("transition_support_need", "Needs seller transition support?", "timing", "enum",
      [("yes", "Yes"), ("nice", "Nice to have"), ("no", "No")], matching=True, importance=True),

    B("payment_approver", "Who approves the payment?", "authority", "text", material=True),
    B("other_partners", "Other partners / family involved", "authority", "text"),
    B("decision_timeline", "How long will a decision take?", "authority", "text"),
    B("must_haves", "Must-haves (in the buyer's words)", "authority", "textarea", matching=True),
    B("deal_breakers", "Deal-breakers (in the buyer's words)", "authority", "textarea", matching=True),
]

BY_KEY = {f.key: f for f in FIELDS}
MATERIAL_KEYS = {f.key for f in FIELDS if f.material}
IMPORTANCE_KEYS = [f.key for f in FIELDS if f.importance]

# Multi-choice preference dimensions: options reuse the seller dictionary so matching compares like with like.
CHOICE_DIMENSIONS = [
    ("gender_format", "Salon format", SELLER_FIELDS["gender_format"].options),
    ("business_model", "Independent or franchise", SELLER_FIELDS["business_model"].options),
    ("positioning", "Positioning", SELLER_FIELDS["positioning"].options),
    ("sale_structure", "Deal structure", SELLER_FIELDS["sale_structure"].options),
    ("floor_level", "Floor", SELLER_FIELDS["floor_level"].options),
    ("area_type", "Type of area", SELLER_FIELDS["area_type"].options),
]
CHOICE_BY_KEY = {k: (label, opts) for k, label, opts in CHOICE_DIMENSIONS}

# Fields a buyer or a staff data-entry form can NEVER set (PRD §7.2: "Never allow the buyer to set private
# admin fields"). They are written only through the reviewer actions in domain/buyers.py.
PRIVILEGED_KEYS = {
    "funding_status", "funding_verified", "funding_note", "funding_reviewed_by", "funding_reviewed_at",
    "criteria_reviewed_version", "criteria_reviewed_by", "criteria_reviewed_at",
    "qualified_by_salon_shastra", "introduction_authorised", "stage", "primary_owner_id", "owner_id",
}

FUNDING_STATUSES = [
    ("not_reviewed", "Not reviewed"),
    ("evidence_requested", "Evidence requested"),
    ("staff_reviewed", "Staff-reviewed"),
    ("verified", "Verified"),
    ("insufficient", "Insufficient / not credible"),
]
FUNDING_LABELS = dict(FUNDING_STATUSES)


def fields_in(section: str) -> list[BuyerFieldDef]:
    return [f for f in FIELDS if f.section == section]


def option_label(key: str, value) -> str:
    fd = BY_KEY.get(key)
    if fd and fd.options:
        return dict(fd.options).get(value, str(value))
    if key in CHOICE_BY_KEY:
        return dict(CHOICE_BY_KEY[key][1]).get(value, str(value))
    return str(value)
