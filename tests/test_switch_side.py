"""Choosing which form to send, and moving a lead to the right side (a salon owner who answered a buyer ad)."""
from datetime import timedelta
from urllib.parse import unquote

from conftest import user

from teamos.domain import buyers as B
from teamos.domain import sellers as S
from teamos.domain import switch_side as SW
from teamos.domain import tasks as T
from teamos.timeutil import now_utc, to_iso


def _buyer(db, phone="9300500001", owner="buyer@t.test"):
    admin = user(db, "admin@t.test")
    oid = B.create_lead(db, admin, {"full_name": "Ravi Kumar", "phone": phone, "city": "Hyderabad",
                                    "locality": "Kukatpally", "source": "meta_lead", "consent_contact": "1",
                                    "owner_id": str(user(db, owner)["id"])})["opportunity_id"]
    db.execute("UPDATE opportunities SET lead_temperature = 'hot', team_remark = 'owns a salon, wants out', "
               "lead_created_at = ? WHERE id = ?", (to_iso(now_utc() - timedelta(days=3)), oid))
    return oid


def test_a_buyer_who_is_really_a_salon_owner_moves_with_all_history(db, login):
    bid = _buyer(db)
    c = login("buyer@t.test")
    c.post(f"/buyers/{bid}/call", {"outcome": "connected", "next_title": "Call back with price idea",
                                   "next_due": (now_utc() + timedelta(days=1)).strftime("%Y-%m-%dT11:00")})
    page = c.get(f"/buyers/{bid}").data.decode()
    assert "Wrong list?" in page and "Move to Salon Owners" in page and "📝 Send form" in page
    form = c.get(f"/records/{bid}/move?to=seller").data.decode()
    assert "Salon name" in form and "Wants to sell" in form
    # salon name is needed on the salon-owner side
    r = c.post(f"/records/{bid}/move", {"to": "seller", "city": "Hyderabad", "locality": "Kukatpally"})
    assert r.status_code == 200 and "Salon name is required" in r.data.decode()
    r = c.post(f"/records/{bid}/move", {"to": "seller", "salon_name": "Glow Unisex", "city": "Hyderabad",
                                        "locality": "Kukatpally", "lead_category": ["sell"],
                                        "reason": "answered the investor ad but owns a salon"})
    sid = db.scalar("SELECT id FROM opportunities WHERE kind = 'seller'")
    assert r.status_code == 302 and r.headers["Location"].endswith(f"/sellers/{sid}")
    new, old = db.one("SELECT * FROM opportunities WHERE id = ?", (sid,)), db.one("SELECT * FROM opportunities WHERE id = ?", (bid,))
    # same person, nothing lost
    assert new["contact_id"] == old["contact_id"] and db.scalar("SELECT COUNT(*) FROM contacts") == 1
    assert (new["lead_temperature"], new["team_remark"], new["lead_category"]) == ("hot", "owns a salon, wants out", "sell")
    assert new["lead_created_at"] == old["lead_created_at"] and new["first_contact_at"] == old["first_contact_at"]
    assert new["stage"] == "connected" and new["primary_owner_id"] == user(db, "buyer@t.test")["id"]
    # the old record is closed (not deleted) and points to the new one
    assert (old["status"], old["stage"]) == ("closed", "moved") and "SL-" in old["closed_reason"]
    # history and the open follow-up travel; no duplicate 'First call' task
    acts = [a["body"] or "" for a in db.all("SELECT body FROM activities WHERE entity_id = ? AND entity_type = 'opportunity'", (sid,))]
    assert any("Moved here from Buyers" in a and "investor ad" in a for a in acts)
    assert db.scalar("SELECT COUNT(*) FROM activities WHERE entity_id = ? AND kind = 'call'", (sid,)) == 1
    open_t = db.all("SELECT title, task_type FROM tasks WHERE entity_id = ? AND status = 'open'", (sid,))
    assert [t["title"] for t in open_t] == ["Call back with price idea"]
    assert not db.all("SELECT 1 FROM tasks WHERE entity_id = ? AND status = 'open'", (bid,))
    # the old profile says where they went; the seller list shows the new record, the buyer list hides the old one
    assert "open SL-" in c.get(f"/buyers/{bid}").data.decode()
    assert "Glow Unisex" in c.get("/sellers/").data.decode()
    assert "Ravi Kumar" not in c.get("/buyers/").data.decode()
    # moving again is refused
    r = c.post(f"/records/{bid}/move", {"to": "seller", "salon_name": "X", "city": "H", "locality": "K"})
    assert "already moved" in r.data.decode()


def test_keep_both_and_move_back_the_other_way(db, login):
    admin = user(db, "admin@t.test")
    sid = S.create_lead(db, admin, {"full_name": "Anji", "phone": "9300500009", "salon_name": "Morning Kisses",
                                    "city": "Hyderabad", "locality": "Kondapur", "source": "manual_call",
                                    "consent_contact": "1", "owner_id": str(admin["id"])})["opportunity_id"]
    new_id = SW.move(db, admin, sid, "buyer", {"city": "Hyderabad", "lead_category": ["buy_salon"]}, keep_old=True)
    old = db.one("SELECT * FROM opportunities WHERE id = ?", (sid,))
    assert old["status"] == "active" and old["stage"] != "moved"                  # both stay open
    assert db.scalar("SELECT kind FROM opportunities WHERE id = ?", (new_id,)) == "buyer"
    assert db.scalar("SELECT lead_category FROM opportunities WHERE id = ?", (new_id,)) == "buy_salon"
    assert db.scalar("SELECT COUNT(*) FROM tasks WHERE entity_id = ? AND status = 'open'", (sid,)) == 1  # kept


def test_send_form_offers_every_form_and_opens_whatsapp(db, login):
    bid = _buyer(db, "9300500002")
    c = login("buyer@t.test")
    page = c.get(f"/buyers/{bid}").data.decode()
    for _k, _s, label, _w in SW.FORMS:
        assert label in page
    # a buyer form goes straight to WhatsApp with their personal link
    r = c.post(f"/records/{bid}/send-form", {"form": "buyer_full"})
    assert r.status_code == 302 and r.headers["Location"].startswith("https://wa.me/919300500002?text=")
    assert "/f/i/" in unquote(r.headers["Location"]) and "saved as you go" in unquote(r.headers["Location"])
    r = c.post(f"/records/{bid}/send-form", {"form": "investor"})
    assert "/s/" in unquote(r.headers["Location"])
    assert db.scalar("SELECT COUNT(*) FROM activities WHERE entity_id = ? AND body LIKE 'Sent on WhatsApp:%'", (bid,)) == 2
    # a salon-owner form on a buyer record: move first, then the form is ready on the new profile
    r = c.post(f"/records/{bid}/send-form", {"form": "owner_checkup"})
    assert r.status_code == 302 and "/move?to=seller&then=owner_checkup" in r.headers["Location"]
    page = c.get(f"/records/{bid}/move?to=seller&then=owner_checkup").data.decode()
    assert "then send" in page and 'value="help" checked' in page
    r = c.post(f"/records/{bid}/move", {"to": "seller", "then": "owner_checkup", "salon_name": "Glow",
                                        "city": "Hyderabad", "locality": "Kukatpally", "lead_category": ["help"]})
    sid = db.scalar("SELECT id FROM opportunities WHERE kind = 'seller'")
    assert r.headers["Location"].endswith(f"/sellers/{sid}?send=owner_checkup#send-form")
    page = c.get(f"/sellers/{sid}?send=owner_checkup").data.decode()
    assert 'id="send-form" open' in page and 'form-choice chosen' in page
    r = c.post(f"/records/{sid}/send-form", {"form": "owner_checkup"})
    assert "/s/" in unquote(r.headers["Location"]) and "SWOT" in unquote(r.headers["Location"])
    assert c.post(f"/records/{sid}/send-form", {"form": "nope"}).status_code == 302


def test_resending_a_detailed_form_keeps_what_they_already_typed(db, login, app):
    from teamos.domain import forms as F
    sid = S.create_lead(db, user(db, "admin@t.test"), {
        "full_name": "Anji", "phone": "9300500011", "salon_name": "Morning Kisses", "city": "Hyderabad",
        "locality": "Kondapur", "source": "manual_call", "consent_contact": "1",
        "owner_id": str(user(db, "seller@t.test")["id"])})["opportunity_id"]
    c = login("seller@t.test")
    c.post(f"/records/{sid}/send-form", {"form": "owner_sell"})
    inv1 = db.one("SELECT * FROM form_invitations WHERE opportunity_id = ?", (sid,))
    now = to_iso(now_utc())
    did = db.insert("form_submissions", {"invitation_id": inv1["id"], "template_key": "seller_deep",
                                         "template_version": inv1["template_version"], "kind": "seller",
                                         "opportunity_id": sid, "status": "draft", "answers": '{"chairs": 6}',
                                         "created_at": now, "updated_at": now}) if _has_answers(db) else None
    c.post(f"/records/{sid}/send-form", {"form": "owner_sell"})
    inv2 = db.one("SELECT * FROM form_invitations WHERE opportunity_id = ? ORDER BY id DESC", (sid,))
    assert inv2["id"] != inv1["id"]
    assert db.scalar("SELECT status FROM form_invitations WHERE id = ?", (inv1["id"],)) == "revoked"
    if did:
        assert F.draft_for(db, inv2)["id"] == did and inv2["status"] == "started"


def _has_answers(db):
    cols = [r["name"] for r in db.all("PRAGMA table_info(form_submissions)")]
    return "answers" in cols and "updated_at" in cols


def test_a_lead_not_called_yet_keeps_exactly_one_first_call(db):
    admin = user(db, "admin@t.test")
    bid = B.create_lead(db, admin, {"full_name": "Sita", "phone": "9300500021", "city": "Hyderabad",
                                    "source": "meta_lead", "consent_contact": "1",
                                    "owner_id": str(admin["id"])})["opportunity_id"]
    sid = SW.move(db, admin, bid, "seller", {"salon_name": "Sita Beauty", "city": "Hyderabad", "locality": "Ameerpet"})
    tasks = db.all("SELECT title FROM tasks WHERE entity_type = 'opportunity' AND entity_id IN (?, ?) AND "
                   "status = 'open'", (bid, sid))
    assert [t["title"] for t in tasks] == ["First call: Sita (Sita Beauty)"]


def test_a_moved_lead_counts_once_in_todays_numbers(db):
    from teamos.domain import dashboard as DB
    admin = user(db, "admin@t.test")
    bid = B.create_lead(db, admin, {"full_name": "Sita", "phone": "9300500031", "city": "Hyderabad",
                                    "source": "meta_lead", "consent_contact": "1",
                                    "owner_id": str(admin["id"])})["opportunity_id"]
    assert DB.numbers(db, [admin["id"]])["new_leads"] == 1
    SW.move(db, admin, bid, "seller", {"salon_name": "Sita Beauty", "city": "Hyderabad", "locality": "Ameerpet"})
    assert DB.numbers(db, [admin["id"]])["new_leads"] == 1
