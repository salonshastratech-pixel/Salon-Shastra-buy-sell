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
    assert r.status_code == 302 and "/s/" in r.headers["Location"] and r.headers["Location"].endswith("/thanks")
    opp = db.one("SELECT o.*, b.name AS salon_name FROM opportunities o JOIN salon_businesses b ON b.id = o.business_id "
                 "WHERE o.kind = 'seller'")
    assert opp["salon_name"] == "Glow Studio" and opp["source_detail"] == "Pilot Program form (public)"
    assert P.profile(db, opp["id"])["form_status"] == "filled"
    assert P.latest_report(db, opp["id"])["status"] == "draft"
    assert db.scalar("SELECT COUNT(*) FROM tasks WHERE entity_id = ? AND task_type = 'review'", (opp["id"],)) == 1
    thanks = pub.get(r.headers["Location"]).data.decode()
    assert "Thank you, Kavya" in thanks and "3 quick wins" in thanks and "What happens next" in thanks
    assert "wa.me/918686878090" in thanks and "ChairPe" in thanks and "Talk to" in thanks
    forms = login("seller@t.test").get("/forms/").data.decode()
    assert "/f/pilot" in forms and "/f/investor" in forms and "on this computer only" not in forms


def test_open_investor_form_prescreens_and_drafts_report(app, db):
    pub = app.test_client()
    page = pub.get("/f/investor").data.decode()
    assert "Planning a salon investment?" in page and "5,000" not in page
    r = pub.post("/f/investor", data={**STRONG, "form_token": _token(page), "consent_contact": "1"})
    assert r.status_code == 302 and r.headers["Location"].endswith("/thanks")
    page2 = pub.get(r.headers["Location"]).data.decode()
    assert "First pointers" in page2 and "Keep these ready" in page2 and "5,000" not in page2
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
    # an expired / forged / wrong-form token saves nothing — but the form comes back with every answer kept
    r = pub.post("/f/pilot", data={**FULL, "form_token": "forged", "consent_contact": "1"})
    assert r.status_code == 200 and "Your answers are kept" in r.data.decode()
    assert not db.scalar("SELECT COUNT(*) FROM opportunities")
    inv = pub.get("/f/investor").data.decode()
    r = pub.post("/f/pilot", data={**FULL, "form_token": _token(inv), "consent_contact": "1"})  # wrong form's token
    assert "Your answers are kept" in r.data.decode() and not db.scalar("SELECT COUNT(*) FROM opportunities")


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



def test_area_check_counts_nearby_salons_and_lands_in_both_reports(app, db, monkeypatch):
    import io
    import json as _json

    from teamos.domain import area_insights as AI
    from teamos.domain import swot_pdf
    els = [{"tags": {"shop": "hairdresser", "name": "Cuts & Co"}}, {"tags": {"shop": "hairdresser"}},
           {"tags": {"shop": "beauty", "name": "Glam Beauty Parlour"}}, {"tags": {"shop": "barber"}},
           {"tags": {"shop": "beauty", "beauty": "nails"}}, {"tags": {"leisure": "spa", "name": "Om Spa"}},
           {"tags": {"shop": "hairdresser", "name": "Royal Mens Salon"}}]
    calls = []

    def opener(req, timeout):
        calls.append(req.full_url)
        return io.BytesIO(_json.dumps({"elements": els}).encode())
    AI._CACHE.clear()
    area = AI.build("Kondapur", 2, False, opener=opener)
    assert area["counts"] == {"salon": 2, "beauty": 1, "nails": 1, "barber": 2, "spa": 1} and area["total"] == 7
    from urllib.parse import unquote
    assert any("Moderate competition" in t for t in area["tips"]) and "around:2000," in unquote(calls[0])
    assert AI.build("Nowhere-pet", 2, False, opener=opener) is None
    AI.build("Kondapur", 2, False, opener=lambda *a, **k: (_ for _ in ()).throw(AssertionError("cached")))  # cached
    # a new pilot form: the draft gets the area block in the background (network replaced by the fake answer)
    app.config["AREA_CHECK_IN_TESTS"] = True
    monkeypatch.setattr(AI, "fetch", lambda lat, lng, r, opener=None: {"counts": area["counts"], "total": 7,
                                                                       "names": []})
    pub = app.test_client()
    page = pub.get("/f/pilot").data.decode()
    pub.post("/f/pilot", data={**FULL, "form_token": _token(page), "consent_contact": "1"})
    opp = db.one("SELECT * FROM opportunities WHERE kind = 'seller'")
    rep = P.latest_report(db, opp["id"])
    assert rep["c"]["area"]["place"] and rep["c"]["area"]["counts"]["nails"] == 1
    pdf = swot_pdf.build({"c": rep["c"], "date": "25 Nov 2026", "version": 1})
    assert pdf[:4] == b"%PDF" and len(pdf) > 3000
    app.config["AREA_CHECK_IN_TESTS"] = False


def test_open_form_shows_save_errors_on_the_right_question(app, db):
    pub = app.test_client()
    page = pub.get("/f/pilot").data.decode()
    r = pub.post("/f/pilot", data={**FULL, "contact_number": "12345", "form_token": _token(page),
                                    "consent_contact": "1"})
    body = r.data.decode()
    assert r.status_code == 200 and not db.scalar("SELECT COUNT(*) FROM opportunities")
    assert "10-digit" in body or "mobile" in body.lower()


def test_form_is_saved_while_filling_and_finishing_keeps_the_same_record(app, db, login):
    pub = app.test_client()
    tok = _token(pub.get("/f/pilot").data.decode())
    # step 1 done: name + mobile + a couple of answers → saved, record appears as "Partly filled"
    r = pub.post("/f/pilot/save", data={"form_token": tok, "owner_name": "Kavya Reddy", "contact_number": "9000011001",
                                        "salon_name": "Glow Studio", "location": "Kondapur", "chairs": "6"})
    j = r.get_json()
    assert j["ok"] and j["draft"]
    opp = db.one("SELECT o.*, b.city, b.locality FROM opportunities o JOIN salon_businesses b ON b.id = o.business_id")
    assert opp["city"] == "Hyderabad" and opp["locality"] == "Kondapur"            # city worked out from the area
    prof = P.profile(db, opp["id"])
    assert prof["form_status"] == "sent" and prof["form_answered"] == 5 and prof["form_questions"] > 40
    assert db.scalar("SELECT chairs FROM seller_listings") == 6                     # known facts already usable
    task = db.one("SELECT * FROM tasks WHERE entity_id = ? AND status = 'open' AND task_type = 'review'", (opp["id"],))
    assert task["title"].startswith("Call to complete the form") and "5 of" in task["title"]
    team = login("seller@t.test")
    assert "Partly filled" in team.get("/sellers/").data.decode()
    assert "Kondapur" in team.get(f"/pilot/{opp['id']}/fill").data.decode()       # team completes from what's saved
    # a second autosave updates the same record
    pub.post("/f/pilot/save", data={"form_token": tok, "draft": j["draft"], "owner_name": "Kavya Reddy",
                                    "contact_number": "9000011001", "chairs": "7", "monthly_rent": "45k"})
    assert db.scalar("SELECT COUNT(*) FROM opportunities") == 1
    # submit with only some answers → same record, greeting page, still marked as missing some answers
    r = pub.post("/f/pilot", data={"form_token": tok, "draft": j["draft"], "owner_name": "Kavya Reddy",
                                   "contact_number": "9000011001", "location": "Kondapur", "consent_contact": "1"})
    assert r.status_code == 302 and r.headers["Location"].endswith("/thanks")
    assert "Thank you, Kavya" in pub.get(r.headers["Location"]).data.decode()
    assert db.scalar("SELECT COUNT(*) FROM opportunities") == 1
    prof = P.profile(db, opp["id"])
    assert prof["form_status"] == "filled" and prof["form_answered"] < prof["form_questions"]
    assert "Review: Form submitted" in db.scalar("SELECT title FROM tasks WHERE id = ?", (task["id"],))


def test_only_name_and_mobile_are_needed_and_location_helpers(app, db):
    pub = app.test_client()
    page = pub.get("/f/investor").data.decode()
    assert 'list="area-names"' in page and "Use my location" in page and "answered" in page
    r = pub.post("/f/investor", data={"form_token": _token(page), "consent_contact": "1", "city": "Hyd"})
    body = r.data.decode()
    assert r.status_code == 200 and "Please tell us your name" in body and "mobile number" in body
    r = pub.post("/f/investor", data={"form_token": _token(page), "consent_contact": "1", "full_name": "Arjun",
                                      "phone": "9100011001", "city": "Hyd"})
    assert r.status_code == 302 and db.scalar("SELECT city FROM contacts") == "Hyderabad"
    assert pub.post("/f/investor/save", data={"form_token": _token(page), "full_name": "X", "phone": "12"}).get_json()["ok"] is False
    near = pub.get("/f/geo/near?lat=17.4605&lng=78.3645").get_json()
    assert near["ok"] and near["label"] == "Kondapur, Hyderabad"
    assert pub.get("/f/geo/near?lat=28.6&lng=77.2").get_json()["ok"] is False      # outside the area list: type it



def test_drive_link_for_photos_and_documents(app, db, login):
    from teamos.domain import pilot as PP
    a, e = PP.parse_answers({"drive_link": "drive.google.com/drive/folders/abc123"}, lenient=True)
    assert a["drive_link"] == "https://drive.google.com/drive/folders/abc123"
    a, e = PP.parse_answers({"drive_link": "my photos"})
    assert "drive_link" in e and "drive_link" not in a
    pub = app.test_client()
    page = pub.get("/f/pilot").data.decode()
    assert "Google Drive link" in page and 'type="url"' in page
    r = pub.post("/f/pilot", data={**FULL, "drive_link": "https://drive.google.com/drive/folders/abc123",
                                   "form_token": _token(page), "consent_contact": "1"})
    assert r.status_code == 302
    oid = db.scalar("SELECT id FROM opportunities")
    rec = login("seller@t.test").get(f"/sellers/{oid}?tab=pilot").data.decode()
    assert "Photos &amp; documents" in rec and "drive.google.com/drive/folders/abc123" in rec



def test_investor_with_few_answers_is_not_prescreened_and_area_retry(app, db, monkeypatch):
    from teamos.domain import area_insights as AI
    from teamos.domain import investor as II
    pub = app.test_client()
    page = pub.get("/f/investor").data.decode()
    pub.post("/f/investor", data={"full_name": "Sneha", "phone": "9100011005", "goal": "Open a new salon",
                                  "areas": "Kondapur", "form_token": _token(page), "consent_contact": "1"})
    opp = db.one("SELECT * FROM opportunities WHERE kind = 'buyer'")
    prof = II.profile(db, opp["id"])
    assert prof["screen_verdict"] is None and "ask the 5 questions" in prof["screen_note"]
    # the area check did not arrive at first (tests have no network): the hourly retry adds it
    monkeypatch.setattr(AI, "fetch", lambda lat, lng, r, opener=None: {"counts": {"salon": 2, "beauty": 0, "nails": 0,
                                                                                 "barber": 1, "spa": 0},
                                                                      "total": 3, "names": []})
    assert "added 1" in AI.retry_missing(db)
    assert II.latest_report(db, opp["id"])["c"]["area"]["place"] == "Kondapur"
    assert "added 0" in AI.retry_missing(db) or "retried 0" in AI.retry_missing(db)
