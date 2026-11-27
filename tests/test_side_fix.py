"""A salon owner who also sits in a mixed call list must not become a buyer too — and the ones an older import already
doubled are folded into their owner record (nothing deleted)."""
import json
from datetime import timedelta

from conftest import user

from teamos.domain import buyers as B
from teamos.domain import sellers as S
from teamos.domain import sheet_sync as SS
from teamos.domain import switch_side as SW
from teamos.timeutil import now_utc


def _row(phone, kind, tab, **canon):
    canon.setdefault("full_name", "Someone")
    return {"phone": phone, "action": "create", "kind": kind, "flavour": "seek_help" if kind == "seller" else "new_salon",
            "tab": tab, "canon": canon, "reason": None, "understood": SS.understand(kind, None, canon)}


def test_owner_in_a_mixed_call_list_stays_an_owner():
    rows = [_row("+919000000001", "seller", "Struggling Salons", describes_you="i_own_a_salon__"),
            _row("+919000000001", "buyer", "Sheet2", salon_name="Glow", timeline="male hairdresser"),   # not a time
            _row("+919000000002", "seller", "Raw Responses"),
            _row("+919000000002", "buyer", "Sheet1", budget="₹8–15 lakh", timeline="within 30 days"),     # really both
            _row("+919000000003", "buyer", "Sheet2")]                                                   # only a buyer
    SS._one_side_per_person(rows)
    assert [r["kind"] for r in rows] == ["seller", "seller", "seller", "buyer", "buyer"]
    assert "Also on a salon-owner list ('Struggling Salons')" in rows[1]["reason"]


def _same_person(db, phone="9300800001"):
    admin = user(db, "admin@t.test")
    sid = S.create_lead(db, admin, {"full_name": "Himesh", "phone": phone, "salon_name": "Boxing", "city": "Hyderabad",
                                    "locality": "Kondapur", "source": "import", "consent_contact": "1",
                                    "owner_id": str(admin["id"])})["opportunity_id"]
    contact = db.scalar("SELECT contact_id FROM opportunities WHERE id = ?", (sid,))
    bid = B.create_lead(db, admin, {"full_name": "Himesh", "phone": phone, "city": "Hyderabad", "source": "import",
                                    "consent_contact": "1", "owner_id": str(admin["id"])},
                        dedupe_decision=f"link:{contact}")["opportunity_id"]
    return admin, sid, bid


def test_doubled_people_are_folded_into_their_owner_record(db, login):
    admin, sid, bid = _same_person(db)
    c = login("admin@t.test")
    c.post(f"/buyers/{bid}/call", {"outcome": "connected", "notes": "owns Boxing salon, wants more clients",
                                   "next_title": "Call back", "next_due": (now_utc() + timedelta(days=1)).strftime("%Y-%m-%dT11:00")})
    db.execute("UPDATE opportunities SET lead_temperature = 'hot', team_remark = 'lease issue' WHERE id = ?", (bid,))
    dry = SW.fix_side_duplicates(db, admin, ["+919300800001", "+919999999999"], dry=True)
    assert dry["merged"] == [f"BY-{bid:05d} → SL-{sid:05d}"] and dry["not_found"] == ["+919999999999"]
    assert db.scalar("SELECT status FROM opportunities WHERE id = ?", (bid,)) == "active"           # dry: nothing done
    SW.fix_side_duplicates(db, admin, ["+919300800001"], dry=False, why="a salon owner (pilot Tracker)")
    b, s = db.one("SELECT * FROM opportunities WHERE id = ?", (bid,)), db.one("SELECT * FROM opportunities WHERE id = ?", (sid,))
    assert (b["status"], b["stage"]) == ("closed", "moved") and "Same person as SL-" in b["closed_reason"]
    assert s["lead_temperature"] == "hot" and "lease issue" in s["team_remark"] and s["first_contact_at"]
    assert db.scalar("SELECT COUNT(*) FROM activities WHERE entity_id = ? AND kind = 'call'", (sid,)) == 1
    assert db.scalar("SELECT COUNT(*) FROM tasks WHERE entity_id = ? AND status = 'open' AND title = 'Call back'", (sid,)) == 1
    assert db.scalar("SELECT COUNT(*) FROM tasks WHERE entity_id = ? AND status = 'open'", (bid,)) == 0
    assert db.scalar("SELECT COUNT(*) FROM opportunities") == 2                                     # nothing deleted
    # running it again changes nothing
    assert SW.fix_side_duplicates(db, admin, ["+919300800001"], dry=False)["merged"] == []


def test_a_buyer_record_with_real_buyer_details_is_kept(db):
    admin, sid, bid = _same_person(db, "9300800002")
    db.execute("UPDATE buyer_mandates SET budget_max = 2000000 WHERE opportunity_id = ?", (bid,))
    res = SW.fix_side_duplicates(db, admin, ["+919300800002"], dry=False)
    assert res["merged"] == [] and "has its own buyer details" in res["kept_both"][0]
    assert db.scalar("SELECT status FROM opportunities WHERE id = ?", (bid,)) == "active"


def test_the_clean_up_runs_as_a_job(app, db):
    from teamos import jobs
    admin, sid, bid = _same_person(db, "9300800003")
    jobs.enqueue(db, "fix_side_duplicates", {"phones": ["+919300800003"], "dry": False, "why": "a salon owner"})
    jobs.run_once(db)
    assert db.scalar("SELECT stage FROM opportunities WHERE id = ?", (bid,)) == "moved"
    msg = db.scalar("SELECT message FROM job_runs ORDER BY id DESC LIMIT 1")
    assert msg.startswith("Merged 1;") and json.loads(msg.split(" · ", 1)[1])["merged"]
    assert db.scalar("SELECT COUNT(*) FROM notifications WHERE title LIKE 'Duplicate clean-up:%'") >= 1


def test_an_owner_record_with_real_owner_details_is_kept(db):
    admin, sid, bid = _same_person(db, "9300800004")
    db.execute("UPDATE seller_listings SET asking_price = 1500000 WHERE opportunity_id = ?", (sid,))
    res = SW.fix_side_duplicates(db, admin, ["+919300800004"], keep="buyer", dry=False)
    assert res["merged"] == [] and "has its own seller details" in res["kept_both"][0]
    res = SW.fix_side_duplicates(db, admin, ["+919300800004"], keep="seller", dry=False)   # the other way is fine
    assert len(res["merged"]) == 1
