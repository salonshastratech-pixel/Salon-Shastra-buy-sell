"""Salon Owners pilot program: needs, pilot form (45 questions), SWOT report + PDF, Vikas bookings, consultations."""
import re
from datetime import timedelta

from conftest import user

from teamos.domain import pilot as P
from teamos.domain import sellers as S
from teamos.timeutil import IST, now_utc

FULL = {
    "salon_name": "Glow Studio", "owner_name": "Kavya Reddy", "location": "Kondapur, Hyderabad",
    "salon_type": "Unisex Salon", "contact_number": "9000011001", "email": "kavya@example.com",
    "area_sft": "900", "floor": "Ground Floor", "chairs": "6", "beds": "1", "pedicure_stations": "1",
    "hair_dressers": "3", "beauticians": "2", "manager": "No", "housekeeping": "Yes", "years_running": "4",
    "monthly_revenue": "1.8L", "monthly_expenses": "1.5L", "monthly_rent": "45k", "price_women_cut": "600",
    "price_men_cut": "250", "digital_marketing_spend": "No", "billing_software": "No",
    "stand_out": ["Highly skilled staff", "Unique services/treatments"], "service_rating": "4",
    "customers_love": "Friendly staff", "training_program": "No", "marketing_worked": "Instagram",
    "staff_struggle": "Yes, they leave for higher pay", "op_challenges": ["Staff turnover & inconsistent service"],
    "online_presence": "3", "track_financials": "No", "complaints": "Waiting time",
    "growth_opps": ["Improving digital marketing & social media presence"], "extra_revenue": "No",
    "digital_ads": "No", "loyalty": "No", "growth_strategies": "Bridal packages",
    "threats": ["New competitors in the market"], "price_competition": "Yes", "revenue_fluctuation": "Yes",
    "external_factors": "Two new salons nearby", "want_report": "Yes", "pilot_interest": "Yes",
    "willing_invest": "Yes", "comments": "Need help",
}


def _seller(db, phone="9000011001"):
    admin = user(db, "admin@t.test")
    return S.create_lead(db, admin, {"full_name": "Kavya Reddy", "phone": phone, "salon_name": "Glow Studio",
                                     "city": "Hyderabad", "locality": "Kondapur", "source": "meta_lead",
                                     "consent_contact": "1", "owner_id": str(user(db, "seller@t.test")["id"])}
                         )["opportunity_id"]


def _token(client, opp_id, purpose="form"):
    page = client.post(f"/sellers/{opp_id}/pilot/link", {"purpose": purpose}).data.decode()
    m = re.search(r"/s/([A-Za-z0-9_\-]+)", page)
    assert m, page[:500]
    return m.group(1)


def test_form_keeps_all_45_google_form_questions():
    assert P.ORIGINAL_COUNT == 45
    assert len(P.QUESTIONS) == len({q["key"] for q in P.QUESTIONS})
    assert P.Q_BY_KEY["salon_type"]["options"][2] == "High End Barber Salon(HEBS)"
    assert "5 th Floor" in P.Q_BY_KEY["floor"]["options"]


def test_money_words_are_understood():
    assert P.parse_amount("40k") == 40000 and P.parse_amount("1.8L") == 180000
    assert P.parse_amount("2 lakhs") == 200000 and P.parse_amount("50000.0") == 50000
    assert P.parse_amount("1000-1200/-") == 1000 and P.parse_amount("no") is None


def test_needs_are_saved_and_listed(db, login):
    opp = _seller(db)
    c = login("seller@t.test")
    r = c.post(f"/sellers/{opp}/pilot/needs", {"lead_type": "salon_owner", "help_areas": ["digital_marketing",
                                                                                         "staff_training", "bogus"],
                                               "problem_note": "Staff keep leaving"})
    assert r.status_code == 302
    prof = P.profile(db, opp)
    assert prof["help_areas"] == ["digital_marketing", "staff_training"] and prof["problem_note"] == "Staff keep leaving"
    lst = c.get("/sellers/").data.decode()
    assert "Salon Owners" in lst and "Marketing &amp; online growth, Staff training" in lst
    assert "Glow Studio" in c.get("/pilot?area=digital_marketing").data.decode()
    assert "Glow Studio" not in c.get("/pilot?area=chairpe").data.decode()


def test_owner_fills_form_by_link_and_swot_draft_appears(db, login, app):
    opp = _seller(db)
    c = login("seller@t.test")
    token = _token(c, opp)
    assert P.profile(db, opp)["form_status"] == "sent"
    pub = app.test_client()
    page = pub.get(f"/s/{token}").data.decode()
    assert "Salon Shastra Pilot Program" in page and "Kavya Reddy" in page  # prefilled from the record
    bad = pub.post(f"/s/{token}", data={"salon_name": "Glow"}).data.decode()
    assert "Please check the highlighted answers" in bad
    ok = pub.post(f"/s/{token}", data=FULL).data.decode()
    assert "Thank you" in ok
    assert P.profile(db, opp)["form_status"] == "filled"
    rep = P.latest_report(db, opp)
    assert rep["status"] == "draft" and rep["c"]["strengths"] and rep["c"]["weaknesses"] and rep["c"]["steps"]
    assert any("online presence" in w.lower() for w in rep["c"]["weaknesses"])
    assert db.scalar("SELECT COUNT(*) FROM notifications WHERE kind = 'pilot_form'") == 1
    tab = c.get(f"/sellers/{opp}?tab=pilot").data.decode()
    assert "Draft — check and approve" in tab and "Approve report" in tab


def test_staff_can_fill_edit_approve_and_download_pdf(db, login):
    opp = _seller(db)
    c = login("seller@t.test")
    assert c.post(f"/sellers/{opp}/pilot/fill", FULL).status_code == 302
    rep = P.latest_report(db, opp)
    pdf = c.get(f"/sellers/{opp}/pilot/report.pdf")
    assert pdf.status_code == 200 and pdf.data[:4] == b"%PDF" and "DRAFT" in pdf.headers["Content-Disposition"]
    form = {"summary": "Edited summary", "strengths": "Great team\nLoyal customers", "weaknesses": "No billing",
            "opportunities": "Bridal", "threats": "Competition", "step_title_0": "Start ChairPe",
            "step_when_0": "This week", "step_why_0": "Track bills", "step_how_0": "We install it", "action": "approve"}
    c.post(f"/sellers/{opp}/pilot/report/{rep['id']}", form)
    rep = P.latest_report(db, opp)
    assert rep["status"] == "approved" and rep["c"]["strengths"] == ["Great team", "Loyal customers"]
    assert [s["title"] for s in rep["c"]["steps"]] == ["Start ChairPe"]
    pdf = c.get(f"/sellers/{opp}/pilot/report.pdf")
    assert pdf.data[:4] == b"%PDF" and "DRAFT" not in pdf.headers["Content-Disposition"]
    # editing an approved report makes a new draft version
    c.post(f"/sellers/{opp}/pilot/report/{rep['id']}", {**form, "summary": "Changed", "action": "save"})
    assert P.latest_report(db, opp)["status"] == "draft" and P.latest_report(db, opp)["version"] == rep["version"] + 1


def test_report_link_shows_only_approved_report_and_books_vikas(db, login, app):
    opp = _seller(db)
    c = login("seller@t.test")
    c.post(f"/sellers/{opp}/pilot/fill", FULL)
    r = c.post(f"/sellers/{opp}/pilot/link", {"purpose": "report"})
    assert r.status_code == 302  # not approved yet → refused
    rep = P.latest_report(db, opp)
    P.save_report(db, user(db, "seller@t.test"), S.load(db, opp), rep["id"],
                  {"summary": "x", "strengths": "a", "weaknesses": "b", "opportunities": "c", "threats": "d",
                   "step_title_0": "Do it"}, approve=True)
    token = _token(c, opp, "report")
    pub = app.test_client()
    page = pub.get(f"/s/{token}/report").data.decode()
    assert "Download the full report" in page and "Talk to Vikas Rachakonda" in page and "Request a call" in page
    assert pub.get(f"/s/{token}/report.pdf").data[:4] == b"%PDF"
    # Vikas adds free times; owner books one; Vikas (consultant) is alerted
    admin = login("admin@t.test")
    day = (now_utc().astimezone(IST) + timedelta(days=2)).strftime("%Y-%m-%d")
    admin.post("/pilot/vikas", {"day": day, "times": "10:00 11:00", "minutes": "30", "mode": "phone"})
    slots = P.open_slots(db)
    assert len(slots) == 2
    pub.post(f"/s/{token}/book", data={"slot_id": str(slots[0]["id"]), "note": "Pricing"})
    b = db.one("SELECT * FROM vikas_bookings WHERE opportunity_id = ?", (opp,))
    assert b["status"] == "booked" and b["source"] == "link"
    admin_id = user(db, "admin@t.test")["id"]
    assert db.scalar("SELECT COUNT(*) FROM notifications WHERE user_id = ? AND kind = 'vikas_booking'", (admin_id,)) == 1
    assert db.scalar("SELECT COUNT(*) FROM email_outbox WHERE kind = 'vikas_booking'") == 1
    assert "is booked for" in pub.get(f"/s/{token}/book").data.decode()
    # double booking the same salon is refused; the taken slot is gone for others
    assert len(P.open_slots(db)) == 1
    again = pub.post(f"/s/{token}/book", data={"slot_id": str(slots[1]["id"])})
    assert db.scalar("SELECT COUNT(*) FROM vikas_bookings") == 1 and again.status_code == 200


def test_request_when_no_slots_then_call_held_creates_callback_task(db, login):
    opp = _seller(db)
    o = S.load(db, opp)
    bid = P.book(db, o, None, "Saturday after 3", "link")
    assert db.one("SELECT status FROM vikas_bookings WHERE id = ?", (bid,))["status"] == "requested"
    c = login("seller@t.test")
    c.post(f"/sellers/{opp}/pilot/booking/{bid}", {"status": "done", "outcome": "Wants a salon visit"})
    assert db.one("SELECT status FROM vikas_bookings WHERE id = ?", (bid,))["status"] == "done"
    assert db.scalar("SELECT COUNT(*) FROM tasks WHERE title LIKE 'Call back: does the owner want a paid%'") == 1


def test_consultation_fees_payment_and_free_video(db, login):
    opp = _seller(db)
    c = login("seller@t.test")
    c.post(f"/sellers/{opp}/pilot/consultation", {"kind": "office", "in_hyderabad": "1"})
    c.post(f"/sellers/{opp}/pilot/consultation", {"kind": "salon_visit", "in_hyderabad": "1"})
    c.post(f"/sellers/{opp}/pilot/consultation", {"kind": "salon_visit", "in_hyderabad": "0"})
    fees = sorted(r["fee"] for r in db.all("SELECT fee FROM consultations"))
    assert fees == [3000, 5000, 10000]
    cid = db.scalar("SELECT id FROM consultations WHERE fee = 5000")
    r = c.post(f"/sellers/{opp}/pilot/consultation/{cid}", {"status": "done", "paid": "1"})  # no mode → refused
    assert db.one("SELECT paid FROM consultations WHERE id = ?", (cid,))["paid"] == 0
    c.post(f"/sellers/{opp}/pilot/consultation/{cid}", {"status": "done", "paid": "1", "paid_mode": "upi",
                                                        "paid_ref": "UPI123", "video_status": "posted",
                                                        "video_link": "https://instagram.com/p/x"})
    k = db.one("SELECT * FROM consultations WHERE id = ?", (cid,))
    assert k["paid"] == 1 and k["paid_mode"] == "upi" and k["video_status"] == "posted"
    ov = c.get("/pilot").data.decode()
    assert "₹5,000" in ov and "collected of ₹18,000" in ov


def test_links_are_private_and_can_be_switched_off(db, login, app):
    opp = _seller(db)
    c = login("seller@t.test")
    token = _token(c, opp)
    pub = app.test_client()
    assert pub.get(f"/s/{token}").status_code == 200
    assert pub.get("/s/not-a-real-token").status_code == 404
    assert pub.get(f"/s/{token}/report").status_code == 200  # no approved report yet → "being prepared"
    assert "being prepared" in pub.get(f"/s/{token}/report").data.decode()
    assert pub.get(f"/s/{token}/report.pdf").status_code == 404
    c.post(f"/sellers/{opp}/pilot/links/off")
    assert pub.get(f"/s/{token}").status_code == 404


def test_finance_user_cannot_touch_pilot_records(db, login):
    opp = _seller(db)
    f = login("fin@t.test")
    assert f.get(f"/sellers/{opp}/pilot/fill").status_code == 403
    assert f.post(f"/sellers/{opp}/pilot/needs", {"help_areas": ["chairpe"]}).status_code == 403
    assert f.get("/pilot").status_code == 403


def test_swot_rules_reward_good_answers():
    good = dict(FULL, online_presence="9", digital_ads="Yes", billing_software="Yes", track_financials="Yes",
                monthly_expenses="90k", loyalty="Yes", training_program="Yes, monthly academy sessions",
                staff_struggle="No", op_challenges=[])
    g, b = P.build_swot(good), P.build_swot(FULL)
    gs, bs = dict(g["scores"]), dict(b["scores"])
    assert gs["Marketing & online"] > bs["Marketing & online"] and gs["Money & profit"] > bs["Money & profit"]
    assert not any("ChairPe" in s["title"] for s in g["steps"])
    assert any("ChairPe" in s["title"] for s in b["steps"])


def test_chairpe_sale_trial_reminder_and_paying(db, login):
    opp = _seller(db)
    c = login("seller@t.test")
    c.post(f"/sellers/{opp}/pilot/fill", dict(FULL, chairs="1", hair_dressers="1", beauticians="0"))
    assert "Basic · ₹599/month (suggested)" in c.get(f"/sellers/{opp}?tab=pilot").data.decode()
    c.post(f"/sellers/{opp}/pilot/chairpe", {"chairpe_status": "trial", "chairpe_plan": "elite"})
    prof = P.profile(db, opp)
    assert prof["chairpe_status"] == "trial" and prof["chairpe_trial_start"]
    assert db.scalar("SELECT COUNT(*) FROM tasks WHERE title LIKE 'ChairPe trial ends%'") == 1
    c.post(f"/sellers/{opp}/pilot/chairpe", {"chairpe_status": "paying", "chairpe_plan": "elite"})  # months missing
    assert P.profile(db, opp)["chairpe_status"] == "trial"
    c.post(f"/sellers/{opp}/pilot/chairpe", {"chairpe_status": "paying", "chairpe_plan": "elite", "chairpe_months": "12"})
    prof = P.profile(db, opp)
    assert prof["chairpe_status"] == "paying" and prof["chairpe_months"] == 12 and prof["chairpe_paid_on"]
    page = c.get("/pilot").data.decode()
    assert "ChairPe paying" in page and ">paying<" in page
    # the PDF offers ChairPe when the report recommends it
    assert any(s["area"] == "chairpe" for s in P.latest_report(db, opp)["c"]["steps"])
    assert c.get(f"/sellers/{opp}/pilot/report.pdf").data[:4] == b"%PDF"


def test_new_salon_lead_type(db, login):
    opp = _seller(db)
    c = login("seller@t.test")
    c.post(f"/sellers/{opp}/pilot/needs", {"lead_type": "new_salon", "help_areas": ["new_salon_setup"]})
    assert P.profile(db, opp)["lead_type"] == "new_salon"
    assert "For a new salon, tick" in c.get(f"/sellers/{opp}?tab=pilot").data.decode()
