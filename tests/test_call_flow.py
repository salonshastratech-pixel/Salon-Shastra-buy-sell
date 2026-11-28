"""How a caller's day really goes on a salon owner / buyer record: no answer, not interested, reopened, handed over,
notes that show, details saved once."""
from conftest import user

from teamos.domain import buyers as B
from teamos.domain import sellers as S


def _seller(db, phone="9300770001", owner="seller@t.test"):
    admin = user(db, "admin@t.test")
    return S.create_lead(db, admin, {"full_name": "Lakshmi", "phone": phone, "salon_name": "Glow", "city": "Hyderabad",
                                     "locality": "Kondapur", "source": "manual_call", "consent_contact": "1",
                                     "owner_id": str(user(db, owner)["id"])})["opportunity_id"]


def _buyer(db, phone="9300770002", owner="buyer@t.test"):
    admin = user(db, "admin@t.test")
    return B.create_lead(db, admin, {"full_name": "Ravi", "phone": phone, "city": "Hyderabad", "source": "meta_lead",
                                     "consent_contact": "1", "owner_id": str(user(db, owner)["id"])})["opportunity_id"]


def _open(db, oid):
    return db.all("SELECT * FROM tasks WHERE entity_type = 'opportunity' AND entity_id = ? AND status = 'open'", (oid,))


def test_no_answer_plans_the_retry_itself(db, login):
    for oid, base, who in ((_seller(db), "sellers", "seller@t.test"), (_buyer(db), "buyers", "buyer@t.test")):
        c = login(who)
        first = _open(db, oid)[0]["id"]
        r = c.post(f"/{base}/{oid}/call", {"outcome": "no_answer", "note": "rang out", "complete_task_id": str(first)})
        assert r.status_code == 302
        ts = _open(db, oid)
        assert len(ts) == 1 and ts[0]["title"] == "Try again — last time: no answer" and ts[0]["due_at"]
        assert db.scalar("SELECT stage FROM opportunities WHERE id = ?", (oid,)) == "contact_attempted"


def test_not_interested_without_next_step_closes_with_the_note(db, login):
    oid = _seller(db)
    c = login("seller@t.test")
    c.post(f"/sellers/{oid}/call", {"outcome": "not_interested", "note": "son will run the salon"})
    assert db.one("SELECT stage, status FROM opportunities WHERE id = ?", (oid,)) == {"stage": "closed_lost",
                                                                                      "status": "closed"}
    assert "son will run" in db.scalar("SELECT reason FROM stage_history WHERE opportunity_id = ? ORDER BY id DESC", (oid,))
    assert _open(db, oid) == []
    bid = _buyer(db)
    c = login("buyer@t.test")
    c.post(f"/buyers/{bid}/call", {"outcome": "not_interested", "note": "", "next_title": "Try in 3 months",
                                   "next_due": "2027-03-01T11:00"})
    assert db.scalar("SELECT status FROM opportunities WHERE id = ?", (bid,)) == "active"   # a next step keeps it open


def test_reopened_record_gets_a_next_step_and_blocked_buyer_move_explains(db, login):
    for oid, base, who in ((_seller(db), "sellers", "seller@t.test"), (_buyer(db), "buyers", "buyer@t.test")):
        c = login(who)
        c.post(f"/{base}/{oid}/stage", {"to": "closed_lost", "reason": "another broker"})
        assert _open(db, oid) == []
        c.post(f"/{base}/{oid}/stage", {"to": "connected"})
        ts = _open(db, oid)
        assert db.scalar("SELECT status FROM opportunities WHERE id = ?", (oid,)) == "active" and len(ts) == 1
    r = c.post(f"/buyers/{oid}/stage", {"to": "closed_won", "reason": "x"})
    assert r.status_code == 302                                  # refused with a message, never a crash


def test_hand_over_moves_the_open_tasks(db, login):
    oid = _seller(db)
    other = user(db, "buyer@t.test")
    c = login("admin@t.test")
    c.post(f"/sellers/{oid}/assign", {"owner_id": str(other["id"]), "reason": "buyer side"})
    assert {t["assignee_id"] for t in _open(db, oid)} == {other["id"]}
    assert "moved to them" in db.scalar("SELECT body FROM activities WHERE entity_id = ? AND kind = 'assignment'", (oid,))


def test_notes_and_calls_show_on_the_profile(db, login):
    oid = _seller(db)
    c = login("seller@t.test")
    c.post(f"/sellers/{oid}/note", {"body": "husband decides the price"})
    c.post(f"/sellers/{oid}/call", {"outcome": "connected", "note": "six chairs, rent 45k", "next_title": "Call back",
                                    "next_due": "2027-01-05T16:00"})
    page = c.get(f"/sellers/{oid}").data.decode()
    assert "husband decides the price" in page and "six chairs, rent 45k" in page


def test_city_typed_twice_and_untouched_fields_are_not_changes(db, login):
    oid = _seller(db)
    c = login("seller@t.test")
    c.post(f"/records/{oid}/basics", {"full_name": "Lakshmi", "phone": "9300770001", "salon_name": "Glow",
                                      "city": "HyderabadHyderabad", "locality": "Kondapur  Kondapur",
                                      "lead_temperature": "normal"})
    assert db.one("SELECT city, locality FROM salon_businesses WHERE id = (SELECT business_id FROM opportunities "
                  "WHERE id = ?)", (oid,)) == {"city": "Hyderabad", "locality": "Kondapur"}
    assert db.scalar("SELECT COUNT(*) FROM activities WHERE entity_id = ? AND kind = 'data_update'", (oid,)) == 0
    new = c.post("/sellers/new", {"full_name": "Kiran", "phone": "9300770009", "salon_name": "Kiran Cuts",
                                  "city": "Hyderabad Hyderabad", "locality": "Ameerpet", "source": "manual_call",
                                  "consent_contact": "1"})
    assert new.status_code == 302
    assert db.scalar("SELECT city FROM salon_businesses WHERE name = 'Kiran Cuts'") == "Hyderabad"
