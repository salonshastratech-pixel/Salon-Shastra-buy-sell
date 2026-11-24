"""Buyers & investors: screening, investor form, call with Vikas, office visit & registration, readiness report."""
import re
from datetime import timedelta

from conftest import user

from teamos.domain import buyers as B
from teamos.domain import investor as I
from teamos.domain import pilot as P
from teamos.timeutil import IST, now_utc

FEE = re.compile(r"(?<![\d,.])5,?000(?![\d,])")  # the registration fee, as a standalone number
STRONG = {
    "full_name": "Arjun Rao", "phone": "9100011001", "email": "arjun@example.com", "city": "Hyderabad",
    "age_band": "35–45", "occupation": "Own business", "keep_job": "No — the salon will be my full-time work",
    "goal": "Buy a running salon", "why_salon": "Steady cash business", "salon_type": ["Unisex"],
    "areas": "Kondapur", "size": "5–8 chairs", "start_when": "Within 3 months",
    "salon_experience": "I own / owned a salon", "business_experience": "I run a business now",
    "managed_staff": "6–20 people", "days_per_week": "Every day, full-time", "total_budget": "30L",
    "budget_covers": ["Buying the salon / setup cost", "Running costs of the first months"], "source": ["Own savings"],
    "money_ready": "Ready now", "runway": "Yes — 6 months or more", "need_income": "After about a year",
    "other_income": "Spouse / family income", "payback": "2–3 years", "if_loss": "Keep funding — I'm in for the long run",
    "max_loss": "10–25%", "decision": "Only me", "aligned": "Yes, fully",
}


def _buyer(db, phone="9100011001"):
    admin = user(db, "admin@t.test")
    return B.create_lead(db, admin, {"full_name": "Arjun Rao", "phone": phone, "city": "Hyderabad",
                                     "source": "referral", "consent_contact": "1",
                                     "owner_id": str(user(db, "buyer@t.test")["id"]),
                                     "need_type": "acquire_operating", "decision_maker": "yes",
                                     "all_in_max": "30L", "purchase_window": "90d"})["opportunity_id"]


def test_screening_verdicts():
    assert I.screen_result({"goal": "buy_running", "budget": "15_30", "money": "savings_ready", "runway": "yes",
                            "operator": "me_full"})["verdict"] == "pursue"
    r = I.screen_result({"goal": "new_salon", "budget": "5_15", "money": "loan_plan", "runway": "no",
                         "operator": "manager"})
    assert r["verdict"] == "not_now" and any("Funding not arranged" in g for g in r["gaps"])
    assert I.screen_result({"goal": "exploring", "budget": "60p", "money": "savings_ready", "runway": "yes",
                            "operator": "me_full"})["verdict"] == "nurture"  # rich but only exploring


def test_screening_on_the_record(db, login):
    opp = _buyer(db)
    c = login("buyer@t.test")
    c.post(f"/buyers/{opp}/investor/screen", {"goal": "buy_running"})  # incomplete → refused
    assert not I.profile(db, opp)["exists"]
    c.post(f"/buyers/{opp}/investor/screen", {"goal": "buy_running", "budget": "15_30", "money": "savings_ready",
                                              "runway": "yes", "operator": "me_full", "screen_note": "Very keen"})
    prof = I.profile(db, opp)
    assert prof["screen_verdict"] == "pursue" and prof["screen_score"] >= 65
    page = c.get(f"/buyers/{opp}?tab=investor").data.decode()
    assert "Serious investor" in page and "Pursue" in page
    assert "Pursue" in c.get("/investors").data.decode()


def test_form_by_link_then_book_vikas_no_prices_shown(db, login, app):
    opp = _buyer(db)
    c = login("buyer@t.test")
    page = c.post(f"/buyers/{opp}/investor/link", {"purpose": "form"}).data.decode()
    token = re.search(r"/s/([A-Za-z0-9_\-]+)", page).group(1)
    assert I.profile(db, opp)["form_status"] == "sent"
    pub = app.test_client()
    form = pub.get(f"/s/{token}").data.decode()
    assert "Planning a salon investment?" in form and "6 months" in form
    assert not FEE.search(form) and "registration" not in form.lower()  # no prices on the form
    admin = login("admin@t.test")
    day = (now_utc().astimezone(IST) + timedelta(days=2)).strftime("%Y-%m-%d")
    admin.post("/pilot/vikas", {"day": day, "times": "11:00", "minutes": "20", "mode": "phone"})
    done = pub.post(f"/s/{token}", data=STRONG).data.decode()
    assert "Pick a time to talk with Vikas Rachakonda" in done and "Book this time" in done
    assert I.profile(db, opp)["form_status"] == "filled"
    rep = I.latest_report(db, opp)
    assert rep["c"]["verdict"] == "ready" and rep["c"]["path"] == "Buy a running salon"
    slot = P.open_slots(db)[0]
    pub.post(f"/s/{token}/book", data={"slot_id": str(slot["id"])})
    assert db.scalar("SELECT status FROM vikas_bookings WHERE opportunity_id = ?", (opp,)) == "booked"
    link = db.scalar("SELECT link FROM notifications WHERE kind = 'vikas_booking' ORDER BY id LIMIT 1")
    assert link == f"/buyers/{opp}?tab=investor"


def test_office_visit_registration_and_report(db, login, app):
    opp = _buyer(db)
    c = login("buyer@t.test")
    c.post(f"/buyers/{opp}/investor/fill", STRONG)
    rep = I.latest_report(db, opp)
    bid = P.book(db, B.load(db, opp), None, "Saturday", "staff", user(db, "buyer@t.test")["id"])
    c.post(f"/buyers/{opp}/investor/booking/{bid}", {"status": "done", "outcome": "Strong; visit office"})
    assert db.scalar("SELECT COUNT(*) FROM tasks WHERE title = 'Book the office visit and registration'") == 1
    c.post(f"/buyers/{opp}/investor/office", {"office_visit_status": "booked"})  # needs a date
    assert I.profile(db, opp)["office_visit_status"] == "not_booked"
    c.post(f"/buyers/{opp}/investor/office", {"office_visit_status": "booked", "office_visit_at": "2026-12-01T11:00"})
    prof = I.profile(db, opp)
    assert prof["office_visit_status"] == "booked" and prof["reg_status"] == "due"
    c.post(f"/buyers/{opp}/investor/office", {"office_visit_status": "done", "reg_status": "paid"})  # no mode
    assert I.profile(db, opp)["reg_status"] == "due"
    c.post(f"/buyers/{opp}/investor/office", {"office_visit_status": "done", "reg_status": "paid",
                                              "reg_paid_mode": "upi", "reg_paid_ref": "UPI-1"})
    prof = I.profile(db, opp)
    assert prof["reg_status"] == "paid" and prof["reg_paid_mode"] == "upi"
    assert "₹5,000" in c.get("/investors").data.decode()
    # finish the report after the call: Vikas's note, approve, send
    form = {"verdict": "ready", "path": rep["c"]["path"], "path_text": rep["c"]["path_text"], "summary": "Strong.",
            "strengths": "Savings\nExperience", "risks": "", "conditions": "Keep 6 months aside",
            "vikas_note": "Go ahead with a running salon in Kondapur.", "step_title_0": "Visit our office",
            "step_when_0": "This week", "action": "approve"}
    c.post(f"/buyers/{opp}/investor/report/{rep['id']}", form)
    rep = I.latest_report(db, opp)
    assert rep["status"] == "approved" and rep["c"]["vikas_note"].startswith("Go ahead")
    pdf = c.get(f"/buyers/{opp}/investor/report.pdf")
    assert pdf.data[:4] == b"%PDF" and "DRAFT" not in pdf.headers["Content-Disposition"]
    page = c.post(f"/buyers/{opp}/investor/link", {"purpose": "report"}).data.decode()
    token = re.search(r"/s/([A-Za-z0-9_\-]+)/report", page).group(1)
    pub = app.test_client()
    public = pub.get(f"/s/{token}/report").data.decode()
    assert "Ready to invest" in public and "Go ahead with a running salon" in public
    assert not FEE.search(public) and "registration" not in public.lower()  # never a price on the report
    assert pub.get(f"/s/{token}/report.pdf").data[:4] == b"%PDF"
    c.post(f"/buyers/{opp}/investor/sent")
    assert I.latest_report(db, opp)["sent_at"]


def test_weak_investor_gets_not_yet_with_conditions():
    weak = dict(STRONG, total_budget="8L", source=["Bank or personal loan"], loan_amount="6L", emi="18k",
                household_monthly="40k", money_ready="In 1–3 months", runway="No — it must run from sales",
                need_income="Yes — from the first month", other_income="No other income",
                salon_experience="No salon experience", business_experience="No business experience",
                days_per_week="1–2 days a week", if_loss="Sell / exit quickly", max_loss="I can't afford any loss",
                payback="Within 1 year", aligned="Not discussed yet")
    r = I.build_report(weak)
    assert r["verdict"] == "not_yet" and r["path"] == "Prepare, then invest"
    assert any("6 months of running costs" in x for x in r["conditions"])
    assert any("EMI" in x for x in r["risks"])


def test_finance_cannot_open_investor_screens(db, login):
    opp = _buyer(db)
    f = login("fin@t.test")
    assert f.get("/investors").status_code == 403
    assert f.post(f"/buyers/{opp}/investor/screen", {"goal": "buy_running"}).status_code == 403
