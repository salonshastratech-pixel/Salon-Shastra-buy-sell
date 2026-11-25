"""Buyers & investors: is this a serious investor, what can they handle, and what should they do?

Founder's process (24 Nov 2026):
  team calls → asks the 5 screening questions → TeamOS says Pursue / Nurture / Not now (spend time or not) →
  team sends the investor form (no prices in it) → form filled → short call with Vikas Rachakonda → office visit,
  registration fee ₹5,000 so Salon Shastra starts working for them → investor readiness report (invest / invest with
  support / not yet) sent after the call with Vikas.

Thinking like a consultant: the questions check the money (how much, from where, savings or loan, EMI, and cash kept
aside to run the salon for 6 months even if sales are slow), the person (experience, time, staff handling, strengths),
the plan (own salon / buy running / franchise / investor-only / support only, area, size, timing) and the expectations
(payback, loss they can take, who decides).
"""
from __future__ import annotations

import json
import re

from .. import audit
from ..permissions import user_can
from ..timeutil import now_iso
from . import pilot as P
from . import tasks as T
from .errors import PermissionDenied, ValidationError

REG_FEE = 5000  # registration fee — staff only, never shown on the form (founder, 24 Nov 2026)

# -- 1. First-call screening ----------------------------------------------------------------------------------------
# (key, question, [(value, label, points 0–20)], hint for the caller)
SCREEN = [
    ("goal", "What do you want to do?", [
        ("buy_running", "Buy a running salon", 20), ("new_salon", "Open a new salon", 20),
        ("franchise", "Take a franchise", 16), ("invest_only", "Invest money only — someone else runs it", 12),
        ("support_only", "Wants advice / support only, not investing yet", 8), ("exploring", "Just exploring", 2)],
     "Listen for a clear plan."),
    ("budget", "How much can you put in, everything included?", [
        ("lt5", "Under ₹5 lakh", 4), ("5_15", "₹5–15 lakh", 12), ("15_30", "₹15–30 lakh", 18),
        ("30_60", "₹30–60 lakh", 20), ("60p", "Above ₹60 lakh", 20), ("unknown", "Not sure", 2)],
     "Ask for one number for everything: purchase/setup, deposit, interiors and first months."),
    ("money", "Where will the money come from, and is it ready?", [
        ("savings_ready", "Own savings — ready now", 20), ("savings_soon", "Own savings — in 1–3 months", 15),
        ("family", "Family / partners — already agreed", 14), ("loan_ok", "Loan — already sanctioned", 9),
        ("loan_plan", "Loan — not applied yet", 4), ("unsure", "Not sure yet", 2)],
     "Savings are safest; a loan adds an EMI from month one."),
    ("runway", "Apart from this, can you keep money to run the salon for 6 months — rent, salaries — even if sales "
               "are slow?", [
        ("yes", "Yes — 6 months or more", 20), ("partly", "Partly — about 3 months", 10),
        ("no", "No — it must run from sales", 0), ("never_thought", "Hasn't thought about it", 4)],
     "Most new salons take 4–8 months to break even."),
    ("operator", "Who will run the salon day to day?", [
        ("me_full", "Me, full-time", 20), ("me_part", "Me part-time, with a manager", 16),
        ("family", "A family member", 13), ("manager", "A hired manager — I'll only invest", 9),
        ("undecided", "Not decided", 4)],
     "Owner involvement is the biggest predictor of success."),
]
SCREEN_BY_KEY = {k: {"q": q, "opts": {v: (l, p) for v, l, p in opts}, "options": opts, "hint": h}
                 for k, q, opts, h in SCREEN}
VERDICTS = {"pursue": ("Pursue", "ok", "Serious investor. Send the investor form today and offer a call with Vikas."),
            "nurture": ("Nurture", "warn", "Promising but not ready. Send the form, follow up in 2–4 weeks, and help "
                                           "close the gaps below."),
            "not_now": ("Not now", "grey", "Thank them and keep them on the nurture list. Don't spend more time now.")}


def screen_result(answers: dict) -> dict:
    pts, gaps = 0, []
    for k, _q, _o, _h in SCREEN:
        v = answers.get(k)
        if v in SCREEN_BY_KEY[k]["opts"]:
            pts += SCREEN_BY_KEY[k]["opts"][v][1]
    score = round(pts)  # out of 100
    goal, money, runway, budget = (answers.get(k) for k in ("goal", "money", "runway", "budget"))
    if runway in ("no", "never_thought"):
        gaps.append("No money kept aside to run the salon for the first months")
    if money in ("loan_plan", "unsure"):
        gaps.append("Funding not arranged yet")
    if money in ("loan_ok", "loan_plan"):
        gaps.append("Depends on a loan — EMI starts before the salon earns")
    if budget in ("lt5", "unknown") and goal in ("buy_running", "new_salon", "franchise"):
        gaps.append("Budget may be too small for this plan")
    if goal in ("exploring", "support_only"):
        gaps.append("Not ready to invest yet")
    if answers.get("operator") in ("manager", "undecided"):
        gaps.append("Owner won't run it — needs a strong manager and support")
    verdict = "pursue" if score >= 65 else "nurture" if score >= 40 else "not_now"
    if verdict == "pursue" and (goal in ("exploring", "support_only") or (money in ("loan_plan", "unsure")
                                                                         and runway in ("no", "never_thought"))):
        verdict = "nurture"
    if money in ("loan_plan", "unsure") and runway in ("no", "never_thought"):
        verdict = "not_now"  # no money arranged and nothing to survive the first months on
    return {"score": score, "verdict": verdict, "gaps": gaps}


def profile(db, opp_id: int) -> dict:
    row = db.one("SELECT * FROM investor_profiles WHERE opportunity_id = ?", (opp_id,))
    if not row:
        return {"opportunity_id": opp_id, "screen": {}, "screen_score": None, "screen_verdict": None,
                "form_status": "not_sent", "office_visit_status": "not_booked", "reg_status": "not_due",
                "reg_fee": REG_FEE, "exists": False}
    row = dict(row)
    row["screen"] = json.loads(row["screen_answers"] or "{}")
    row["exists"] = True
    return row


def _upsert(db, opp_id, actor_id, values):
    values = dict(values, updated_by=actor_id, updated_at=now_iso())
    if db.scalar("SELECT id FROM investor_profiles WHERE opportunity_id = ?", (opp_id,)):
        sets = ", ".join(f"{k} = ?" for k in values)
        db.execute(f"UPDATE investor_profiles SET {sets} WHERE opportunity_id = ?", (*values.values(), opp_id))
    else:
        db.insert("investor_profiles", {"opportunity_id": opp_id, **values})


def _need(db, actor, cap):
    if not user_can(db, actor, cap):
        raise PermissionDenied(cap)


def save_screening(db, actor, opp, form) -> dict:
    _need(db, actor, "crm.edit")
    answers = {k: form.get(k) for k, *_ in SCREEN if form.get(k) in SCREEN_BY_KEY[k]["opts"]}
    if len(answers) < 5:
        raise ValidationError({"screen": "Answer all 5 questions (pick 'Not sure' if they don't know)."})
    res = screen_result(answers)
    with db:
        _upsert(db, opp["id"], actor["id"], {"screen_answers": json.dumps(answers), "screen_score": res["score"],
                                             "screen_verdict": res["verdict"],
                                             "screen_note": (form.get("screen_note") or "").strip()[:1000] or None,
                                             "screened_by": actor["id"], "screened_at": now_iso()})
        T.add_activity(db, actor["id"], "opportunity", opp["id"], "data_update",
                       f"Screening: {VERDICTS[res['verdict']][0]} ({res['score']}/100)"
                       + (f" — {'; '.join(res['gaps'])}" if res["gaps"] else ""))
        audit.record(db, actor["id"], "investor.screened", "opportunity", opp["id"],
                     {"score": res["score"], "verdict": res["verdict"]})
    return res


# -- 2. The investor form (no prices) -------------------------------------------------------------------------------
_q = P._q
from .geo import RADIUS_CHOICES  # noqa: E402
GOALS = [l for _v, l, _p in SCREEN[0][2]]
SECTIONS = [
    ("you", "About you", "", [
        _q("full_name", "Your name"),
        _q("phone", "Mobile number", "phone"),
        _q("email", "Email", "email", required=False),
        _q("city", "City you live in"),
        _q("age_band", "Your age", "choice", ["Under 25", "25–35", "35–45", "45–55", "55+"]),
        _q("occupation", "What do you do today?", "choice", ["Salaried job", "Own business",
           "Professional (doctor, CA, engineer…)", "Homemaker", "Student", "Retired", "Working abroad (NRI)"]),
        _q("keep_job", "Will you continue your current job or business?", "choice",
           ["Yes — I'll continue it", "No — the salon will be my full-time work", "Not decided"]),
    ]),
    ("plan", "What you want to do", "", [
        _q("goal", "What would you like to do?", "choice", GOALS),
        _q("why_salon", "Why the salon business? What attracts you to it?", "long",
           hint="A line or two is enough — e.g. 'steady cash business', 'my wife is a beautician'."),
        _q("salon_type", "What kind of salon?", "multi", ["Unisex", "Men's salon", "Women's salon / beauty parlour",
           "Luxury / premium", "Family salon", "Spa / skin clinic", "Not sure yet"]),
        _q("areas", "Which areas or localities do you prefer?", hint="e.g. Kondapur, Gachibowli — or 'any good area'"),
        _q("radius", "How far from those areas is OK?", "choice", [l for _, l in RADIUS_CHOICES], required=False),
        _q("size", "What size of salon?", "choice", ["2–4 chairs", "5–8 chairs", "9–12 chairs", "More than 12",
                                                     "Not sure"]),
        _q("start_when", "When do you want to start?", "choice", ["Within 3 months", "3–6 months", "6–12 months",
                                                                  "Just exploring"]),
    ]),
    ("experience", "Your experience", "Helps us know how much support you'll need — there are no wrong answers.", [
        _q("salon_experience", "Your experience with salons", "choice", ["I own / owned a salon",
           "I work / worked in a salon", "I did a beauty or hair course", "No salon experience"]),
        _q("business_experience", "Your business experience", "choice", ["I run a business now",
           "I ran a business before", "I managed a team at work", "No business experience"]),
        _q("managed_staff", "Largest team you have managed", "choice", ["None", "1–5 people", "6–20 people",
                                                                        "More than 20"]),
        _q("days_per_week", "How much time can you give the salon?", "choice", ["Every day, full-time",
           "3–5 days a week", "1–2 days a week", "Only weekends / remotely"]),
        _q("strengths", "Where are you strong? (select all that apply)", "multi", ["Sales & customers",
           "Managing people", "Money & accounts", "Marketing & social media", "Discipline & daily operations",
           "Hair / beauty skills"], required=False),
        _q("need_help", "Where would you want our help? (select all that apply)", "multi", ["Choosing the right "
           "salon to buy", "Location & rent negotiation", "Interiors & setup", "Hiring staff", "Training staff",
           "Marketing & launch", "Day-to-day operations", "Billing & accounts (software)"], required=False),
    ]),
    ("money", "Money", "Be as accurate as you can — it decides what is safe for you.", [
        _q("total_budget", "Total amount you can put in, everything included", "money",
           hint="One rough number is fine — e.g. 25L. Include buying / setting up, deposit and interiors."),
        _q("budget_covers", "What should this amount cover? (select all that apply)", "multi",
           ["Buying the salon / setup cost", "Interiors & equipment", "Rent advance / deposit",
            "First stock of products", "Running costs of the first months"]),
        _q("source", "Where will the money come from? (select all that apply)", "multi", ["Own savings", "Family",
           "Partners / friends", "Bank or personal loan", "Gold loan", "Selling property or other assets"]),
        _q("loan_amount", "Loan amount (if any)", "money", required=False),
        _q("emi", "Monthly EMI you will pay (if any)", "money", required=False,
           hint="Helps us check the salon won't have to pay your EMI from day one."),
        _q("money_ready", "Is the money ready?", "choice", ["Ready now", "In 1–3 months", "In 3–6 months",
                                                            "Not arranged yet"]),
        _q("runway", "Apart from the amount above, do you have money to run the salon for 6 months — rent, "
           "salaries, products — even if sales are slow?", "choice", ["Yes — 6 months or more",
           "Partly — 3 to 6 months", "Less than 3 months", "No — it must run from sales"],
           hint="Most salons take 4–8 months to earn steadily. This money keeps you safe until then."),
        _q("runway_amount", "How much is kept aside for running costs?", "money", required=False),
        _q("need_income", "Do you need income from the salon for your household?", "choice",
           ["Yes — from the first month", "After about 6 months", "After about a year",
            "No — I don't depend on it"]),
        _q("household_monthly", "Your monthly household expenses (approximate)", "money", required=False,
           hint="Optional — only to make sure the plan never puts your home budget under pressure."),
        _q("other_income", "Other income while the salon grows", "choice", ["My salary / business continues",
           "Spouse / family income", "Rent or other income", "No other income"]),
    ]),
    ("expectations", "Expectations & risk", "", [
        _q("payback", "In how long do you expect to get your money back?", "choice", ["Within 1 year", "1–2 years",
           "2–3 years", "No fixed expectation"]),
        _q("monthly_profit", "Monthly profit you expect once it's running", "money", required=False),
        _q("if_loss", "If the salon makes a loss in the first 6 months, what would you do?", "choice",
           ["Keep funding — I'm in for the long run", "Cut costs and wait", "Sell / exit quickly", "Not sure"]),
        _q("max_loss", "How much of your money could you afford to lose without trouble?", "choice",
           ["Up to 10%", "10–25%", "More than 25%", "I can't afford any loss"],
           hint="Every business has risk. Knowing your comfort level helps us suggest a safer plan."),
        _q("decision", "Who takes the final decision?", "choice", ["Only me", "Me with my spouse / family",
                                                                   "My partners and me together"]),
        _q("aligned", "Are your family / partners on board?", "choice", ["Yes, fully", "Not discussed yet",
                                                                         "Not applicable"]),
        _q("comments", "Anything else we should know?", "long", required=False),
    ]),
]
QUESTIONS = [q for _, _, _, qs in SECTIONS for q in qs]
Q_BY_KEY = {q["key"]: q for q in QUESTIONS}


def prefill(db, opp) -> dict:
    last = latest_response(db, opp["id"])
    if last:
        return last["answers"]
    phone = opp.get("phone_normalized") or ""
    return {"full_name": opp.get("contact_name") or "", "phone": phone[3:] if phone.startswith("+91") else phone,
            "email": opp.get("contact_email") or opp.get("email") or "",
            "city": opp.get("contact_city") or opp.get("city") or ""}


def parse_answers(form) -> tuple[dict, dict]:
    return P.parse_answers(form, QUESTIONS)


def latest_response(db, opp_id: int) -> dict | None:
    row = db.one("SELECT * FROM investor_responses WHERE opportunity_id = ? ORDER BY id DESC LIMIT 1", (opp_id,))
    if not row:
        return None
    row = dict(row)
    row["answers"] = json.loads(row["answers"])
    return row


def set_form_status(db, actor, opp, status: str) -> None:
    _need(db, actor, "crm.log_activity")
    if status not in P.FORM_STATUS_LABELS:
        raise ValidationError({"status": "Unknown form status."})
    vals = {"form_status": status}
    if status == "sent":
        vals["form_sent_at"] = now_iso()
    if status == "filled":
        vals["form_filled_at"] = now_iso()
    with db:
        _upsert(db, opp["id"], actor["id"], vals)
        T.add_activity(db, actor["id"], "opportunity", opp["id"], "data_update",
                       f"Investor form marked: {P.FORM_STATUS_LABELS[status]}")


def save_response(db, opp, answers, source, actor_id=None) -> int:
    with db:
        rid = db.insert("investor_responses", {"opportunity_id": opp["id"], "source": source,
                                               "answers": json.dumps(answers, ensure_ascii=False),
                                               "created_by": actor_id, "submitted_at": now_iso()})
        _upsert(db, opp["id"], actor_id, {"form_status": "filled", "form_filled_at": now_iso()})
        T.add_activity(db, actor_id, "opportunity", opp["id"], "system",
                       ("Investor" if source == "link" else "Team (on the investor's behalf)")
                       + " filled the investor form — readiness report draft ready; next: call with Vikas")
        draft_report(db, opp["id"], rid, actor_id)
        sync_to_mandate(db, opp["id"], answers, actor_id)
        T.notify(db, opp.get("primary_owner_id"), "investor_form",
                 f"Investor form filled: {opp.get('contact_name') or opp.get('ref_code')} — book the call with Vikas",
                 f"/buyers/{opp['id']}?tab=investor")
        audit.record(db, actor_id, "investor.form_filled", "opportunity", opp["id"], {"source": source})
    P.rescore_matches(db, "buyer", opp["id"])
    return rid


def form_message(opp, link) -> str:
    first = (opp.get("contact_name") or "").split(" ")[0] or "Sir/Madam"
    return (f"Hello {first}, thank you for speaking with Salon Shastra.\n\nTo plan the right salon investment for you, "
            f"please fill this short form (about 10 minutes):\n{link}\n\nAfter that, you can speak with our founder "
            "Vikas Rachakonda for a few minutes.\n\n— Team Salon Shastra")


def report_message(opp, link) -> str:
    first = (opp.get("contact_name") or "").split(" ")[0] or "Sir/Madam"
    return (f"Hello {first}, thank you for your time with Vikas Rachakonda.\n\nYour Investor Readiness Report is ready "
            f"— it shows whether and how to invest, what to keep ready and the next steps:\n{link}\n\n"
            "— Team Salon Shastra")


# -- 3. Office visit & registration -------------------------------------------------------------------------------
def save_office(db, actor, opp, form) -> None:
    _need(db, actor, "crm.log_activity")
    from ..timeutil import fmt_ist, parse_local_input
    prof = profile(db, opp["id"])
    vals, notes = {}, []
    when = parse_local_input(form.get("office_visit_at"))
    st = form.get("office_visit_status") or prof["office_visit_status"]
    if st not in ("not_booked", "booked", "done", "no_show", "cancelled"):
        raise ValidationError({"office": "Unknown visit status."})
    if st == "booked" and not (when or prof.get("office_visit_at")):
        raise ValidationError({"office": "Choose the date and time of the office visit."})
    if when:
        vals["office_visit_at"] = when
    if st != prof["office_visit_status"] or when:
        vals["office_visit_status"] = st
        notes.append("office visit " + {"booked": "booked" + (f" for {fmt_ist(when)}" if when else ""),
                                        "done": "done", "no_show": "missed", "cancelled": "cancelled",
                                        "not_booked": "cleared"}[st])
        if st in ("booked", "done") and prof["reg_status"] == "not_due":
            vals["reg_status"] = "due"
    reg = form.get("reg_status")
    if reg and reg != prof["reg_status"]:
        if reg not in ("not_due", "due", "paid", "waived"):
            raise ValidationError({"reg": "Unknown registration status."})
        if reg == "paid":
            mode = form.get("reg_paid_mode")
            if mode not in dict(P.PAY_MODES):
                raise ValidationError({"reg": "Choose how the registration fee was paid."})
            vals.update(reg_paid_mode=mode, reg_paid_ref=(form.get("reg_paid_ref") or "").strip()[:80] or None,
                        reg_paid_at=now_iso())
            notes.append(f"registration fee ₹{prof['reg_fee']:,} received ({dict(P.PAY_MODES)[mode]})")
        elif reg == "waived":
            notes.append("registration fee waived")
        vals["reg_status"] = reg
    note = (form.get("reg_note") or "").strip()[:300]
    if note:
        vals["reg_note"] = note
    if not vals:
        return
    with db:
        _upsert(db, opp["id"], actor["id"], vals)
        if notes:
            T.add_activity(db, actor["id"], "opportunity", opp["id"], "note", "Investor: " + "; ".join(notes))
        audit.record(db, actor["id"], "investor.office", "opportunity", opp["id"],
                     {k: v for k, v in vals.items() if k != "reg_note"})


# -- 4. Investor readiness report -----------------------------------------------------------------------------------
REPORT_RULES = "investor-readiness rules v1 (24 Nov 2026)"
VERDICT_TEXT = {
    "ready": ("Ready to invest", "Your money, time and plan are in good shape. Go ahead with the plan below."),
    "with_support": ("Ready — with Salon Shastra's support",
                     "You can invest, but success depends on closing the gaps below with our support from day one."),
    "not_yet": ("Not yet — prepare first", "Investing now carries avoidable risk. Close the gaps below first; we will "
                                           "help you get ready."),
}


def _has(a, key, *words):
    v = a.get(key)
    vals = v if isinstance(v, list) else [v or ""]
    return any(w.lower() in str(x).lower() for x in vals for w in words)


def build_report(a: dict, screen: dict | None = None) -> dict:
    """Consultant rules: money, runway, funding safety, experience, time, risk → verdict, path, conditions, steps."""
    total = P.parse_amount(a.get("total_budget"))
    runway_amt = P.parse_amount(a.get("runway_amount"))
    emi = P.parse_amount(a.get("emi"))
    house = P.parse_amount(a.get("household_monthly"))
    loan = P.parse_amount(a.get("loan_amount"))

    def clamp(x):
        return max(1, min(10, round(x)))
    capital = clamp(2 if not total else 3 if total < 5e5 else 5 if total < 15e5 else 7 if total < 30e5 else 9)
    if _has(a, "money_ready", "Ready now"):
        capital = clamp(capital + 1)
    elif _has(a, "money_ready", "Not arranged"):
        capital = clamp(capital - 2)
    runway = {"Yes — 6 months or more": 9, "Partly — 3 to 6 months": 6, "Less than 3 months": 3,
              "No — it must run from sales": 1}.get(a.get("runway"), 3)
    if _has(a, "budget_covers", "Running costs"):
        runway = clamp(runway + 1)
    funding = 8
    if _has(a, "source", "loan"):
        funding -= 3
    if _has(a, "source", "Selling property"):
        funding -= 1
    if emi and house and emi > 0.3 * house:
        funding -= 2
    if _has(a, "need_income", "first month"):
        funding -= 2
    if _has(a, "other_income", "No other income"):
        funding -= 1
    if total and loan and loan > 0.5 * total:
        funding -= 2
    funding = clamp(funding)
    exp = 2
    exp += {"I own / owned a salon": 4, "I work / worked in a salon": 3, "I did a beauty or hair course": 2}.get(
        a.get("salon_experience"), 0)
    exp += {"I run a business now": 3, "I ran a business before": 2, "I managed a team at work": 1}.get(
        a.get("business_experience"), 0)
    exp += {"6–20 people": 1, "More than 20": 1}.get(a.get("managed_staff"), 0)
    experience = clamp(exp)
    time_ = {"Every day, full-time": 9, "3–5 days a week": 7, "1–2 days a week": 4, "Only weekends / remotely": 2}.get(
        a.get("days_per_week"), 4)
    if _has(a, "keep_job", "Yes") and time_ > 6:
        time_ -= 1
    time_ = clamp(time_)
    risk = 6
    risk += {"Keep funding": 2, "Cut costs": 1, "Sell / exit": -2, "Not sure": -1}.get(
        next((k for k in ("Keep funding", "Cut costs", "Sell / exit", "Not sure") if _has(a, "if_loss", k)), ""), 0)
    risk += {"More than 25%": 1, "10–25%": 0, "Up to 10%": -1, "I can't afford any loss": -3}.get(a.get("max_loss"), 0)
    if _has(a, "payback", "Within 1 year"):
        risk -= 2
    if _has(a, "aligned", "Not discussed"):
        risk -= 1
    risk = clamp(risk)
    scores = [("Capital", capital), ("6-month running money", runway), ("Funding safety", funding),
              ("Experience", experience), ("Time & involvement", time_), ("Risk readiness", risk)]
    avg = sum(s for _, s in scores) / len(scores)

    goal = a.get("goal") or ""
    hard_stop = (runway <= 3 and (funding <= 4 or _has(a, "need_income", "first month"))) or \
        _has(a, "money_ready", "Not arranged") or (a.get("max_loss") == "I can't afford any loss" and funding <= 5) or \
        goal in ("Just exploring",)
    if hard_stop or avg < 5:
        verdict = "not_yet"
    elif avg >= 7 and runway >= 7 and funding >= 6 and experience >= 5 and time_ >= 6:
        verdict = "ready"
    else:
        verdict = "with_support"

    # Recommended path
    if goal.startswith("Wants advice"):
        path = ("Consulting first", "Start with Salon Business Audit & Diagnosis before putting money in.")
    elif verdict == "not_yet":
        path = ("Prepare, then invest", "Arrange the money and 6 months of running costs first; we'll review again.")
    elif goal.startswith("Invest money only") or time_ <= 4:
        path = ("Investor with a managed salon", "Buy a running salon with a proven team and manager, with Salon "
                                                  "Shastra's Growth Partner Program for monthly reviews.")
    elif goal.startswith("Open a new salon"):
        path = ("New salon with launch support", "Feasibility, location & rent negotiation, interiors, hiring and a "
                                                 "90-day launch plan with Salon Shastra.")
    elif goal.startswith("Take a franchise"):
        path = ("Franchise — check carefully", "Compare the franchise fee and royalty with an independent salon; we "
                                               "review the agreement and numbers with you.")
    else:
        path = ("Buy a running salon", "A salon that already has customers and staff is the lower-risk start; we "
                                       "shortlist verified salons that fit your budget and area.")

    strengths, risks, conditions = [], [], []
    if capital >= 7:
        strengths.append("Strong capital for the plan")
    if runway >= 7:
        strengths.append("Money kept aside to run the salon through the slow first months")
    if _has(a, "source", "Own savings") and not _has(a, "source", "loan"):
        strengths.append("Investing own savings — no EMI pressure")
    if experience >= 6:
        strengths.append("Relevant salon / business experience")
    if time_ >= 7:
        strengths.append("Can give the salon real time every week")
    if _has(a, "if_loss", "Keep funding"):
        strengths.append("Long-term mindset — ready to support the first months")
    if _has(a, "strengths", "Managing people"):
        strengths.append("Comfortable managing people")
    if runway <= 5:
        risks.append("Not enough money kept aside to run the salon for 6 months")
        conditions.append("Keep at least 6 months of running costs (rent + salaries + products) aside before you "
                          "start")
    if _has(a, "source", "loan"):
        risks.append("Loan-funded: the EMI starts before the salon earns")
        conditions.append("Keep the loan below half of the total and the EMI covered by other income")
    if _has(a, "need_income", "first month"):
        risks.append("Needs income from month one — salons usually take 4–8 months to break even")
        conditions.append("Arrange 6 months of household expenses from other sources")
    if experience <= 4:
        risks.append("Little salon or business experience")
        conditions.append("Hire an experienced manager and do the owner training before opening")
    if time_ <= 4:
        risks.append("Limited time for the salon")
        conditions.append("Put a trusted manager in charge with weekly reviews and software reporting (ChairPe)")
    if _has(a, "payback", "Within 1 year"):
        risks.append("Expects the money back within a year — usually it takes 2–3 years")
    if a.get("max_loss") == "I can't afford any loss":
        risks.append("Cannot afford any loss")
        conditions.append("Invest only money you can afford to have locked in for 2–3 years")
    if _has(a, "aligned", "Not discussed"):
        risks.append("Family / partners not yet on board")
        conditions.append("Agree the plan with your family / partners before committing")
    if _has(a, "money_ready", "Not arranged"):
        risks.append("Money not arranged yet")

    split = None
    if total:
        keep = round(total * 0.3 / 10000) * 10000
        split = (f"Of your ₹{total / 1e5:.1f} lakh, keep about ₹{keep / 1e5:.1f} lakh (≈30%) for the first 6 months "
                 f"of running costs and spend about ₹{(total - keep) / 1e5:.1f} lakh on buying / setting up.")
        if runway_amt and runway_amt >= 0.25 * total:
            split = (f"You already keep ₹{runway_amt / 1e5:.1f} lakh aside for running costs — good. Use up to "
                     f"₹{total / 1e5:.1f} lakh for buying / setting up.")

    steps = [{"title": "Visit our office", "why": "Meet the team, see real salon numbers and finalise your plan.",
              "when": "This week"}]
    if verdict == "not_yet":
        steps = [{"title": c, "why": "", "when": "Before investing"} for c in conditions[:3]] + [
            {"title": "Review again with Salon Shastra", "why": "When these are in place, we re-check the plan.",
             "when": "In 1–3 months"}]
    else:
        steps += [{"title": path[0], "why": path[1], "when": "Next 30 days"}]
        steps += [{"title": c, "why": "", "when": "Before you sign"} for c in conditions[:3]]
    name = a.get("full_name") or "the investor"
    summary = (f"{name.split(' ')[0]} scores {avg:.1f}/10 overall — strongest in "
               f"{max(scores, key=lambda s: s[1])[0].lower()}, weakest in {min(scores, key=lambda s: s[1])[0].lower()}.")
    facts = [("Plan", goal or "—"), ("Total budget", f"₹{total / 1e5:.1f} lakh" if total else "—"),
             ("Money source", ", ".join(a.get("source") or []) or "—"), ("Money ready", a.get("money_ready") or "—"),
             ("6-month running money", a.get("runway") or "—"), ("Time for the salon", a.get("days_per_week") or "—"),
             ("Preferred areas", a.get("areas") or "—"), ("Start", a.get("start_when") or "—")]
    return {"rules": REPORT_RULES, "name": name, "verdict": verdict, "verdict_label": VERDICT_TEXT[verdict][0],
            "verdict_text": VERDICT_TEXT[verdict][1], "path": path[0], "path_text": path[1], "summary": summary,
            "scores": scores, "facts": facts, "strengths": strengths[:6] or ["Clear interest in the salon business"],
            "risks": risks[:6], "conditions": conditions[:5], "split": split, "steps": steps[:6]}


def draft_report(db, opp_id, response_id, actor_id) -> int:
    resp = db.one("SELECT * FROM investor_responses WHERE id = ?", (response_id,)) if response_id else \
        db.one("SELECT * FROM investor_responses WHERE opportunity_id = ? ORDER BY id DESC LIMIT 1", (opp_id,))
    if not resp:
        raise ValidationError({"report": "The investor form has not been filled yet."})
    content = build_report(json.loads(resp["answers"]), profile(db, opp_id)["screen"])
    version = (db.scalar("SELECT MAX(version) FROM investor_reports WHERE opportunity_id = ?", (opp_id,)) or 0) + 1
    rid = db.insert("investor_reports", {"opportunity_id": opp_id, "response_id": resp["id"], "version": version,
                                         "content": json.dumps(content, ensure_ascii=False), "status": "draft",
                                         "created_by": actor_id, "created_at": now_iso(), "updated_at": now_iso()})
    from . import area_insights as AI
    a = json.loads(resp["answers"])
    place = (re.split(r"[,/;]| and ", a.get("areas") or "")[0].strip() or None)
    radius = {lbl: k for k, lbl in RADIUS_CHOICES}.get(a.get("radius")) or 3
    db.after_commit(lambda: AI.schedule("investor_reports", rid, place, min(radius, 10), True))  # their area
    return rid


def latest_report(db, opp_id, approved_only=False):
    row = db.one("SELECT r.*, u.full_name AS approver_name FROM investor_reports r LEFT JOIN users u ON u.id = "
                 "r.approved_by WHERE r.opportunity_id = ?" + (" AND r.status = 'approved'" if approved_only else "")
                 + " ORDER BY r.version DESC LIMIT 1", (opp_id,))
    if not row:
        return None
    row = dict(row)
    row["c"] = json.loads(row["content"])
    return row


def save_report(db, actor, opp, report_id, form, approve: bool) -> None:
    _need(db, actor, "crm.edit")
    rep = db.one("SELECT * FROM investor_reports WHERE id = ? AND opportunity_id = ?", (report_id, opp["id"]))
    if not rep:
        raise ValidationError({"report": "Report not found."})
    c = json.loads(rep["content"])
    v = form.get("verdict")
    if v in VERDICT_TEXT and v != c["verdict"]:
        c.update(verdict=v, verdict_label=VERDICT_TEXT[v][0], verdict_text=VERDICT_TEXT[v][1])
    c["path"] = (form.get("path") or "").strip()[:80] or c["path"]
    c["path_text"] = (form.get("path_text") or "").strip()[:400] or c["path_text"]
    c["summary"] = (form.get("summary") or "").strip()[:800] or c["summary"]
    c["split"] = (form.get("split") or "").strip()[:400] or None
    for k in ("strengths", "risks", "conditions"):
        c[k] = P._lines(form.get(k), 8)
    steps = []
    for i in range(8):
        t = (form.get(f"step_title_{i}") or "").strip()[:140]
        if t:
            steps.append({"title": t, "why": (form.get(f"step_why_{i}") or "").strip()[:240],
                          "when": (form.get(f"step_when_{i}") or "").strip()[:40]})
    c["steps"] = steps
    vikas = (form.get("vikas_note") or "").strip()[:800]
    c["vikas_note"] = vikas or None
    if approve and not steps:
        raise ValidationError({"report": "Add at least one next step before approving."})
    with db:
        vals = {"content": json.dumps(c, ensure_ascii=False), "updated_at": now_iso()}
        if rep["status"] == "approved" and not approve:
            db.insert("investor_reports", {"opportunity_id": rep["opportunity_id"], "response_id": rep["response_id"],
                                           "version": rep["version"] + 1, "status": "draft",
                                           "created_by": actor["id"], "created_at": now_iso(), **vals})
        else:
            if approve:
                vals.update(status="approved", approved_by=actor["id"], approved_at=now_iso())
            sets = ", ".join(f"{k} = ?" for k in vals)
            db.execute(f"UPDATE investor_reports SET {sets} WHERE id = ?", (*vals.values(), report_id))
        T.add_activity(db, actor["id"], "opportunity", opp["id"], "approval" if approve else "data_update",
                       f"Investor readiness report v{rep['version']} " + ("approved" if approve else "edited")
                       + f" — {c['verdict_label']}")
        audit.record(db, actor["id"], "investor.report_" + ("approved" if approve else "edited"), "opportunity",
                     opp["id"], {"report": report_id, "verdict": c["verdict"]})


def redraft(db, actor, opp) -> int:
    _need(db, actor, "crm.edit")
    with db:
        return draft_report(db, opp["id"], None, actor["id"])


def mark_sent(db, actor, opp) -> None:
    _need(db, actor, "crm.log_activity")
    rep = latest_report(db, opp["id"], approved_only=True)
    if not rep:
        raise ValidationError({"report": "Approve the report first."})
    with db:
        db.execute("UPDATE investor_reports SET sent_at = ?, sent_by = ? WHERE id = ?", (now_iso(), actor["id"],
                                                                                        rep["id"]))
        T.add_activity(db, actor["id"], "opportunity", opp["id"], "note",
                       f"Investor readiness report v{rep['version']} sent ({rep['c']['verdict_label']})")


# -- Overview / record context ----------------------------------------------------------------------------------------
def record_context(db, opp_id) -> dict:
    return {"inv": profile(db, opp_id), "inv_response": latest_response(db, opp_id),
            "inv_report": latest_report(db, opp_id), "inv_bookings": P.bookings(db, opp_id),
            "inv_slots": P.open_slots(db, 14), "inv_consultant": P.consultant(db)}


def overview(db, verdict: str = "") -> dict:
    rows = db.all(
        "SELECT o.id, o.ref_code, o.stage, o.created_at, c.full_name AS contact_name, c.phone_normalized, "
        "u.full_name AS owner_name, p.screen_score, p.screen_verdict, p.form_status, p.office_visit_status, "
        "p.office_visit_at, p.reg_status, m.need_type, m.all_in_max, "
        "(SELECT r.status FROM investor_reports r WHERE r.opportunity_id = o.id ORDER BY r.version DESC LIMIT 1) "
        "AS report_status, (SELECT MAX(r.sent_at) FROM investor_reports r WHERE r.opportunity_id = o.id) AS sent_at, "
        "(SELECT r.content FROM investor_reports r WHERE r.opportunity_id = o.id ORDER BY r.version DESC LIMIT 1) "
        "AS report_content, "
        "(SELECT vb.status FROM vikas_bookings vb WHERE vb.opportunity_id = o.id ORDER BY vb.id DESC LIMIT 1) "
        "AS vikas_status FROM opportunities o JOIN contacts c ON c.id = o.contact_id "
        "LEFT JOIN users u ON u.id = o.primary_owner_id LEFT JOIN investor_profiles p ON p.opportunity_id = o.id "
        "LEFT JOIN buyer_mandates m ON m.opportunity_id = o.id WHERE o.kind = 'buyer' AND o.status <> 'closed' "
        "ORDER BY COALESCE(p.screen_score, -1) DESC, o.created_at DESC LIMIT 400")
    out, counts = [], {"pursue": 0, "nurture": 0, "not_now": 0, "unscreened": 0}
    for r in rows:
        r = dict(r)
        counts[r["screen_verdict"] or "unscreened"] += 1
        r["report_verdict"] = json.loads(r.pop("report_content"))["verdict_label"] if r.get("report_content") else None
        r["form_status"] = r["form_status"] or "not_sent"
        if verdict and (r["screen_verdict"] or "unscreened") != verdict:
            continue
        out.append(r)
    reg = db.one("SELECT COALESCE(SUM(CASE WHEN reg_status = 'paid' THEN reg_fee ELSE 0 END), 0) AS collected, "
                 "SUM(CASE WHEN reg_status = 'paid' THEN 1 ELSE 0 END) AS paid, "
                 "SUM(CASE WHEN reg_status = 'due' THEN 1 ELSE 0 END) AS due FROM investor_profiles")
    return {"rows": out, "counts": counts, "reg": reg}


def _clean(s):
    return re.sub(r"\s+", " ", str(s or "")).strip()


# -- Public (open) Investor form ----------------------------------------------------------------------------------------
GOAL_TO_NEED = {"Buy a running salon": "acquire_operating", "Open a new salon": "new_salon",
                "Take a franchise": "franchise", "Invest money only — someone else runs it": "passive_invest",
                "Wants advice / support only, not investing yet": "exploring", "Just exploring": "exploring"}
START_TO_WINDOW = {"Within 3 months": "90d", "3–6 months": "180d", "6–12 months": "later", "Just exploring": "later"}


def screen_from_form(a: dict) -> dict:
    """The 5 screening answers, read from the investor's own form (the team can correct them after the call)."""
    goal = {"Buy a running salon": "buy_running", "Open a new salon": "new_salon", "Take a franchise": "franchise",
            "Invest money only — someone else runs it": "invest_only",
            "Wants advice / support only, not investing yet": "support_only", "Just exploring": "exploring"}.get(
        a.get("goal"), "exploring")
    total = P.parse_amount(a.get("total_budget"))
    budget = ("unknown" if not total else "lt5" if total < 5e5 else "5_15" if total < 15e5 else "15_30"
              if total < 30e5 else "30_60" if total < 60e5 else "60p")
    src = a.get("source") or []
    ready = a.get("money_ready") or ""
    loan = any("loan" in s.lower() for s in src)
    if loan:
        money = "loan_plan" if ready in ("Not arranged yet", "In 3–6 months") else "loan_ok"
    elif "Own savings" in src:
        money = "savings_ready" if ready == "Ready now" else "savings_soon" if ready else "unsure"
    elif src:
        money = "family" if ready != "Not arranged yet" else "unsure"
    else:
        money = "unsure"
    runway = {"Yes — 6 months or more": "yes", "Partly — 3 to 6 months": "partly", "Less than 3 months": "partly",
              "No — it must run from sales": "no"}.get(a.get("runway"), "never_thought")
    days = a.get("days_per_week") or ""
    operator = ("manager" if goal == "invest_only" or days == "Only weekends / remotely" else
                "me_full" if days == "Every day, full-time" else "me_part" if days else "undecided")
    return {"goal": goal, "budget": budget, "money": money, "runway": runway, "operator": operator}


def submit_public(db, answers: dict, consent: bool) -> int:
    """Create the buyer/investor record from the open Investor form, pre-screen it (Pursue / Nurture / Not now from
    their own answers), save the answers and draft the readiness report."""
    from . import buyers as B
    total = P.parse_amount(answers.get("total_budget"))
    payload = {"full_name": answers.get("full_name"), "phone": answers.get("phone"), "city": answers.get("city"),
               "locality": re.split(r"[,/;]", answers.get("areas") or "")[0].strip()[:80], "email": answers.get("email"),
               "need_type": GOAL_TO_NEED.get(answers.get("goal"), "exploring"),
               "all_in_max": str(int(total)) if total else "",
               "purchase_window": START_TO_WINDOW.get(answers.get("start_when"), "later"),
               "preferred_city": answers.get("city"), "consent_contact": "1" if consent else ""}
    with db:
        opp_id = B.submit_respondent_form(db, payload, source="landing_page")
        db.execute("UPDATE opportunities SET source_detail = ? WHERE id = ?", ("Investor form (public)", opp_id))
        opp = B.load(db, opp_id)
        scr = screen_from_form(answers)
        res = screen_result(scr)
        _upsert(db, opp_id, None, {"screen_answers": json.dumps(scr), "screen_score": res["score"],
                                   "screen_verdict": res["verdict"], "screen_note": "From the investor's own form — "
                                   "confirm on the call.", "screened_at": now_iso()})
        save_response(db, opp, answers, "link")
    return opp_id


# -- Form → buyer requirements ------------------------------------------------------------------------------------
# The investor's answers fill the buyer record's requirement fields that are still EMPTY (never overwriting what the
# team entered), add the areas they named as preferred localities and the salon type as a format preference — so the
# Match Desk can score them against salon owners straight away.
SIZE_TO_CHAIRS = {"2–4 chairs": (2, 4), "5–8 chairs": (5, 8), "9–12 chairs": (9, 12), "More than 12": (12, None)}
TYPE_TO_FORMAT = {"Unisex": "unisex", "Family salon": "unisex", "Men's salon": "men",
                  "Women's salon / beauty parlour": "women"}
TYPE_TO_POSITIONING = {"Luxury / premium": "premium"}


def mandate_values_from_answers(a: dict) -> dict:
    out = {"need_type": GOAL_TO_NEED.get(a.get("goal")),
           "purchase_window": START_TO_WINDOW.get(a.get("start_when")),
           "preferred_city": (a.get("city") or "").strip() or None,
           "decision_timeline": a.get("start_when")}
    total = P.parse_amount(a.get("total_budget"))
    if total and total >= 50000:
        out["all_in_max"] = int(total)
    decision = a.get("decision") or ""
    out["decision_maker"] = {"Only me": "yes"}.get(decision, "shared" if decision else None)
    out["investor_type"] = ("partnership" if decision.startswith("My partners") else "individual" if decision else None)
    exp = a.get("salon_experience") or ""
    out["salon_background"] = ("operator" if exp.startswith("I own") else "industry_other" if exp.startswith("I ")
                               else "none" if exp else None)
    src = a.get("source") or []
    loan = [s for s in src if "loan" in s.lower()]
    if src:
        out["funding_source"] = ("loan" if loan and len(loan) == len(src) else "mixed" if loan and len(src) > 1
                                 else "investor_group" if src == ["Partners / friends"] else "self"
                                 if len(src) == 1 else "mixed")
        if not loan:
            out["finance_dependence"] = "none"
        else:
            amt = P.parse_amount(a.get("loan_amount"))
            out["finance_dependence"] = "full" if (len(loan) == len(src) or (amt and total and amt >= 0.7 * total)) \
                else "partial"
    ready = a.get("money_ready") or ""
    out["cash_ready"] = "now" if ready == "Ready now" else "planned" if ready else None
    if ready == "Ready now" and total:
        out["cash_ready_amount"] = int(total)
    days = a.get("days_per_week") or ""
    out["involvement"] = ("absentee" if out["need_type"] == "passive_invest" or days == "Only weekends / remotely"
                          else "owner_operator" if days == "Every day, full-time" else "partly" if days else None)
    out["max_commute_km"] = {l: k for k, l in RADIUS_CHOICES}.get(a.get("radius"))
    lo, hi = SIZE_TO_CHAIRS.get(a.get("size"), (None, None))
    out["chairs_min"], out["chairs_max"] = lo, hi
    if a.get("need_help"):
        out["transition_support_need"] = "yes"
    return {k: v for k, v in out.items() if v not in (None, "")}


def sync_to_mandate(db, opp_id: int, answers: dict, actor_id=None) -> list[str]:
    from . import buyer_fields as BF
    from . import buyers as B
    from . import sellers as S
    m = db.one("SELECT * FROM buyer_mandates WHERE opportunity_id = ?", (opp_id,))
    if not m:
        return []
    prov = B.provenance(db, m["id"])
    fill = {}
    for k, v in mandate_values_from_answers(answers).items():
        fd = BF.BY_KEY.get(k)
        if not fd or m.get(k) not in (None, "") or k in prov:
            continue
        if fd.ftype == "enum" and v not in [o[0] for o in fd.options]:
            continue
        try:
            fill[k] = (S._parse_one(fd, str(v)), "value")
        except ValueError:
            continue
    changed = B._apply_values(db, actor_id, m["id"], fill, source="investor_form", evidence="buyer_declared") \
        if fill else []
    labels = [BF.BY_KEY[k].label for k in changed]
    # preferred areas: add the ones not listed yet (nothing the team listed or excluded is changed)
    city = (answers.get("city") or m.get("preferred_city") or "").strip()
    areas = [x.strip() for x in re.split(r"[,/;]| and ", answers.get("areas") or "") if 2 <= len(x.strip()) <= 60]
    listed = {r["locality"].lower() for r in db.all("SELECT locality FROM buyer_localities WHERE mandate_id = ?",
                                                    (m["id"],))}
    new_areas = [x for x in dict.fromkeys(areas[:8]) if x.lower() not in listed]
    if city and new_areas:
        rank = db.scalar("SELECT MAX(rank_order) FROM buyer_localities WHERE mandate_id = ? AND kind = 'preferred'",
                         (m["id"],)) or 0
        for loc in new_areas:
            rank += 1
            db.insert("buyer_localities", {"mandate_id": m["id"], "kind": "preferred", "city": city, "locality": loc,
                                           "rank_order": rank, "note": "From the investor form", "added_by": actor_id,
                                           "added_at": now_iso()})
        labels.append("preferred areas")
    types = answers.get("salon_type") or []
    types = [types] if isinstance(types, str) else types
    wanted = [("gender_format", TYPE_TO_FORMAT[t]) for t in types if t in TYPE_TO_FORMAT] +              [("positioning", TYPE_TO_POSITIONING[t]) for t in types if t in TYPE_TO_POSITIONING]
    have = {r["dimension"] for r in db.all("SELECT dimension FROM buyer_preferences WHERE mandate_id = ?", (m["id"],))}
    version = db.scalar("SELECT data_version FROM buyer_mandates WHERE id = ?", (m["id"],)) + 1
    added = False
    for dim, choice in dict.fromkeys(wanted):
        if dim not in have and B._set_importance(db, actor_id, m["id"], dim, choice, "prefer", version):
            added = True
    if added:
        labels.append("salon format preference")
    if labels and len(labels) > len(changed):
        B._bump(db, m["id"])
    if labels:
        T.add_activity(db, actor_id, "opportunity", opp_id, "data_update",
                       "Filled from the investor form (empty fields only): " + ", ".join(labels))
    return labels
