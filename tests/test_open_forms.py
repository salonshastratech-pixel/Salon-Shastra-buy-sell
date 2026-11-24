"""Open (public) Pilot Program and Investor forms: anyone can fill them; each creates the record and the report."""
import re

from test_investor import STRONG
from test_pilot import FULL

from teamos.domain import investor as I
from teamos.domain import pilot as P


def _token(page: str) -> str:
    return re.search(r'name="form_token" value="([^"]+)"', page).group(1)


def test_open_pilot_form_creates_salon_owner_and_swot(app, db, login):
    pub = app.test_client()
    page = pub.get("/f/pilot").data.decode()
    assert "Salon Shastra Pilot Program" in page and "What is the total area of the Salon in SFT" in page
    tok = _token(page)
    r = pub.post("/f/pilot", data={**FULL, "form_token": tok})                 # no consent tick → asked again
    assert r.status_code == 200 and "Please tick this box" in r.data.decode()
    r = pub.post("/f/pilot", data={**FULL, "form_token": tok, "consent_contact": "1"})
    assert r.status_code == 302 and r.headers["Location"].endswith("/f/pilot/done")
    opp = db.one("SELECT o.*, b.name AS salon_name FROM opportunities o JOIN salon_businesses b ON b.id = o.business_id "
                 "WHERE o.kind = 'seller'")
    assert opp["salon_name"] == "Glow Studio" and opp["source_detail"] == "Pilot Program form (public)"
    assert P.profile(db, opp["id"])["form_status"] == "filled"
    assert P.latest_report(db, opp["id"])["status"] == "draft"
    assert db.scalar("SELECT COUNT(*) FROM tasks WHERE entity_id = ? AND task_type = 'review'", (opp["id"],)) == 1
    assert "Thank you" in pub.get("/f/pilot/done").data.decode()
    forms = login("seller@t.test").get("/forms/").data.decode()
    assert "/f/pilot" in forms and "/f/investor" in forms and "on this computer only" not in forms


def test_open_investor_form_prescreens_and_drafts_report(app, db):
    pub = app.test_client()
    page = pub.get("/f/investor").data.decode()
    assert "Planning a salon investment?" in page and "5,000" not in page
    r = pub.post("/f/investor", data={**STRONG, "form_token": _token(page), "consent_contact": "1"})
    assert r.status_code == 302 and r.headers["Location"].endswith("/f/investor/done")
    opp = db.one("SELECT * FROM opportunities WHERE kind = 'buyer'")
    prof = I.profile(db, opp["id"])
    assert prof["screen_verdict"] == "pursue" and prof["form_status"] == "filled"
    assert I.latest_report(db, opp["id"])["c"]["verdict"] == "ready"
    m = db.one("SELECT * FROM buyer_mandates WHERE opportunity_id = ?", (opp["id"],))
    assert m["need_type"] == "acquire_operating" and m["all_in_max"] == 3_000_000 and m["purchase_window"] == "90d"


def test_open_forms_block_bots_and_stale_pages(app, db):
    pub = app.test_client()
    page = pub.get("/f/pilot").data.decode()
    r = pub.post("/f/pilot", data={**FULL, "form_token": _token(page), "consent_contact": "1", "website": "spam"})
    assert r.status_code == 302 and not db.scalar("SELECT COUNT(*) FROM opportunities")   # honeypot: nothing saved
    r = pub.post("/f/pilot", data={**FULL, "form_token": "forged", "consent_contact": "1"})
    assert r.status_code == 400 and not db.scalar("SELECT COUNT(*) FROM opportunities")
    inv = pub.get("/f/investor").data.decode()
    r = pub.post("/f/pilot", data={**FULL, "form_token": _token(inv), "consent_contact": "1"})  # wrong form's token
    assert r.status_code == 400


def test_screen_from_form_reads_weak_answers():
    weak = dict(STRONG, goal="Open a new salon", total_budget="8L", source=["Bank or personal loan"],
                money_ready="Not arranged yet", runway="No — it must run from sales",
                days_per_week="Only weekends / remotely")
    s = I.screen_from_form(weak)
    assert s == {"goal": "new_salon", "budget": "5_15", "money": "loan_plan", "runway": "no", "operator": "manager"}
    assert I.screen_result(s)["verdict"] == "not_now"


def test_pilot_form_fills_empty_salon_fields_only_and_keeps_help_seekers_out_of_matching(app, db):
    from test_pilot import _seller

    from teamos.domain import matching as M
    from teamos.domain import sellers as S
    oid = _seller(db)
    lst = db.one("SELECT * FROM seller_listings WHERE opportunity_id = ?", (oid,))
    db.execute("UPDATE seller_listings SET chairs = 9 WHERE id = ?", (lst["id"],))     # the team already wrote 9
    opp = db.one("SELECT * FROM opportunities WHERE id = ?", (oid,))
    P.save_response(db, opp, FULL, "staff")
    lst = S.load_listing(db, lst["id"])
    assert lst["chairs"] == 9                                                          # never overwritten
    assert lst["carpet_area_sqft"] == 900 and lst["floor_level"] == "ground" and lst["operating_years"] == 4
    assert lst["avg_monthly_sales"] == 180000 and lst["monthly_rent"] == 45000 and lst["staff_headcount"] == 6
    assert lst["gender_format"] == "unisex" and lst["sale_purpose"] == "seek_help"
    assert S.provenance(db, lst["id"])["carpet_area_sqft"]["source"] == "pilot_form"
    assert not M.seller_eligible(M.load_seller(db, oid))                               # wants help, not a sale
    admin = db.one("SELECT * FROM users WHERE email = 'admin@t.test'")
    P.save_needs(db, admin, opp, {"lead_type": "salon_owner", "help_areas": ["sell_salon", "chairpe"]})
    assert S.load_listing(db, lst["id"])["sale_purpose"] == "sell_entire"             # team ticked "wants to sell"
    assert M.seller_eligible(M.load_seller(db, oid))


def test_investor_form_fills_buyer_requirements(app, db):
    pub = app.test_client()
    page = pub.get("/f/investor").data.decode()
    pub.post("/f/investor", data={**STRONG, "areas": "Kondapur, Gachibowli", "form_token": _token(page),
                                  "consent_contact": "1"})
    opp = db.one("SELECT * FROM opportunities WHERE kind = 'buyer'")
    m = db.one("SELECT * FROM buyer_mandates WHERE opportunity_id = ?", (opp["id"],))
    assert (m["chairs_min"], m["chairs_max"]) == (5, 8) and m["salon_background"] == "operator"
    assert m["funding_source"] == "self" and m["finance_dependence"] == "none" and m["cash_ready"] == "now"
    assert m["involvement"] == "owner_operator" and m["decision_maker"] == "yes"
    locs = [r["locality"] for r in db.all("SELECT locality FROM buyer_localities WHERE mandate_id = ? ORDER BY rank_order",
                                         (m["id"],))]
    assert "Gachibowli" in locs
    assert db.scalar("SELECT choice FROM buyer_preferences WHERE mandate_id = ? AND dimension = 'gender_format'",
                     (m["id"],)) == "unisex"


def test_team_can_do_everything_except_finance(app, db, login):
    from teamos.permissions import ROLE_CAPABILITIES
    team = ROLE_CAPABILITIES["team_member"]
    assert "listing.approve" in team and "crm.import" in team and "crm.assign" in team
    assert not team & {"finance.view", "finance.edit", "users.manage", "settings.manage"}
    c = login("seller@t.test")
    assert "Finance" not in c.get("/").data.decode().split("<main")[0]
    assert c.get("/admin/finance").status_code in (302, 403)
    page = login("admin@t.test").get("/admin/users").data.decode()
    assert "Team members" in page and "everything except Finance" in page and "Default role matrix" not in page
