"""Duplicates: the same person twice on the same side is folded into one record — nothing lost — and the open public
forms never make a new record for someone already with us (saving while they type, retries, a second visit)."""
import re

from conftest import user

from teamos.domain import buyers as B
from teamos.domain import sellers as S
from teamos.domain import switch_side as SW
from teamos.domain import tasks as T


def _owner_twice(db, phone="9300990001"):
    """What older imports left on live: two contacts with one number, each with its own salon-owner record."""
    admin = user(db, "admin@t.test")
    a = S.create_lead(db, admin, {"full_name": "Himesh", "phone": phone, "salon_name": "Boxing", "city": "Hyderabad",
                                  "locality": "Kondapur", "source": "import", "consent_contact": "1",
                                  "owner_id": str(admin["id"]), "asking_price": "1500000",
                                  "sale_purpose": "sell_entire"})["opportunity_id"]
    b = S.create_lead(db, admin, {"full_name": "Himesh B", "phone": phone, "salon_name": "Boxing Salon",
                                  "city": "Hyderabad", "locality": "Kondapur East", "source": "import",
                                  "consent_contact": "1", "owner_id": str(admin["id"])},
                      dedupe_decision="new_flagged")["opportunity_id"]
    return admin, a, b


def test_two_owner_records_become_one_with_everything_kept(db, login):
    admin, a, b = _owner_twice(db)
    la, lb = S.load(db, a)["listing_id"], S.load(db, b)["listing_id"]
    db.execute("UPDATE seller_listings SET chairs = 6, monthly_rent = 45000 WHERE id = ?", (lb,))   # only on b
    db.execute("UPDATE seller_listings SET asking_price = 1800000 WHERE id = ?", (lb,))            # differs from a
    c = login("admin@t.test")
    c.post(f"/sellers/{b}/call", {"outcome": "connected", "notes": "wants 18 lakh now"})
    T.create_task(db, admin, title="Visit Boxing", assignee_id=admin["id"], due_at=None, task_type="visit",
                  entity_type="opportunity", entity_id=b)
    assert db.scalar("SELECT COUNT(*) FROM duplicate_reviews WHERE status = 'open'") >= 1
    dry = SW.merge_duplicates(db, admin, dry=True)
    assert dry["people"] == 1 and len(dry["merged"]) == 1
    assert db.scalar("SELECT status FROM opportunities WHERE id = ?", (b,)) == "active"          # dry: untouched
    res = SW.merge_duplicates(db, admin, dry=False)
    keep, gone = (b, a) if res["merged"][0].startswith(f"SL-{a:05d}") else (a, b)
    kept = S.load_listing(db, S.load(db, keep)["listing_id"])
    assert kept["chairs"] == 6 and kept["monthly_rent"] == 45000                                  # filled in
    assert db.one("SELECT stage, status FROM opportunities WHERE id = ?", (gone,)) == {"stage": "moved", "status": "closed"}
    assert db.scalar("SELECT COUNT(*) FROM activities WHERE entity_id = ? AND kind = 'call'", (keep,)) == 1
    assert db.scalar("SELECT COUNT(*) FROM tasks WHERE entity_id = ? AND title = 'Visit Boxing' AND status = 'open'", (keep,)) == 1
    clash = db.scalar("SELECT body FROM activities WHERE entity_id = ? AND body LIKE 'From the duplicate record%'", (keep,))
    assert clash and "asking price" in clash                                                     # differing value kept as a note
    assert db.scalar("SELECT COUNT(*) FROM duplicate_reviews WHERE status = 'open'") == 0
    assert db.scalar("SELECT COUNT(*) FROM opportunities") == 2                                   # nothing deleted
    assert SW.merge_duplicates(db, admin, dry=True)["merged"] == []                              # nothing left


def test_record_in_a_deal_is_left_for_review(db):
    admin, a, b = _owner_twice(db, "9300990002")
    db.execute("UPDATE opportunities SET status = 'closed' WHERE id = ?", (a,))
    keep_first = SW.merge_duplicates(db, admin, dry=True)
    assert len(keep_first["merged"]) == 1 and keep_first["merged"][0].endswith(f"SL-{b:05d}")    # the open one stays


def test_public_forms_never_make_a_second_record(app, db):
    c = app.test_client()
    for path, name_key, phone_key, kind in (("/f/investor", "full_name", "phone", "buyer"),
                                             ("/f/pilot", "owner_name", "contact_number", "seller")):
        page = c.get(path).data.decode()
        tok = re.search(r'name="form_token" value="([^"]+)"', page).group(1)
        base = {"form_token": tok, name_key: "Ashok", phone_key: "9300990011" if kind == "buyer" else "9300990012",
                "consent_contact": "1"}
        for _ in range(3):                         # the page saving while they type, never learning its draft key
            c.post(path + "/save", data=base)
        c.post(path, data=base)                    # and submitting at the end
        c.post(path, data=base)                    # and once more, the next day
        n = db.scalar("SELECT COUNT(*) FROM opportunities o JOIN contacts ct ON ct.id = o.contact_id WHERE "
                      "ct.phone_normalized = ? AND o.kind = ?", ("+91" + base[phone_key], kind))
        assert n == 1, path


def test_public_form_adds_to_a_record_the_team_already_has(app, db):
    admin = user(db, "admin@t.test")
    bid = B.create_lead(db, admin, {"full_name": "Ravi", "phone": "9300990021", "city": "Hyderabad",
                                    "source": "manual_call", "consent_contact": "1",
                                    "owner_id": str(admin["id"])})["opportunity_id"]
    c = app.test_client()
    tok = re.search(r'name="form_token" value="([^"]+)"', c.get("/f/investor").data.decode()).group(1)
    r = c.post("/f/investor", data={"form_token": tok, "full_name": "Ravi K", "phone": "9300990021",
                                    "consent_contact": "1"})
    assert r.status_code == 302
    assert db.scalar("SELECT COUNT(*) FROM opportunities WHERE kind = 'buyer'") == 1
    assert db.scalar("SELECT full_name FROM contacts") == "Ravi"                               # nothing overwritten
    assert "added to this record" in db.scalar("SELECT body FROM activities WHERE entity_id = ? AND body LIKE "
                                               "'Filled the Investor form%'", (bid,))


def test_an_expired_public_form_keeps_every_answer(app, db, monkeypatch):
    from teamos.web import public
    c = app.test_client()
    r = c.post("/f/investor", data={"form_token": "expired-or-tampered", "full_name": "Ashok B", "phone": "9300990031",
                                    "areas": "Kondapur, Gachibowli", "consent_contact": "1"})
    page = r.data.decode()
    assert r.status_code == 200 and "Your answers are kept" in page
    assert "Ashok B" in page and "Kondapur, Gachibowli" in page and 'name="form_token" value="' in page
    assert public.FORM_TOKEN_MAX_AGE >= 12 * 3600


def test_session_errors_explain_and_number_fields_are_marked(app, login):
    c = login("seller@t.test")
    r = c.post("/sellers/new", {"full_name": "x"}, csrf=False)
    assert r.status_code == 400, r.status_code
    assert "everything you typed is kept" in r.data.decode()
    page = c.get("/sellers/new").data.decode()
    assert 'name="asking_price"' in page and re.search(r'name="asking_price"[^>]*data-num="int"', page)
    js = c.get("/static/app.js").data.decode()
    assert "teamos-draft:" in js and "Numbers only" in js


def test_placeholder_salon_names_are_not_duplicates(db):
    admin = user(db, "admin@t.test")
    for i, ph in enumerate(("9300990041", "9300990042")):
        S.create_lead(db, admin, {"full_name": f"Owner {i}", "phone": ph, "salon_name": "Salon name not given",
                                  "city": "Hyderabad", "locality": "Area not given", "source": "import",
                                  "consent_contact": "1", "owner_id": str(admin["id"])})
    assert S.find_duplicates(db, None, "Salon name not given", "Area not given") == []
    assert S.find_duplicates(db, None, "Hyderabad", "Hyderabad") == []
    assert db.scalar("SELECT COUNT(*) FROM duplicate_reviews WHERE status = 'open'") == 0
