"""Salon Owners pilot program.

Flow (founder's process, 24 Nov 2026):
  ad → team calls → marks lead type (salon owner / investor) and ticks the help areas → sends the pilot form link →
  form Filled / Not filled → SWOT report drafted from the answers → staff edit + approve → PDF sent with a message →
  owner books a call with Vikas Rachakonda (he is alerted) → team calls back → paid consultation (office ₹3,000 /
  salon visit ₹5,000 in Hyderabad; ₹10,000 outside) → free video by Vikas as a bonus.

The 45 questions keep the exact wording and choices of the Google Form "Salon Shastra Pilot Program"; a few extra
questions (marked extra=True) give the SWOT report more to work with.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
from datetime import timedelta

from .. import audit
from ..permissions import user_can
from ..timeutil import IST, fmt_ist, now_iso, now_utc, parse_iso, to_iso
from . import tasks as T
from .errors import PermissionDenied, ValidationError

# ---------------------------------------------------------------------------------------------------------------
# Help areas the team ticks on the first call
# Names follow salonshastra.com/services so the team uses the same words as the website.
HELP_AREAS = [
    ("business_consulting", "Business audit & consulting"),
    ("digital_marketing", "Marketing & online growth"),
    ("operations", "Operations (SOPs, KPIs, reports)"),
    ("staff_hiring", "Staff hiring"),
    ("staff_training", "Staff training"),
    ("chairpe", "ChairPe software"),
    ("new_salon_setup", "New salon setup"),
    ("vikas_consult", "Talk to Vikas Rachakonda"),
    ("sell_salon", "Wants to sell the salon"),
]
HELP_LABELS = dict(HELP_AREAS)
LEAD_TYPES = [("salon_owner", "Existing salon"), ("new_salon", "Opening a new salon"), ("investor", "Investor")]

# ChairPe: Salon Shastra's salon operating system (chairpe.com, read 24 Nov 2026). Every new salon gets Elite free for
# 14 days, no card; then pay for 1, 3, 6 or 12 months (longer is cheaper; Elite yearly works out to about Rs 1,000/month).
CHAIRPE_URL = "https://www.chairpe.com"
CHAIRPE_TRIAL_DAYS = 14
CHAIRPE_PLANS = [
    ("basic", "Basic", 599, "New or single-chair salon getting off paper: billing, customers, daily sales"),
    ("standard", "Standard", 1199, "Busy salon with staff and stock: appointments, inventory, memberships, WhatsApp bills"),
    ("elite", "Elite", 1499, "Most popular: + commissions, ChairPe Core chair profit, client recovery, expert consultation"),
    ("multi_branch", "Elite Multi-branch", 2499, "2+ branches, one login (2 branches; each extra branch costs less)"),
]
CHAIRPE_PLAN_LABELS = {k: f"{n} · ₹{p:,}/month" for k, n, p, _ in CHAIRPE_PLANS}
CHAIRPE_STATUS = [("not_offered", "Not offered yet"), ("demo", "Demo shown"), ("trial", "On 14-day free trial"),
                  ("paying", "Paying customer"), ("not_interested", "Not interested")]
CHAIRPE_STATUS_LABELS = dict(CHAIRPE_STATUS)
CHAIRPE_MONTHS = [1, 3, 6, 12]
FORM_STATUS = [("not_sent", "Form not sent"), ("sent", "Form sent"), ("filled", "Form filled"),
               ("not_filled", "Not filled")]
FORM_STATUS_LABELS = dict(FORM_STATUS)

# Consultation fees (₹) — founder, 24 Nov 2026
FEES = {("office", 1): 3000, ("salon_visit", 1): 5000, ("office", 0): 10000, ("salon_visit", 0): 10000}
CONSULT_KINDS = [("office", "Meeting at our office"), ("salon_visit", "Visit to their salon")]
VIDEO_STATUS = [("pending", "To do"), ("shot", "Shot"), ("posted", "Posted"), ("declined", "Owner declined")]
PAY_MODES = [("upi", "UPI"), ("cash", "Cash"), ("bank", "Bank transfer"), ("card", "Card")]
LINK_DAYS = 90
CONSULTANT_NAME = "Vikas Rachakonda"
CONSULTANT_SETTING = "pilot_consultant_user_id"

YN = ["Yes", "No"]
SALON_TYPES = ["Unisex Salon", "Family Salon", "High End Barber Salon(HEBS)",
               "Stand Alone (You are the Hero of your Business)", "Mens Salon", "Luxury Beauty Salon", "Beauty Parlour"]
FLOORS = ["Ground Floor", "1st floor", "2nd Floor", "3rd Floor", "4th floor", "5 th Floor"]
STAND_OUT = ["Unique services/treatments", "Highly skilled staff", "Strong brand presence & reputation",
             "High client retention rate", "Efficient business operations & technology (ERP, automation)"]
CHALLENGES = ["Inefficient appointment management", "Staff turnover & inconsistent service",
              "Low client retention & satisfaction", "Limited digital marketing or online presence",
              "Financial issues (pricing, cash flow, profitability)"]
GROWTH = ["New customer segments (bridal, men’s grooming, luxury)",
          "Expanding services (new treatments, high-end offerings)",
          "Retailing beauty products & upselling services",
          "Partnering with brands, influencers, or academies",
          "Improving digital marketing & social media presence"]
THREATS = ["New competitors in the market", "Changing customer preferences & industry trends",
           "Rising costs (rent, staff salaries, products)", "Economic downturn affecting customer spending",
           "Government regulations & compliance issues"]


def _q(key, label, kind="text", options=None, required=True, other=False, extra=False, hint=None):
    return {"key": key, "label": label, "kind": kind, "options": options or [], "required": required,
            "other": other, "extra": extra, "hint": hint}


SECTIONS = [
    ("basics", "Your salon", "", [
        _q("salon_name", "Salon Name"),
        _q("owner_name", "Owner Name"),
        _q("location", "Location"),
        _q("salon_type", "Salon Type", "choice", SALON_TYPES),
        _q("contact_number", "Contact Number", "phone"),
        _q("email", "Email", "email"),
    ]),
    ("about", "About the Salon", "This Section helps us understand your Salon Strength", [
        _q("area_sft", "What is the total area of the Salon in SFT", "number"),
        _q("floor", "Which floor is your salon located", "choice", FLOORS, other=True),
        _q("chairs", "No. of Chairs", "number"),
        _q("beds", "No. of Beds", "number"),
        _q("pedicure_stations", "No. of Pedicure Stations", "number"),
        _q("hair_dressers", "No. of Hair dressers", "number"),
        _q("beauticians", "No. of Beauticians", "number"),
        _q("manager", "Manager", "choice", YN),
        _q("housekeeping", "House keeping", "choice", YN),
        _q("years_running", "How many years has your salon been running?", "number", extra=True),
    ]),
    ("finance", "About the Financial Challenges", "this Section helps us understand you financial challenges", [
        _q("monthly_revenue", "What is the Current Monthly Revenue", "money", required=False),
        _q("monthly_expenses", "What are your Monthly expenses", "money", required=False),
        _q("monthly_rent", "What is the Monthly Rent of your Salon", "money"),
        _q("price_women_cut", "What is the price for  Woman Hair Cut", required=False),
        _q("price_men_cut", "What is the Price for Men's Hair cut", required=False),
        _q("digital_marketing_spend", "Are you Spending on Digital Marketing", "choice", YN),
        _q("billing_software", "Do u Use any BILLING SOFTWARE", "choice", YN),
        _q("monthly_customers", "About how many customers visit in a month?", "number", required=False, extra=True),
    ]),
    ("strengths", "Your Salon Strength", "", [
        _q("stand_out", "What makes your salon stand out? (Select all that apply)", "multi", STAND_OUT,
           required=False, other=True),
        _q("service_rating", "How do you rate your salon’s service quality?", "scale5"),
        _q("customers_love", "What do your customers love the most about your salon?", "long"),
        _q("training_program", "Do you have a structured training program for staff? If yes, please describe.",
           "long"),
        _q("marketing_worked", "What marketing strategies have worked best for you so far?", "long"),
    ]),
    ("weaknesses", "Weaknesses", "Challenges You Face", [
        _q("staff_struggle", "Do you struggle with hiring and retaining skilled staff? (Yes/No) If yes, why? "
           "(Short answer)", "long"),
        _q("op_challenges", "What are your biggest operational challenges? (Select all that apply)", "multi",
           CHALLENGES, other=True),
        _q("online_presence", "How strong is your salon’s online presence? (Scale: 1-10)", "scale10"),
        _q("track_financials", "Do you track financial metrics (revenue, profit, cost per service)?", "choice", YN),
        _q("complaints", "What customer complaints do you receive most often?", "long"),
        _q("google_rating", "Your Google rating (if you know it)", required=False, extra=True,
           hint="For example 4.3"),
        _q("instagram", "Instagram page or Google listing link", required=False, extra=True),
    ]),
    ("opportunities", "Opportunities", "Growth Potential & Market Trends", [
        _q("growth_opps", "What are the biggest growth opportunities for your salon? (Select all that apply)",
           "multi", GROWTH, other=True),
        _q("extra_revenue", "Are you currently exploring any additional revenue streams? (Yes/No) If yes, which "
           "ones? (Short answer)", "choice", YN, other=True),
        _q("digital_ads", "Do you use digital ads (Facebook, Instagram, Google) to attract new clients? (Yes/No)",
           "choice", YN),
        _q("loyalty", "Have you considered salon memberships or loyalty programs? (Yes/No)", "choice", YN),
        _q("growth_strategies", "What strategies do you think can help you grow your revenue? (Short answer)",
           "long"),
    ]),
    ("threats", "Threats", "External Risks & Competition Challenges", [
        _q("threats", "What are the biggest threats to your salon’s success? (Select all that apply)", "multi",
           THREATS, other=True),
        _q("price_competition", "Do you face challenges in retaining customers due to pricing competition? "
           "(Yes/No)", "choice", YN),
        _q("revenue_fluctuation", "Have you experienced revenue fluctuations in the last 6 months? (Yes/No)",
           "choice", YN),
        _q("external_factors", "What external factors (economic, legal, competition) have affected your salon’s "
           "growth? (Short answer)", "long"),
    ]),
    ("next", "Final Section: Next Steps", "", [
        _q("want_report", "Would you like a personalized business report based on your responses? (Yes/No)",
           "choice", YN),
        _q("pilot_interest", "Are you interested in Salon Shastra’s Pilot Program for salon growth? (Yes/No)",
           "choice", YN),
        _q("willing_invest", "Are you willing to spend time and money towards your business growth?", "choice", YN,
           required=False, other=True),
        _q("goal_12m", "What is the one thing you most want to achieve in the next 12 months?", "long",
           required=False, extra=True),
        _q("comments", "Any additional comments or concerns? (Short answer)", "long"),
    ]),
]
QUESTIONS = [q for _, _, _, qs in SECTIONS for q in qs]
Q_BY_KEY = {q["key"]: q for q in QUESTIONS}
ORIGINAL_COUNT = sum(1 for q in QUESTIONS if not q["extra"])  # 45 — same as the Google Form


# ---------------------------------------------------------------------------------------------------------------
def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _need(db, actor, cap):
    if not user_can(db, actor, cap):
        raise PermissionDenied(cap)


def profile(db, opp_id: int) -> dict:
    row = db.one("SELECT * FROM pilot_profiles WHERE opportunity_id = ?", (opp_id,))
    if not row:
        return {"opportunity_id": opp_id, "lead_type": "salon_owner", "help_areas": [], "help_other": None,
                "problem_note": None, "form_status": "not_sent", "form_sent_at": None, "form_filled_at": None,
                "chairpe_status": "not_offered", "chairpe_plan": None, "chairpe_months": None,
                "chairpe_trial_start": None, "chairpe_paid_on": None, "chairpe_note": None, "exists": False}
    row = dict(row)
    try:
        row["help_areas"] = [h for h in json.loads(row["help_areas"] or "[]") if h in HELP_LABELS]
    except ValueError:
        row["help_areas"] = []
    row["exists"] = True
    return row


def _upsert(db, opp_id: int, actor_id, values: dict) -> None:
    values = dict(values, updated_by=actor_id, updated_at=now_iso())
    if db.scalar("SELECT id FROM pilot_profiles WHERE opportunity_id = ?", (opp_id,)):
        sets = ", ".join(f"{k} = ?" for k in values)
        db.execute(f"UPDATE pilot_profiles SET {sets} WHERE opportunity_id = ?", (*values.values(), opp_id))
    else:
        db.insert("pilot_profiles", {"opportunity_id": opp_id, **values})


def save_needs(db, actor, opp, data) -> None:
    """Lead type, help areas and the problem in the team's words (first call)."""
    _need(db, actor, "crm.edit")
    lead_type = data.get("lead_type") or "salon_owner"
    if lead_type not in dict(LEAD_TYPES):
        raise ValidationError({"lead_type": "Choose existing salon, new salon or investor."})
    areas = [a for a in (data.getlist("help_areas") if hasattr(data, "getlist") else data.get("help_areas") or [])
             if a in HELP_LABELS]
    other = (data.get("help_other") or "").strip()[:300] or None
    note = (data.get("problem_note") or "").strip()[:2000] or None
    with db:
        _upsert(db, opp["id"], actor["id"], {"lead_type": lead_type, "help_areas": json.dumps(areas),
                                             "help_other": other, "problem_note": note})
        names = ", ".join(HELP_LABELS[a] for a in areas) or "none ticked"
        T.add_activity(db, actor["id"], "opportunity", opp["id"], "data_update",
                       f"Needs recorded — {dict(LEAD_TYPES)[lead_type]}; help with: {names}"
                       + (f"; other: {other}" if other else ""))
        audit.record(db, actor["id"], "pilot.needs", "opportunity", opp["id"], {"lead_type": lead_type,
                                                                               "areas": areas})


def set_form_status(db, actor, opp, status: str) -> None:
    _need(db, actor, "crm.log_activity")
    if status not in FORM_STATUS_LABELS:
        raise ValidationError({"status": "Unknown form status."})
    vals = {"form_status": status}
    if status == "sent":
        vals["form_sent_at"] = now_iso()
    if status == "filled":
        vals["form_filled_at"] = now_iso()
    with db:
        _upsert(db, opp["id"], actor["id"], vals)
        T.add_activity(db, actor["id"], "opportunity", opp["id"], "data_update",
                       f"Pilot form marked: {FORM_STATUS_LABELS[status]}")


# -- Private links ------------------------------------------------------------------------------------------------
def create_link(db, actor, opp) -> str:
    """A private link for this salon owner: pilot form, report download and booking with Vikas. Returns the secret
    once. Several links can be live at once (each message carries its own); each expires after 90 days."""
    _need(db, actor, "crm.log_activity")
    if opp["status"] == "closed" or opp.get("do_not_contact") or opp["stage"] == "do_not_contact":
        raise ValidationError({"link": "This record is closed or marked do-not-contact, so no link can be shared."})
    token = secrets.token_urlsafe(18)
    now = now_utc()
    db.insert("pilot_links", {"opportunity_id": opp["id"], "token_hash": _hash(token), "created_by": actor["id"],
                              "created_at": to_iso(now), "expires_at": to_iso(now + timedelta(days=LINK_DAYS))})
    audit.record(db, actor["id"], "pilot.link_created", "opportunity", opp["id"], {})
    return token


def revoke_links(db, actor, opp) -> None:
    _need(db, actor, "crm.log_activity")
    db.execute("UPDATE pilot_links SET revoked_at = ? WHERE opportunity_id = ? AND revoked_at IS NULL",
               (now_iso(), opp["id"]))
    audit.record(db, actor["id"], "pilot.links_revoked", "opportunity", opp["id"], {})


def resolve(db, token: str) -> dict | None:
    """The salon-owner record behind a live link, or None (expired, switched off, closed, do-not-contact)."""
    if not token or len(token) > 100 or not re.fullmatch(r"[A-Za-z0-9_\-]+", token):
        return None
    link = db.one("SELECT * FROM pilot_links WHERE token_hash = ?", (_hash(token),))
    if not link or link["revoked_at"] or link["expires_at"] <= now_iso():
        return None
    opp = db.one("SELECT o.*, c.full_name AS contact_name, c.phone_normalized, c.email AS contact_email, "
                 "c.do_not_contact, b.name AS salon_name, b.locality, b.city FROM opportunities o "
                 "JOIN contacts c ON c.id = o.contact_id LEFT JOIN salon_businesses b ON b.id = o.business_id "
                 "WHERE o.id = ?", (link["opportunity_id"],))
    if not opp or opp["status"] == "closed" or opp["do_not_contact"]:
        return None
    db.execute("UPDATE pilot_links SET last_opened_at = ? WHERE id = ?", (now_iso(), link["id"]))
    return opp


# -- Answers ------------------------------------------------------------------------------------------------------
def latest_response(db, opp_id: int) -> dict | None:
    row = db.one("SELECT * FROM pilot_responses WHERE opportunity_id = ? ORDER BY id DESC LIMIT 1", (opp_id,))
    if not row:
        return None
    row = dict(row)
    row["answers"] = json.loads(row["answers"])
    return row


def prefill(db, opp: dict) -> dict:
    """What we already know, so the owner doesn't type it twice."""
    last = latest_response(db, opp["id"])
    if last:
        return last["answers"]
    phone = (opp.get("phone_normalized") or "")
    return {"salon_name": opp.get("salon_name") or "", "owner_name": opp.get("contact_name") or "",
            "location": ", ".join(x for x in (opp.get("locality"), opp.get("city")) if x),
            "contact_number": phone[3:] if phone.startswith("+91") else phone, "email": opp.get("contact_email") or ""}


def parse_answers(form) -> tuple[dict, dict]:
    """Form → clean answers + errors. Accepts the owner's own words for money ("40k", "1.8L")."""
    answers, errors = {}, {}
    for q in QUESTIONS:
        k = q["key"]
        if q["kind"] == "multi":
            vals = [v for v in (form.getlist(k) if hasattr(form, "getlist") else form.get(k) or [])
                    if v in q["options"]]
            other = (form.get(k + "__other") or "").strip()[:300]
            if other:
                vals.append("Other: " + other)
            if vals:
                answers[k] = vals
            elif q["required"]:
                errors[k] = "Please choose at least one."
            continue
        v = (form.get(k) or "").strip()
        if v == "__other":
            v = (form.get(k + "__other") or "").strip()
            v = ("Other: " + v) if v else ""
        v = v[:2000]
        if not v:
            if q["required"]:
                errors[k] = "Please answer this question."
            continue
        if q["kind"] == "choice" and v not in q["options"] and not (q["other"] and v.startswith("Other: ")):
            errors[k] = "Please choose one of the options."
            continue
        if q["kind"] in ("scale5", "scale10"):
            top = 5 if q["kind"] == "scale5" else 10
            if not v.isdigit() or not 1 <= int(v) <= top:
                errors[k] = f"Choose a number from 1 to {top}."
                continue
        if q["kind"] == "number" and parse_amount(v) is None:
            errors[k] = "Please enter a number."
            continue
        if q["kind"] == "phone" and len(re.sub(r"\D", "", v)) < 10:
            errors[k] = "Please enter a 10-digit mobile number."
            continue
        if q["kind"] == "email" and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", v):
            errors[k] = "Please enter a valid e-mail address."
            continue
        answers[k] = v
    return answers, errors


def save_response(db, opp: dict, answers: dict, source: str, actor_id=None) -> int:
    with db:
        rid = db.insert("pilot_responses", {"opportunity_id": opp["id"], "answers": json.dumps(answers,
                                                                                               ensure_ascii=False),
                                            "source": source, "created_by": actor_id, "submitted_at": now_iso()})
        _upsert(db, opp["id"], actor_id, {"form_status": "filled", "form_filled_at": now_iso()})
        who = "Salon owner" if source == "link" else "Team (on the owner's behalf)"
        T.add_activity(db, actor_id, "opportunity", opp["id"], "system",
                       f"{who} filled the pilot program form — SWOT report draft ready to review")
        draft_report(db, opp["id"], rid, actor_id)
        owner = opp.get("primary_owner_id")
        T.notify(db, owner, "pilot_form", f"Pilot form filled: {opp.get('salon_name') or opp.get('ref_code')} — "
                 "SWOT draft ready", f"/sellers/{opp['id']}?tab=pilot")
        audit.record(db, actor_id, "pilot.form_filled", "opportunity", opp["id"], {"source": source, "response": rid})
    return rid


# -- SWOT engine --------------------------------------------------------------------------------------------------
SWOT_VERSION = "pilot-swot rules v1 (24 Nov 2026)"


def parse_amount(v) -> float | None:
    """'40k' → 40000, '1.8L' → 180000, '2 lakhs' → 200000, '50000.0' → 50000, '1000-1200/-' → 1000."""
    if v is None:
        return None
    s = str(v).lower().replace(",", "").replace("₹", "").replace("rs", "").strip()
    m = re.search(r"(\d+(?:\.\d+)?)\s*(k|l|lakh|lakhs|lac|lacs|cr|crore)?", s)
    if not m:
        return None
    n = float(m.group(1))
    unit = m.group(2) or ""
    if unit == "k":
        n *= 1000
    elif unit.startswith("l"):
        n *= 100000
    elif unit.startswith("cr"):
        n *= 10000000
    return n


def _yes(v) -> bool:
    return str(v or "").strip().lower().startswith("y")


def _inr(n: float | None) -> str:
    if n is None:
        return "—"
    if n >= 100000:
        return f"₹{n / 100000:.1f} lakh".replace(".0 ", " ")
    return f"₹{n:,.0f}"


def _clean(text) -> str:
    t = re.sub(r"\s+", " ", str(text or "")).strip()
    return "" if t.lower() in {"", "no", "nil", "null", "na", "n/a", "nothing", "none", "not at all", "-", "."} else t


def build_swot(answers: dict, help_areas: list[str] | None = None) -> dict:
    """Rules that turn the pilot answers into a SWOT report with numbers, scores and next steps."""
    a = answers
    help_areas = help_areas or []
    rev = parse_amount(a.get("monthly_revenue"))
    exp = parse_amount(a.get("monthly_expenses"))
    rent = parse_amount(a.get("monthly_rent"))
    chairs = parse_amount(a.get("chairs")) or 0
    online = int(a["online_presence"]) if str(a.get("online_presence", "")).isdigit() else None
    rating = int(a["service_rating"]) if str(a.get("service_rating", "")).isdigit() else None
    stand = a.get("stand_out") or []
    chall = a.get("op_challenges") or []
    growth = a.get("growth_opps") or []
    threats = a.get("threats") or []
    staff_struggle = _yes(a.get("staff_struggle"))
    training = _yes(a.get("training_program")) or (len(_clean(a.get("training_program"))) > 12
                                                   and not str(a.get("training_program", "")).lower().startswith("no"))
    margin = ((rev - exp) / rev) if rev and exp is not None and rev > 0 else None
    rent_pct = (rent / rev) if rev and rent else None
    per_chair = (rev / chairs) if rev and chairs else None

    S, W, O, Th = [], [], [], []

    # Strengths
    labels = {"Unique services/treatments": "Unique services and treatments that set the salon apart",
              "Highly skilled staff": "A skilled team that customers trust",
              "Strong brand presence & reputation": "A recognised brand and good reputation",
              "High client retention rate": "Customers keep coming back (good retention)",
              "Efficient business operations & technology (ERP, automation)": "Organised operations using technology"}
    for s in stand:
        if s in labels:
            S.append(labels[s])
    if rating and rating >= 4:
        S.append(f"Owner rates service quality {rating}/5 — a strong base to build on")
    love = _clean(a.get("customers_love"))
    if love:
        S.append(f"Customers love: “{love[:120]}”")
    if online and online >= 7:
        S.append(f"Good online presence ({online}/10)")
    if _yes(a.get("billing_software")):
        S.append("Already uses billing software — data is available to manage the business")
    if _yes(a.get("track_financials")):
        S.append("Tracks revenue and costs — decisions can be based on numbers")
    if _yes(a.get("manager")):
        S.append("Has a manager, so the owner is not tied to the counter all day")
    if margin is not None and margin >= 0.25:
        S.append(f"Healthy margin: about {margin:.0%} of revenue left after expenses")
    if rent_pct is not None and rent_pct <= 0.15:
        S.append(f"Rent is under control ({rent_pct:.0%} of revenue)")
    if _yes(a.get("digital_marketing_spend")) or _yes(a.get("digital_ads")):
        S.append("Already investing in digital marketing")

    # Weaknesses
    if online is not None and online <= 5:
        W.append(f"Weak online presence ({online}/10) — new customers can't easily find the salon")
    if not _yes(a.get("digital_marketing_spend")) and not _yes(a.get("digital_ads")):
        W.append("No spending on digital marketing or ads")
    if not _yes(a.get("billing_software")):
        W.append("No billing software — bills, repeat visits and staff performance are not tracked")
    if not _yes(a.get("track_financials")):
        W.append("Revenue, profit and cost per service are not tracked")
    if staff_struggle:
        why = _clean(re.sub(r"^\s*yes[\s,.:-]*", "", str(a.get("staff_struggle") or ""), flags=re.I))
        W.append("Struggles to hire and keep skilled staff" + (f" — “{why[:100]}”" if why else ""))
    if not training:
        W.append("No structured staff training program")
    weak_map = {"Inefficient appointment management": "Appointments are not managed well",
                "Staff turnover & inconsistent service": "Staff turnover makes service inconsistent",
                "Low client retention & satisfaction": "Customers don't come back often enough",
                "Limited digital marketing or online presence": None,
                "Financial issues (pricing, cash flow, profitability)": "Money pressure: pricing, cash flow or profit"}
    for c in chall:
        if weak_map.get(c):
            W.append(weak_map[c])
        elif c.startswith("Other: "):
            W.append(c[7:])
    if margin is not None and margin < 0.1:
        W.append("Very little left after expenses" + (f" (about {margin:.0%})" if margin >= 0 else
                                                       " — expenses are higher than revenue"))
    if rent_pct is not None and rent_pct > 0.25:
        W.append(f"Rent takes {rent_pct:.0%} of revenue (healthy is under 15–20%)")
    comp = _clean(a.get("complaints"))
    if comp:
        W.append(f"Most common complaint: “{comp[:120]}”")
    if rating and rating <= 3:
        W.append(f"Owner rates service quality only {rating}/5")

    # Opportunities
    opp_map = {"New customer segments (bridal, men’s grooming, luxury)":
               "New customer groups — bridal packages, men's grooming and premium services",
               "Expanding services (new treatments, high-end offerings)": "Add new, higher-value treatments",
               "Retailing beauty products & upselling services": "Sell products and add-on services at the counter",
               "Partnering with brands, influencers, or academies": "Partner with brands, influencers or academies",
               "Improving digital marketing & social media presence":
               "Grow on Instagram and Google — most customers now choose salons online"}
    for g_ in growth:
        if g_ in opp_map:
            O.append(opp_map[g_])
        elif g_.startswith("Other: "):
            O.append(g_[7:])
    if not _yes(a.get("loyalty")):
        O.append("Start a membership or loyalty plan to bring customers back every month")
    if not _yes(a.get("digital_ads")):
        O.append("Local Instagram/Google ads can bring new walk-ins within weeks")
    if per_chair is not None and per_chair < 40000:
        O.append(f"Each chair earns about {_inr(per_chair)} a month — filling empty slots can lift revenue")
    idea = _clean(a.get("growth_strategies"))
    if idea:
        O.append(f"Owner's own idea: “{idea[:120]}”")
    O.append("A free video by Vikas Rachakonda can put the salon in front of thousands of local viewers")

    # Threats
    th_map = {"New competitors in the market": "New salons opening nearby",
              "Changing customer preferences & industry trends": "Customer tastes and trends are changing fast",
              "Rising costs (rent, staff salaries, products)": "Rising rent, salaries and product costs",
              "Economic downturn affecting customer spending": "Customers cutting back when money is tight",
              "Government regulations & compliance issues": "Licences, tax and compliance rules"}
    for t in threats:
        if t in th_map:
            Th.append(th_map[t])
        elif t.startswith("Other: "):
            Th.append(t[7:])
    if _yes(a.get("price_competition")):
        Th.append("Customers move to cheaper salons — price competition")
    if _yes(a.get("revenue_fluctuation")):
        Th.append("Revenue has gone up and down in the last 6 months")
    ext = _clean(a.get("external_factors"))
    if ext:
        Th.append(f"Owner mentions: “{ext[:120]}”")
    if staff_struggle:
        Th.append("Losing trained staff to competitors")

    # Scores (1–10) for the report's health bars
    def clamp(x):
        return max(1, min(10, round(x)))
    marketing = clamp((online or 4) * 0.6 + (2 if _yes(a.get("digital_ads")) else 0)
                      + (1.5 if _yes(a.get("digital_marketing_spend")) else 0) + (1 if growth else 0))
    operations = clamp(5 + (2 if _yes(a.get("billing_software")) else -1) + (1 if _yes(a.get("manager")) else 0)
                       - (2 if "Inefficient appointment management" in chall else 0)
                       + (1 if (rating or 0) >= 4 else 0))
    team = clamp(6 - (3 if staff_struggle else 0) + (2 if training else -1)
                 - (1 if "Staff turnover & inconsistent service" in chall else 0)
                 + (1 if "Highly skilled staff" in stand else 0))
    finance = clamp(5 + (2 if _yes(a.get("track_financials")) else -1)
                    + ((2 if margin >= 0.25 else 0 if margin >= 0.1 else -2) if margin is not None else 0)
                    + ((1 if rent_pct <= 0.15 else -1 if rent_pct > 0.25 else 0) if rent_pct is not None else 0)
                    - (1 if "Financial issues (pricing, cash flow, profitability)" in chall else 0))
    customers = clamp(5 + (2 if "High client retention rate" in stand else 0) + ((rating or 3) - 3)
                      - (2 if "Low client retention & satisfaction" in chall else 0)
                      + (1 if _yes(a.get("loyalty")) else 0))
    scores = [("Marketing & online", marketing), ("Operations", operations), ("Team & training", team),
              ("Money & profit", finance), ("Customer loyalty", customers)]

    # Next steps — each tied to how Salon Shastra helps
    steps = []

    def step(title, why, how, when, area):
        steps.append({"title": title, "why": why, "how": how, "when": when, "area": area})
    if marketing <= 6 or "digital_marketing" in help_areas:
        step("Fix your online presence", "Most new customers look you up on Instagram and Google first.",
             "Salon Shastra's Hyperlocal Marketing System and Google & online reputation growth.", "Next 30 days",
             "digital_marketing")
    if not _yes(a.get("billing_software")) or "Inefficient appointment management" in chall or "chairpe" in help_areas:
        step("Run the salon on ChairPe",
             "Billing, appointments, staff and stock in one place, plus each chair's profit and a list of customers "
             "who stopped coming, with WhatsApp win-back messages.",
             "Start ChairPe's 14-day free Elite trial (no card needed); we set it up with your team.", "This week",
             "chairpe")
    if staff_struggle or "staff_hiring" in help_areas:
        step("Build a reliable team", "Staff turnover is the biggest reason service quality drops.",
             "Salon Shastra's staff hiring support and staff structure & salary planning.", "Next 30 days",
             "staff_hiring")
    if not training or "staff_training" in help_areas:
        step("Start a simple staff training plan", "Trained staff upsell better and customers return.",
             "Technical skill training in your salon, plus behaviour, retail & sales training.", "Next 60 days",
             "staff_training")
    if finance <= 6 or "business_consulting" in help_areas or "operations" in help_areas:
        step("Get your numbers right", "Know your profit per service, right prices and rent limit.",
             "Salon Business Audit & Diagnosis, then the 90-day Fix & Relaunch Program if needed.", "Next 30 days",
             "business_consulting")
    if not _yes(a.get("loyalty")):
        step("Launch a membership / loyalty plan", "Members visit more often and spend more.",
             "Customer Retention & Reactivation System; memberships and wallets run automatically in ChairPe.",
             "Next 60 days", "operations")
    step(f"Talk to {CONSULTANT_NAME}", "Get a clear plan for your salon from the founder of Salon Shastra.",
         "Book a free call using the link in our message.", "This week", "vikas_consult")

    name = a.get("salon_name") or "your salon"
    strongest = max(scores, key=lambda s: s[1])
    weakest = min(scores, key=lambda s: s[1])
    summary = (f"{name} is strongest in {strongest[0].lower()} ({strongest[1]}/10) and most needs attention in "
               f"{weakest[0].lower()} ({weakest[1]}/10). The steps below are in order of impact — start with the "
               f"first one this week.")
    facts = [("Salon type", a.get("salon_type") or "—"), ("Location", a.get("location") or "—"),
             ("Chairs", a.get("chairs") or "—"), ("Area", f"{a['area_sft']} sq ft" if a.get("area_sft") else "—"),
             ("Monthly revenue", _inr(rev)), ("Monthly rent", _inr(rent)),
             ("Left after expenses", f"{margin:.0%}" if margin is not None else "—"),
             ("Online presence", f"{online}/10" if online else "—")]

    def dedupe(xs, n):
        seen, out = set(), []
        for x in xs:
            if x and x.lower() not in seen:
                seen.add(x.lower())
                out.append(x)
        return out[:n]
    return {"rules": SWOT_VERSION, "salon_name": name, "owner_name": a.get("owner_name") or "",
            "summary": summary, "scores": scores, "facts": facts,
            "strengths": dedupe(S, 6) or ["The owner is looking for ways to grow — the most important strength"],
            "weaknesses": dedupe(W, 6), "opportunities": dedupe(O, 6), "threats": dedupe(Th, 6),
            "steps": steps[:6]}


def draft_report(db, opp_id: int, response_id: int | None, actor_id) -> int:
    resp = (db.one("SELECT * FROM pilot_responses WHERE id = ?", (response_id,)) if response_id
            else db.one("SELECT * FROM pilot_responses WHERE opportunity_id = ? ORDER BY id DESC LIMIT 1", (opp_id,)))
    if not resp:
        raise ValidationError({"report": "The pilot form has not been filled yet."})
    content = build_swot(json.loads(resp["answers"]), profile(db, opp_id)["help_areas"])
    version = (db.scalar("SELECT MAX(version) FROM swot_reports WHERE opportunity_id = ?", (opp_id,)) or 0) + 1
    return db.insert("swot_reports", {"opportunity_id": opp_id, "response_id": resp["id"], "version": version,
                                      "content": json.dumps(content, ensure_ascii=False), "status": "draft",
                                      "created_by": actor_id, "created_at": now_iso(), "updated_at": now_iso()})


def latest_report(db, opp_id: int, approved_only: bool = False) -> dict | None:
    row = db.one("SELECT r.*, u.full_name AS approver_name FROM swot_reports r LEFT JOIN users u ON u.id = "
                 "r.approved_by WHERE r.opportunity_id = ?" + (" AND r.status = 'approved'" if approved_only else "")
                 + " ORDER BY r.version DESC LIMIT 1", (opp_id,))
    if not row:
        return None
    row = dict(row)
    row["c"] = json.loads(row["content"])
    return row


def _lines(text: str, n: int = 8) -> list[str]:
    return [re.sub(r"\s+", " ", x).strip(" -•*")[:300] for x in (text or "").splitlines()
            if x.strip(" -•*")][:n]


def save_report(db, actor, opp, report_id: int, form, approve: bool) -> None:
    _need(db, actor, "crm.edit")
    rep = db.one("SELECT * FROM swot_reports WHERE id = ? AND opportunity_id = ?", (report_id, opp["id"]))
    if not rep:
        raise ValidationError({"report": "Report not found."})
    c = json.loads(rep["content"])
    c["summary"] = (form.get("summary") or "").strip()[:1200] or c["summary"]
    for k in ("strengths", "weaknesses", "opportunities", "threats"):
        c[k] = _lines(form.get(k), 8)
    steps = []
    for i in range(8):
        t = (form.get(f"step_title_{i}") or "").strip()[:120]
        if t:
            steps.append({"title": t, "why": (form.get(f"step_why_{i}") or "").strip()[:240],
                          "how": (form.get(f"step_how_{i}") or "").strip()[:240],
                          "when": (form.get(f"step_when_{i}") or "").strip()[:40] or "Next 30 days",
                          "area": form.get(f"step_area_{i}") or ""})
    c["steps"] = steps
    if approve and not (c["strengths"] and c["weaknesses"] and steps):
        raise ValidationError({"report": "Add at least one strength, one weakness and one next step before "
                                         "approving."})
    with db:
        vals = {"content": json.dumps(c, ensure_ascii=False), "updated_at": now_iso()}
        if rep["status"] == "approved" and not approve:  # editing an approved report makes a new draft version
            report_id = db.insert("swot_reports", {**{k: rep[k] for k in ("opportunity_id", "response_id")},
                                                   "version": rep["version"] + 1, "status": "draft",
                                                   "created_by": actor["id"], "created_at": now_iso(), **vals})
        else:
            if approve:
                _need(db, actor, "crm.edit")
                vals.update(status="approved", approved_by=actor["id"], approved_at=now_iso())
            sets = ", ".join(f"{k} = ?" for k in vals)
            db.execute(f"UPDATE swot_reports SET {sets} WHERE id = ?", (*vals.values(), report_id))
        T.add_activity(db, actor["id"], "opportunity", opp["id"], "approval" if approve else "data_update",
                       f"SWOT report v{rep['version']} " + ("approved — ready to send" if approve else "edited"))
        audit.record(db, actor["id"], "pilot.report_" + ("approved" if approve else "edited"), "opportunity",
                     opp["id"], {"report": report_id})


def redraft(db, actor, opp) -> int:
    _need(db, actor, "crm.edit")
    with db:
        rid = draft_report(db, opp["id"], None, actor["id"])
        T.add_activity(db, actor["id"], "opportunity", opp["id"], "data_update",
                       "SWOT report re-drafted from the latest form answers")
    return rid


def mark_sent(db, actor, opp) -> None:
    _need(db, actor, "crm.log_activity")
    rep = latest_report(db, opp["id"], approved_only=True)
    if not rep:
        raise ValidationError({"report": "Approve the report first."})
    with db:
        db.execute("UPDATE swot_reports SET sent_at = ?, sent_by = ? WHERE id = ?", (now_iso(), actor["id"],
                                                                                    rep["id"]))
        T.add_activity(db, actor["id"], "opportunity", opp["id"], "note",
                       f"SWOT report v{rep['version']} sent to the salon owner with the booking link")


def report_message(opp: dict, link: str) -> str:
    first = (opp.get("contact_name") or "").split(" ")[0] or "Sir/Madam"
    return (f"Hello {first}, thank you for filling the Salon Shastra Pilot Program form.\n\n"
            f"Your personalised SWOT report for {opp.get('salon_name') or 'your salon'} is ready — it shows what is "
            f"working, what to fix first and clear next steps:\n{link}\n\n"
            f"Would you like to discuss it with {CONSULTANT_NAME}? Pick a time that suits you on the same page.\n\n"
            "— Team Salon Shastra")


def form_message(opp: dict, link: str) -> str:
    first = (opp.get("contact_name") or "").split(" ")[0] or "Sir/Madam"
    return (f"Hello {first}, thank you for speaking with Salon Shastra today.\n\n"
            "Please fill this short Pilot Program form (5–10 minutes). Based on your answers we will send you a "
            f"free SWOT report for your salon with clear next steps:\n{link}\n\n— Team Salon Shastra")


# -- Vikas Rachakonda: slots and bookings -------------------------------------------------------------------------
def consultant(db) -> dict | None:
    """The TeamOS user who receives booking alerts (set on the Pilot page; default: first super admin)."""
    uid = db.scalar("SELECT value FROM integration_settings WHERE key = ?", (CONSULTANT_SETTING,))
    row = None
    if uid and str(uid).isdigit():
        row = db.one("SELECT * FROM users WHERE id = ? AND is_active = 1", (int(uid),))
    return row or db.one("SELECT * FROM users WHERE role = 'super_admin' AND is_active = 1 ORDER BY id LIMIT 1")


def set_consultant(db, actor, user_id: int) -> None:
    _need(db, actor, "settings.manage")
    if not db.one("SELECT id FROM users WHERE id = ? AND is_active = 1", (user_id,)):
        raise ValidationError({"user": "Choose an active user."})
    with db:
        if db.scalar("SELECT id FROM integration_settings WHERE key = ?", (CONSULTANT_SETTING,)):
            db.execute("UPDATE integration_settings SET value = ?, updated_by = ?, updated_at = ? WHERE key = ?",
                       (str(user_id), actor["id"], now_iso(), CONSULTANT_SETTING))
        else:
            db.insert("integration_settings", {"key": CONSULTANT_SETTING, "value": str(user_id), "is_secret": 0,
                                               "updated_by": actor["id"], "updated_at": now_iso()})
        audit.record(db, actor["id"], "pilot.consultant_set", "user", user_id, {})


def can_manage_slots(db, actor) -> bool:
    c = consultant(db)
    return bool(actor and (user_can(db, actor, "settings.manage") or user_can(db, actor, "crm.assign")
                           or (c and c["id"] == actor["id"])))


def add_slots(db, actor, day: str, times: list[str], minutes: int, mode: str) -> int:
    if not can_manage_slots(db, actor):
        raise PermissionDenied("pilot.slots")
    if mode not in ("phone", "video", "office"):
        raise ValidationError({"mode": "Choose phone, video or office."})
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day or ""):
        raise ValidationError({"day": "Choose a date."})
    made = 0
    from datetime import datetime
    with db:
        for t in times:
            if not re.fullmatch(r"\d{2}:\d{2}", t or ""):
                continue
            local = datetime.strptime(f"{day} {t}", "%Y-%m-%d %H:%M").replace(tzinfo=IST)
            iso = to_iso(local)
            if iso <= now_iso() or db.scalar("SELECT id FROM vikas_slots WHERE starts_at = ? AND status <> "
                                             "'cancelled'", (iso,)):
                continue
            db.insert("vikas_slots", {"starts_at": iso, "minutes": max(10, min(120, minutes or 30)), "mode": mode,
                                      "status": "open", "created_by": actor["id"], "created_at": now_iso()})
            made += 1
    if not made:
        raise ValidationError({"slots": "No new future slots were added (past times or already listed)."})
    return made


def cancel_slot(db, actor, slot_id: int) -> None:
    if not can_manage_slots(db, actor):
        raise PermissionDenied("pilot.slots")
    if db.scalar("SELECT status FROM vikas_slots WHERE id = ?", (slot_id,)) != "open":
        raise ValidationError({"slot": "Only open (unbooked) slots can be removed."})
    db.execute("UPDATE vikas_slots SET status = 'cancelled' WHERE id = ?", (slot_id,))


def open_slots(db, days: int = 21) -> list[dict]:
    until = to_iso(now_utc() + timedelta(days=days))
    soon = to_iso(now_utc() + timedelta(hours=2))  # no surprise bookings within 2 hours
    return db.all("SELECT * FROM vikas_slots WHERE status = 'open' AND starts_at > ? AND starts_at < ? "
                  "ORDER BY starts_at LIMIT 60", (soon, until))


def _alert_consultant(db, opp, text: str) -> None:
    from flask import current_app, has_app_context

    from .. import messaging
    c = consultant(db)
    link = f"/sellers/{opp['id']}?tab=pilot"
    if c:
        T.notify(db, c["id"], "vikas_booking", text, link)
        try:
            prov = messaging.email_provider(current_app.config if has_app_context() else None)
            body = (f"{text}\n\nSalon: {opp.get('salon_name') or '—'} ({opp.get('ref_code')})\n"
                    f"Owner: {opp.get('contact_name') or '—'}\nOpen in TeamOS: {link}\n")
            res = prov.send(c["email"], f"Salon Shastra: {text}", body)
            db.insert("email_outbox", {"user_id": c["id"], "to_addr": c["email"], "subject": f"Salon Shastra: {text}",
                                       "body": body, "kind": "vikas_booking", "status": res["status"],
                                       "provider": prov.name, "error": res.get("detail") if res["status"] == "failed"
                                       else None, "dedupe_key": None, "created_at": now_iso(),
                                       "sent_at": now_iso() if res["status"] == "sent" else None})
        except Exception:  # noqa: BLE001 — an e-mail problem must never lose the booking; the in-app alert stands
            pass
    if opp.get("primary_owner_id") and (not c or c["id"] != opp["primary_owner_id"]):
        T.notify(db, opp["primary_owner_id"], "vikas_booking", text, link)


def book(db, opp: dict, slot_id: int | None, preferred: str | None, source: str, actor_id=None,
         note: str | None = None) -> int:
    """Owner (via link) or team books a call with Vikas. Without a free slot it becomes a request."""
    if db.scalar("SELECT COUNT(*) FROM vikas_bookings WHERE opportunity_id = ? AND status IN ('requested','booked') "
                 "AND (starts_at IS NULL OR starts_at > ?)", (opp["id"], now_iso())):
        raise ValidationError({"booking": "A call with Vikas is already booked for this salon."})
    with db:
        if slot_id:
            slot = db.one("SELECT * FROM vikas_slots WHERE id = ?", (slot_id,))
            if not slot or slot["status"] != "open" or slot["starts_at"] <= now_iso():
                raise ValidationError({"slot": "Sorry, that time was just taken. Please pick another."})
            n = db.execute("UPDATE vikas_slots SET status = 'booked' WHERE id = ? AND status = 'open'",
                           (slot_id,)).rowcount
            if n != 1:
                raise ValidationError({"slot": "Sorry, that time was just taken. Please pick another."})
            bid = db.insert("vikas_bookings", {"opportunity_id": opp["id"], "slot_id": slot_id,
                                               "starts_at": slot["starts_at"], "status": "booked", "source": source,
                                               "note": (note or "")[:500] or None, "created_by": actor_id,
                                               "created_at": now_iso(), "updated_at": now_iso()})
            when = fmt_ist(slot["starts_at"], "%a %d %b, %I:%M %p")
            text = f"Call booked with {opp.get('contact_name') or 'a salon owner'} — {when} ({slot['mode']})"
        else:
            pref = (preferred or "").strip()[:200]
            if not pref:
                raise ValidationError({"preferred": "Tell us a day and time that suits you."})
            bid = db.insert("vikas_bookings", {"opportunity_id": opp["id"], "preferred_time": pref,
                                               "status": "requested", "source": source,
                                               "note": (note or "")[:500] or None, "created_by": actor_id,
                                               "created_at": now_iso(), "updated_at": now_iso()})
            text = f"Call requested by {opp.get('contact_name') or 'a salon owner'} — prefers: {pref}"
        T.add_activity(db, actor_id, "opportunity", opp["id"], "system",
                       text.replace("Call booked with", "Call with Vikas booked for").replace(
                           "Call requested by", "Call with Vikas requested by"))
        _alert_consultant(db, opp, text)
        audit.record(db, actor_id, "pilot.vikas_booked", "opportunity", opp["id"], {"booking": bid,
                                                                                   "source": source})
    return bid


def update_booking(db, actor, opp, booking_id: int, status: str, outcome: str | None,
                   starts_at: str | None = None) -> None:
    _need(db, actor, "crm.log_activity")
    b = db.one("SELECT * FROM vikas_bookings WHERE id = ? AND opportunity_id = ?", (booking_id, opp["id"]))
    if not b:
        raise ValidationError({"booking": "Booking not found."})
    if status not in ("requested", "booked", "done", "cancelled", "no_show"):
        raise ValidationError({"status": "Unknown status."})
    if status == "booked" and not (starts_at or b["starts_at"]):
        raise ValidationError({"starts_at": "Set the date and time of the call."})
    with db:
        db.execute("UPDATE vikas_bookings SET status = ?, outcome_note = COALESCE(?, outcome_note), "
                   "starts_at = COALESCE(?, starts_at), updated_at = ? WHERE id = ?",
                   (status, (outcome or "").strip()[:1000] or None, starts_at, now_iso(), booking_id))
        if status in ("cancelled", "no_show") and b["slot_id"] and b["status"] == "booked":
            if status == "cancelled":
                db.execute("UPDATE vikas_slots SET status = 'open' WHERE id = ? AND starts_at > ?",
                           (b["slot_id"], now_iso()))
        labels = {"done": "held", "cancelled": "cancelled", "no_show": "missed (no-show)", "booked": "confirmed",
                  "requested": "back to requested"}
        T.add_activity(db, actor["id"], "opportunity", opp["id"], "note",
                       f"Call with Vikas {labels[status]}" + (f": {outcome.strip()[:200]}" if outcome and
                                                              outcome.strip() else ""))
        if status == "done":
            T.create_task(db, actor, title="Call back: does the owner want a paid consultation?",
                          entity_type="opportunity", entity_id=opp["id"],
                          assignee_id=opp.get("primary_owner_id") or actor["id"],
                          due_at=to_iso(now_utc() + timedelta(days=1)), task_type="call", priority="high",
                          is_synthetic=opp.get("is_synthetic") or 0)
        if status == "booked" and starts_at:
            _alert_consultant(db, opp, f"Call confirmed with {opp.get('contact_name') or 'a salon owner'} — "
                                       f"{fmt_ist(starts_at, '%a %d %b, %I:%M %p')}")


def bookings(db, opp_id: int) -> list[dict]:
    return db.all("SELECT * FROM vikas_bookings WHERE opportunity_id = ? ORDER BY id DESC", (opp_id,))


# -- Paid consultations ---------------------------------------------------------------------------------------------
def fee_for(kind: str, in_hyderabad: bool) -> int:
    return FEES[(kind, 1 if in_hyderabad else 0)]


def book_consultation(db, actor, opp, data) -> int:
    _need(db, actor, "crm.log_activity")
    kind = data.get("kind")
    if kind not in dict(CONSULT_KINDS):
        raise ValidationError({"kind": "Choose office meeting or salon visit."})
    hyd = data.get("in_hyderabad", "1") == "1"
    fee = fee_for(kind, hyd)
    from ..timeutil import parse_local_input
    when = parse_local_input(data.get("scheduled_at"))
    with db:
        cid = db.insert("consultations", {"opportunity_id": opp["id"], "kind": kind, "in_hyderabad": 1 if hyd else 0,
                                          "fee": fee, "scheduled_at": when, "status": "booked",
                                          "note": (data.get("note") or "").strip()[:500] or None,
                                          "created_by": actor["id"], "created_at": now_iso(),
                                          "updated_at": now_iso()})
        T.add_activity(db, actor["id"], "opportunity", opp["id"], "system",
                       f"Consultation booked: {dict(CONSULT_KINDS)[kind]}, "
                       f"{'Hyderabad' if hyd else 'outside Hyderabad'} — ₹{fee:,}"
                       + (f" on {fmt_ist(when)}" if when else "") + " (+ free video by Vikas)")
        _alert_consultant(db, opp, f"Paid consultation booked ({dict(CONSULT_KINDS)[kind].lower()}, ₹{fee:,})"
                          + (f" — {fmt_ist(when, '%a %d %b, %I:%M %p')}" if when else ""))
        audit.record(db, actor["id"], "pilot.consultation_booked", "opportunity", opp["id"],
                     {"consultation": cid, "fee": fee})
    return cid


def update_consultation(db, actor, opp, cid: int, data) -> None:
    _need(db, actor, "crm.log_activity")
    c = db.one("SELECT * FROM consultations WHERE id = ? AND opportunity_id = ?", (cid, opp["id"]))
    if not c:
        raise ValidationError({"consultation": "Not found."})
    vals, notes = {}, []
    if data.get("status") in ("booked", "done", "cancelled") and data["status"] != c["status"]:
        vals["status"] = data["status"]
        notes.append({"done": "held", "cancelled": "cancelled", "booked": "re-booked"}[data["status"]])
    if data.get("paid") == "1" and not c["paid"]:
        mode = data.get("paid_mode")
        if mode not in dict(PAY_MODES):
            raise ValidationError({"paid_mode": "Choose how it was paid."})
        vals.update(paid=1, paid_mode=mode, paid_ref=(data.get("paid_ref") or "").strip()[:80] or None,
                    paid_at=now_iso())
        notes.append(f"₹{c['fee']:,} received by {dict(PAY_MODES)[mode]}")
    if data.get("video_status") in dict(VIDEO_STATUS) and data["video_status"] != c["video_status"]:
        vals["video_status"] = data["video_status"]
        notes.append("video: " + dict(VIDEO_STATUS)[data["video_status"]].lower())
    link = (data.get("video_link") or "").strip()[:300]
    if link and link != (c["video_link"] or ""):
        if not re.match(r"https?://", link):
            raise ValidationError({"video_link": "Paste the full video link (https://…)."})
        vals["video_link"] = link
    if not vals:
        return
    vals["updated_at"] = now_iso()
    with db:
        sets = ", ".join(f"{k} = ?" for k in vals)
        db.execute(f"UPDATE consultations SET {sets} WHERE id = ?", (*vals.values(), cid))
        if notes:
            T.add_activity(db, actor["id"], "opportunity", opp["id"], "note", "Consultation: " + "; ".join(notes))
        audit.record(db, actor["id"], "pilot.consultation_updated", "opportunity", opp["id"],
                     {"consultation": cid, **{k: v for k, v in vals.items() if k != "updated_at"}})


def consultations(db, opp_id: int) -> list[dict]:
    return db.all("SELECT * FROM consultations WHERE opportunity_id = ? ORDER BY id DESC", (opp_id,))


# -- Overview -------------------------------------------------------------------------------------------------------
def overview(db, area: str = "", status: str = "") -> dict:
    rows = db.all(
        "SELECT o.id, o.ref_code, o.stage, o.status, o.created_at, c.full_name AS contact_name, b.name AS salon_name, "
        "b.locality, b.city, u.full_name AS owner_name, p.lead_type, p.help_areas, p.form_status, "
        "p.chairpe_status, p.chairpe_plan, "
        "(SELECT r.status FROM swot_reports r WHERE r.opportunity_id = o.id ORDER BY r.version DESC LIMIT 1) "
        "AS report_status, "
        "(SELECT MAX(r.sent_at) FROM swot_reports r WHERE r.opportunity_id = o.id) AS report_sent_at, "
        "(SELECT vb.status FROM vikas_bookings vb WHERE vb.opportunity_id = o.id ORDER BY vb.id DESC LIMIT 1) "
        "AS vikas_status, "
        "(SELECT vb.starts_at FROM vikas_bookings vb WHERE vb.opportunity_id = o.id ORDER BY vb.id DESC LIMIT 1) "
        "AS vikas_at, "
        "(SELECT COUNT(*) FROM consultations k WHERE k.opportunity_id = o.id AND k.status <> 'cancelled') "
        "AS consults, "
        "(SELECT COALESCE(SUM(k.fee), 0) FROM consultations k WHERE k.opportunity_id = o.id AND k.paid = 1) AS paid "
        "FROM opportunities o JOIN contacts c ON c.id = o.contact_id "
        "LEFT JOIN salon_businesses b ON b.id = o.business_id LEFT JOIN users u ON u.id = o.primary_owner_id "
        "LEFT JOIN pilot_profiles p ON p.opportunity_id = o.id WHERE o.kind = 'seller' AND o.status <> 'closed' "
        "ORDER BY o.created_at DESC LIMIT 400")
    out = []
    for r in rows:
        r = dict(r)
        try:
            r["areas"] = [h for h in json.loads(r["help_areas"] or "[]") if h in HELP_LABELS]
        except ValueError:
            r["areas"] = []
        r["form_status"] = r["form_status"] or "not_sent"
        r["chairpe_status"] = r["chairpe_status"] or "not_offered"
        if area == "chairpe_open" and r["chairpe_status"] not in ("demo", "trial"):
            continue
        if area and area != "chairpe_open" and area not in r["areas"]:
            continue
        if status and r["form_status"] != status:
            continue
        out.append(r)
    money = db.one("SELECT COUNT(*) AS n, COALESCE(SUM(fee), 0) AS booked, "
                   "COALESCE(SUM(CASE WHEN paid = 1 THEN fee ELSE 0 END), 0) AS collected, "
                   "COALESCE(SUM(CASE WHEN video_status = 'posted' THEN 1 ELSE 0 END), 0) AS videos "
                   "FROM consultations WHERE status <> 'cancelled'")
    counts = {k: 0 for k, _ in FORM_STATUS}
    area_counts = {k: 0 for k, _ in HELP_AREAS}
    for r in rows:
        try:
            areas = json.loads(r["help_areas"] or "[]")
        except ValueError:
            areas = []
        counts[r["form_status"] or "not_sent"] = counts.get(r["form_status"] or "not_sent", 0) + 1
        for a_ in areas:
            if a_ in area_counts:
                area_counts[a_] += 1
    upcoming = db.all("SELECT vb.*, o.ref_code, c.full_name AS contact_name, b.name AS salon_name FROM vikas_bookings vb "
                      "JOIN opportunities o ON o.id = vb.opportunity_id JOIN contacts c ON c.id = o.contact_id "
                      "LEFT JOIN salon_businesses b ON b.id = o.business_id WHERE vb.status IN ('requested','booked') "
                      "ORDER BY CASE WHEN vb.starts_at IS NULL THEN 0 ELSE 1 END, vb.starts_at LIMIT 30")
    chairpe = {k: 0 for k, _ in CHAIRPE_STATUS}
    for r in db.all("SELECT chairpe_status, COUNT(*) AS n FROM pilot_profiles GROUP BY chairpe_status"):
        chairpe[r["chairpe_status"]] = r["n"]
    return {"rows": out, "money": money, "counts": counts, "area_counts": area_counts, "upcoming": upcoming,
            "chairpe": chairpe}


def record_context(db, opp: dict) -> dict:
    prof = profile(db, opp["id"])
    resp = latest_response(db, opp["id"])
    return {"pilot": prof, "pilot_response": resp,
            "chairpe_suggested": suggest_chairpe_plan(resp["answers"] if resp else None),
            "chairpe_trial_ends": chairpe_trial_end(prof),
            "pilot_report": latest_report(db, opp["id"]), "pilot_bookings": bookings(db, opp["id"]),
            "pilot_consults": consultations(db, opp["id"]), "pilot_slots": open_slots(db, 14),
            "pilot_consultant": consultant(db)}


def parse_iso_safe(v):
    return parse_iso(v) if v else None


# -- ChairPe sale ---------------------------------------------------------------------------------------------------
def suggest_chairpe_plan(answers: dict | None) -> str:
    """Basic for a one/two-chair salon with almost no staff; otherwise Elite (what most salons choose)."""
    a = answers or {}
    chairs = parse_amount(a.get("chairs")) or 0
    staff = (parse_amount(a.get("hair_dressers")) or 0) + (parse_amount(a.get("beauticians")) or 0)
    if a and chairs and chairs <= 2 and staff <= 2:
        return "basic"
    return "elite"


def set_chairpe(db, actor, opp, data) -> None:
    _need(db, actor, "crm.log_activity")
    status = data.get("chairpe_status") or ""
    if status not in CHAIRPE_STATUS_LABELS:
        raise ValidationError({"chairpe": "Choose a ChairPe status."})
    plan = data.get("chairpe_plan") or None
    if plan and plan not in CHAIRPE_PLAN_LABELS:
        raise ValidationError({"chairpe": "Unknown plan."})
    months = int(data["chairpe_months"]) if str(data.get("chairpe_months") or "").isdigit() else None
    if status == "paying":
        if not plan:
            raise ValidationError({"chairpe": "Choose the plan they paid for."})
        if months not in CHAIRPE_MONTHS:
            raise ValidationError({"chairpe": "Choose how many months they paid for (1, 3, 6 or 12)."})
    prof = profile(db, opp["id"])
    vals = {"chairpe_status": status, "chairpe_plan": plan, "chairpe_months": months if status == "paying" else None,
            "chairpe_note": (data.get("chairpe_note") or "").strip()[:500] or None}
    if status == "trial" and prof["chairpe_status"] != "trial":
        vals["chairpe_trial_start"] = now_iso()
    if status == "paying" and prof["chairpe_status"] != "paying":
        vals["chairpe_paid_on"] = now_iso()
    with db:
        _upsert(db, opp["id"], actor["id"], vals)
        text = f"ChairPe: {CHAIRPE_STATUS_LABELS[status]}"
        if plan:
            text += f" ({CHAIRPE_PLAN_LABELS[plan]}" + (f" x {months} month(s))" if status == "paying" else ")")
        T.add_activity(db, actor["id"], "opportunity", opp["id"], "note", text)
        if "chairpe_trial_start" in vals:  # remind the record owner 2 days before the free trial ends
            ends = now_utc() + timedelta(days=CHAIRPE_TRIAL_DAYS)
            T.create_task(db, actor, title=f"ChairPe trial ends {fmt_ist(to_iso(ends), '%d %b')}: help them pick a plan",
                          entity_type="opportunity", entity_id=opp["id"],
                          assignee_id=opp.get("primary_owner_id") or actor["id"],
                          due_at=to_iso(ends - timedelta(days=2)), task_type="call", priority="high",
                          is_synthetic=opp.get("is_synthetic") or 0)
        audit.record(db, actor["id"], "pilot.chairpe", "opportunity", opp["id"],
                     {"status": status, "plan": plan, "months": vals["chairpe_months"]})


def chairpe_trial_end(prof: dict) -> str | None:
    start = parse_iso(prof.get("chairpe_trial_start")) if prof.get("chairpe_trial_start") else None
    return to_iso(start + timedelta(days=CHAIRPE_TRIAL_DAYS)) if start else None
