"""Milestone 7.2 — easier customer forms with more questions, and the new facts synced into matching."""
import io
import json

from test_forms import new_buyer, new_seller, public, send
from test_matching import buyer, cand, seller

from teamos.domain import form_templates as FT
from teamos.domain import seller_fields as SF
from teamos.domain import buyer_fields as BF
from teamos.domain.common import parse_inr

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def test_every_seller_and_buyer_question_is_in_the_easy_form_once():
    for key, catalogue in (("seller_deep", SF.FIELDS), ("buyer_deep", BF.FIELDS)):
        keys = FT.template_field_keys(FT.TEMPLATES[key])
        assert sorted(keys) == sorted(f.key for f in catalogue), key
        assert len(keys) == len(set(keys))
    assert {"maintenance_monthly", "floor_level", "area_type", "parking", "started_year", "maps_link",
            "trade_licence"} <= set(FT.template_field_keys(FT.TEMPLATES["seller_deep"]))
    assert {k for k, _ in FT.PHOTO_TYPES} <= set(FT.TEMPLATES["seller_deep"]["docs"])
    assert parse_inr("45 thousand") == 45_000 and parse_inr("45k") == 45_000


def test_seller_form_is_easy_all_optional_and_collects_premises_costs_and_photos(app, db, login):
    c, opp = new_seller(login, db)
    token, _ = send(c, opp, "seller_deep")
    p = public(app)
    page = p.get(f"/f/i/{token}").data.decode()
    assert "Every question is optional" in page and "data-stepper" in page
    assert "Which floor is the salon on?" in page and 'type="radio" name="floor_level"' in page  # tap buttons
    assert "maintenance you pay every month" in page
    assert 'capture="environment"' in page and "Shop front from across the road" in page
    assert "I don&#39;t know" in page or "I don't know" in page
    # an owner answers just a few things, says "don't know" to one and adds a photo
    r = p.post(f"/f/i/{token}", data={"monthly_rent": "45k", "maintenance_monthly": "8000", "floor_level": "first",
                                       "parking": "bike_only", "started_year": "2017", "rent_deposit__state": "unknown",
                                       "action": "submit",
                                       "doc__photo_front": (io.BytesIO(PNG), "front.png")},
               content_type="multipart/form-data")
    assert r.status_code == 200 and b"Thank you" in r.data, r.data[:500]
    sub = db.one("SELECT * FROM form_submissions WHERE status = 'submitted'")
    ans = json.loads(sub["answers"])
    assert ans["monthly_rent"]["value"] == "45k" and ans["rent_deposit"]["state"] == "unknown"
    assert db.scalar("SELECT doc_type FROM documents WHERE submission_id = ?", (sub["id"],)) == "photo_front"
    # the team reviews and applies: new facts land on the seller record with their source
    lead = login("lead@t.test")
    review = lead.get(f"/forms/submissions/{sub['id']}").data.decode()
    assert "maintenance" in review.lower() and "First floor" in review
    lead.post(f"/forms/submissions/{sub['id']}", {"accept": ["monthly_rent", "maintenance_monthly", "floor_level",
                                                             "parking", "started_year"], "action": "apply"})
    l = db.one("SELECT * FROM seller_listings WHERE opportunity_id = ?", (opp,))
    assert (l["monthly_rent"], l["maintenance_monthly"], l["floor_level"], l["parking"], l["started_year"]) == \
        (45_000, 8_000, "first", "bike_only", 2017)
    # the staff intake shows the same new questions
    staff = c.get(f"/sellers/{opp}?tab=intake").data.decode()
    assert "Location &amp; premises" in staff or "Location & premises" in staff


def test_buyer_form_asks_floor_area_and_parking_in_the_same_words(app, db, login):
    c, opp = new_buyer(login, db)
    token, _ = send(c, opp, "buyer_deep")
    p = public(app)
    page = p.get(f"/f/i/{token}").data.decode()
    assert "Inside a mall" in page and "Main road / high street" in page and "Customer parking" in page
    r = p.post(f"/f/i/{token}", data={"pref__floor_level__ground": "must", "pref__area_type__main_road": "prefer",
                                       "parking_need": "car", "parking_need__importance": "must",
                                       "max_monthly_rent": "60k", "action": "submit"})
    assert r.status_code == 200
    ans = json.loads(db.scalar("SELECT answers FROM form_submissions WHERE status = 'submitted'"))
    assert ans["pref:floor_level:ground"]["value"] == "must" and ans["parking_need"]["value"] == "car"
    assert ans["importance:parking_need"]["value"] == "must"


def test_matching_uses_rent_plus_maintenance_floor_and_parking(app, db):
    s = seller(db, monthly_rent="40000")
    db.execute("UPDATE seller_listings SET maintenance_monthly = 10000, floor_level = 'first', parking = 'bike_only' "
               "WHERE opportunity_id = ?", (s,))
    b = buyer(db)
    mid = db.scalar("SELECT id FROM buyer_mandates WHERE opportunity_id = ?", (b,))
    db.execute("UPDATE buyer_mandates SET max_monthly_rent = 45000, parking_need = 'car' WHERE id = ?", (mid,))
    now = "2026-01-01T00:00:00Z"
    for dim, choice, imp in (("max_monthly_rent", "", "must"), ("parking_need", "", "prefer"),
                             ("floor_level", "ground", "must")):
        db.execute("INSERT INTO buyer_preferences (mandate_id, dimension, choice, importance, updated_at) "
                   "VALUES (?, ?, ?, ?, ?)", (mid, dim, choice, imp, now))
    c = cand(db, b, s)
    assert c is not None
    from teamos.domain import matching as M
    res = M.evaluate(M.load_buyer(db, b), M.load_seller(db, s))
    gates = " ".join(g["text"] for g in res["gates"])
    assert "Rent: ₹50,000/month" in gates or "₹50,000" in gates  # 40k rent + 10k maintenance vs 45k max
    assert "Floor" in gates  # buyer must have ground floor
    f = {x["key"]: x for x in res["factors"]}
    assert f["parking"]["result"] == "partial" and f["floor_level"]["result"] == "miss"
    assert "v0.2" in M.RULES_VERSION


def test_record_shows_what_is_still_missing_and_sends_just_those(app, db, login):
    c, opp = new_seller(login, db)
    page = c.get(f"/sellers/{opp}").data.decode()
    assert "Still to collect" in page and "Monthly maintenance" not in page or "maintenance" in page.lower()
    assert "Shop front from across the road" in page  # photos not yet received
    r = c.post(f"/forms/send/{opp}", {"template_key": "targeted", "channel": "copy_link",
                                      "fields": ["maintenance_monthly", "floor_level"], "docs": ["photo_front"]})
    assert r.status_code == 200
    inv = db.one("SELECT * FROM form_invitations WHERE template_key = 'targeted'")
    assert json.loads(inv["field_keys"]) == ["maintenance_monthly", "floor_level"]
    assert json.loads(inv["doc_types"]) == ["photo_front"]
