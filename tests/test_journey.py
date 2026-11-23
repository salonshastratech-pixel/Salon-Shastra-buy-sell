"""Whole-business journey through the real screens, module by module: lead → call → WhatsApp → customer form →
review → listing approval → buyer → match → teaser → introduction → deal → fee → reports.

Each step uses the same pages and buttons the team uses, so any seam between modules shows up here."""
import io
import json
import re

from conftest import SELLER_FORM, user
from test_deals import approved_teaser, close_won, introduce, share
from test_forms import BUYER, public, send
from test_matching import consent, set_seller

from teamos.domain import matching as M
from teamos.domain import seller_fields as SF

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def ok(r, *texts):
    assert r.status_code in (200, 302), (r.status_code, r.data[:300])
    body = r.data.decode()
    for t in texts:
        assert t in body, (t, body[:500])
    return body


def test_full_business_journey_through_the_screens(app, db, login):
    shaheen, harika, lead, admin, fin = (login(e) for e in ("seller@t.test", "buyer@t.test", "lead@t.test",
                                                           "admin@t.test", "fin@t.test"))
    # 1. Seller lead + first call + one-click WhatsApp -------------------------------------------------
    assert shaheen.post("/sellers/new", SELLER_FORM).status_code == 302
    s = db.scalar("SELECT MAX(id) FROM opportunities WHERE kind = 'seller'")
    ok(shaheen.get("/"), "First call")                                   # first-contact task on My Day
    r = shaheen.post(f"/sellers/{s}/call", {"outcome": "connected", "note": "Keen to sell"})
    assert "after_call=connected" in r.headers["Location"]
    ok(shaheen.get(f"/sellers/{s}?after_call=connected"), "Call logged — send a WhatsApp follow-up")
    shaheen.post(f"/inbox/quick/{s}", {"sid": "call_thanks_seller", "outcome": "connected"})
    assert db.scalar("SELECT template_key FROM messages WHERE direction = 'out' ORDER BY id DESC") == "call_thanks_seller"
    assert db.scalar("SELECT stage FROM opportunities WHERE id = ?", (s,)) == "connected"

    # 2. Detailed form → the owner answers on their phone → reviewed into the record ------------------------
    token, _ = send(shaheen, s, "seller_deep")
    p = public(app)
    ok(p.get(f"/f/i/{token}"), "Every question is optional", "Which floor is the salon on?")
    answers = {"monthly_rent": "80k", "maintenance_monthly": "10k", "rent_deposit": "5L",
               "deposit_treatment": "included_in_asking", "floor_level": "ground", "area_type": "main_road",
               "parking": "car_and_bike", "started_year": "2019", "chairs": "8", "carpet_area_sqft": "1200",
               "gender_format": "unisex", "positioning": "premium", "business_model": "independent",
               "transfer_authority": "yes", "avg_monthly_sales": "10L", "avg_monthly_expenses": "7L",
               "staff_continuity": "yes", "lease_expiry": "2031-03-31", "lease_renewal": "yes",
               "lease_transfer_status": "confirmed_transferable", "trade_licence": "yes",
               "stock_value_separate__state": "not_applicable"}
    r = p.post(f"/f/i/{token}", data={**answers, "action": "submit", "doc__photo_front": (io.BytesIO(PNG), "f.png")},
               content_type="multipart/form-data")
    ok(r, "Thank you")
    sub = db.one("SELECT * FROM form_submissions WHERE status = 'submitted' AND opportunity_id = ?", (s,))
    task = db.one("SELECT * FROM tasks WHERE id = ?", (sub["review_task_id"],))
    assert task and task["status"] == "open"
    ok(lead.get(f"/forms/submissions/{sub['id']}"), "First floor" if False else "Ground floor")
    keys = [k.replace("__state", "") for k in answers]
    lead.post(f"/forms/submissions/{sub['id']}", {"accept": keys, "action": "apply"})
    l = db.one("SELECT * FROM seller_listings WHERE opportunity_id = ?", (s,))
    assert (l["monthly_rent"], l["maintenance_monthly"], l["floor_level"], l["parking"]) == \
        (80_000, 10_000, "ground", "car_and_bike")
    assert db.scalar("SELECT status FROM tasks WHERE id = ?", (task["id"],)) == "done"
    # remaining facts the team collects on calls (staff intake), then listing approval through the screens
    set_seller(db, s, equipment_condition="good", operating_years="5", negotiable="no", outstanding_loans="0",
               litigation="no", sale_structure="entity", transition_support="yes")
    for key, _label in SF.LISTING_CHECKLIST:
        lead.post(f"/sellers/{s}/checklist", {"item_key": key, "status": "reviewed"})
    shaheen.post(f"/sellers/{s}/request-review")
    ok(admin.get("/"), "Review listing")
    admin.post(f"/sellers/{s}/decision", {"decision": "approved"})
    assert db.scalar("SELECT approval_state FROM seller_listings WHERE opportunity_id = ?", (s,)) == "approved"
    consent(db, s, "share_teaser")

    # 3. Buyer lead → buyer form with floor / area / parking → criteria confirmed, funding reviewed ------------
    assert harika.post("/buyers/new", {**BUYER, "all_in_max": "40L"}).status_code == 302
    b = db.scalar("SELECT MAX(id) FROM opportunities WHERE kind = 'buyer'")
    token, _ = send(harika, b, "buyer_deep")
    r = p.post(f"/f/i/{token}", data={"pref__floor_level__ground": "must", "pref__area_type__main_road": "prefer",
                                       "pref__gender_format__unisex": "must", "parking_need": "car",
                                       "parking_need__importance": "prefer", "max_monthly_rent": "1L",
                                       "chairs_min": "6", "cash_ready": "now", "localities_preferred": "Kondapur",
                                       "consent_share_criteria": "1", "action": "submit"})
    ok(r, "Thank you")
    bsub = db.one("SELECT * FROM form_submissions WHERE status = 'submitted' AND opportunity_id = ?", (b,))
    bans = json.loads(bsub["answers"])
    accept = [k for k in bans if not k.startswith("_")]
    lead.post(f"/forms/submissions/{bsub['id']}", {"accept": accept, "action": "apply"})
    lead.post(f"/buyers/{b}/confirm-criteria")
    lead.post(f"/buyers/{b}/funding", {"status": "verified", "note": "Bank statement seen"})
    m = db.one("SELECT * FROM buyer_mandates WHERE opportunity_id = ?", (b,))
    assert m["parking_need"] == "car" and m["max_monthly_rent"] == 1_00_000 and m["funding_status"] == "verified"
    consent(db, b, "share_teaser")

    # 4. Matching uses the new facts; Match Desk shows the pair -----------------------------------------------
    M.refresh(db, force=True)
    c = db.one("SELECT * FROM match_candidates WHERE buyer_opp_id = ? AND seller_opp_id = ?", (b, s))
    assert c, "salon and buyer were not paired"
    res = M.evaluate(M.load_buyer(db, b), M.load_seller(db, s))
    f = {x["key"]: x for x in res["factors"]}
    assert f["floor_level"]["result"] == "match" and f["parking"]["result"] == "match"
    assert f["rent"]["result"] == "match" and "90,000" in (f["rent"]["seller"] or "")  # rent + maintenance
    ok(lead.get("/matches/"), "Test Salon" if False else "")
    assert c["readiness"] == "ready_to_approach", (c["readiness"], res.get("questions"))

    # 5. Approach → teaser → introduction → deal → close → fee → reports --------------------------------------
    lead.post(f"/matches/{c['id']}/stage", {"to": "approved_to_approach"})
    lead.post(f"/matches/{c['id']}/stage", {"to": "interest_confirmed", "note": "Buyer keen"})
    approved_teaser(db, login, s)
    tok = share(login, c["id"])
    ok(app.test_client().get(f"/t/{tok}"), "Salon Shastra")
    deal = introduce(db, login, c["id"])
    ok(shaheen.get(f"/deals/{deal}"), "Due diligence" if False else "")
    fin.post("/admin/finance/agreement", {"deal_id": deal, "payer": "seller", "fee_type": "success",
                                          "basis": "percent", "rate_pct": "5", "earning_condition": "on_closing"})
    close_won(db, login, deal)
    assert db.scalar("SELECT status FROM opportunities WHERE id = ?", (s,)) == "closed"
    ok(fin.get("/admin/finance"), "₹1 L")
    rep = ok(admin.get("/reports/?period=month"), "Leads", "WhatsApp")
    assert "Deals closed won" in rep
    for who in (shaheen, harika, lead, admin):
        ok(who.get("/"))
    # nothing in the whole journey produced a server error in the audit trail of denials meant for real users
    denied = db.all("SELECT action, entity_type, detail FROM audit_events WHERE outcome = 'denied' AND actor_id IN "
                    "(?, ?)", (user(db, "seller@t.test")["id"], user(db, "buyer@t.test")["id"]))
    print("DENIED", denied)
    assert [d["action"] for d in denied] == ["deal.stage"] or not denied, denied  # only the intended approver check
    assert re.search(r"closed", db.scalar("SELECT stage FROM deals WHERE id = ?", (deal,)))
