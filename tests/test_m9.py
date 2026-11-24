"""Milestone 9: tap-to-call, calendar + private feed, Facebook / Instagram lead ads (test-only until live)."""
import hashlib
import hmac
import json
from datetime import timedelta

from conftest import user
from test_matching import seller

from teamos.domain import calendar as C
from teamos.domain import leadads as LA
from teamos.timeutil import now_utc, to_iso


# -- tap-to-call ------------------------------------------------------------------------------------------------
def test_record_page_has_tap_to_call_and_jumps_to_log_a_call(db, login):
    s = seller(db, phone="9000011001")
    page = login("seller@t.test").get(f"/sellers/{s}").data.decode()
    assert 'href="tel:+919000011001"' in page and 'id="log-call"' in page and "data-call-log" in page


def test_do_not_contact_has_no_call_link(db, login):
    s = seller(db, phone="9000011001")
    db.execute("UPDATE contacts SET do_not_contact = 1")
    assert "tel:+91" not in login("seller@t.test").get(f"/sellers/{s}").data.decode()


# -- calendar ---------------------------------------------------------------------------------------------------
def test_calendar_shows_my_tasks_this_week(db, login):
    seller(db)
    page = login("seller@t.test").get("/calendar").data.decode()
    assert "Calendar" in page and "week of" in page


def test_calendar_feed_is_private_revocable_and_has_no_customer_details(db, login, app):
    s = seller(db, phone="9000011777", salon="Private Name Salon")
    me = user(db, "seller@t.test")
    db.insert("tasks", {"title": "Call Owner Private Name Salon about rent", "task_type": "call", "status": "open",
                        "priority": "normal", "due_at": to_iso(now_utc() + timedelta(days=1)), "assignee_id": me["id"],
                        "creator_id": me["id"], "entity_type": "opportunity", "entity_id": s,
                        "created_at": to_iso(now_utc()), "updated_at": to_iso(now_utc())})
    c = login("seller@t.test")
    page = c.post("/calendar/link").data.decode()
    assert "/cal/" in page
    token = page.split("/cal/")[1].split(".ics")[0]
    anon = app.test_client()
    ics = anon.get(f"/cal/{token}.ics")
    assert ics.status_code == 200 and ics.mimetype == "text/calendar"
    body = ics.data.decode()
    assert "BEGIN:VCALENDAR" in body and "BEGIN:VEVENT" in body
    assert "9000011777" not in body and "Private Name" not in body and "about rent" not in body
    assert "Call" in body and "SL-" in body
    assert db.scalar("SELECT COUNT(*) FROM calendar_feeds WHERE token_hash = ?", (token,)) == 0  # only a hash stored
    c.post("/calendar/link/revoke")
    assert anon.get(f"/cal/{token}.ics").status_code == 404
    assert anon.get("/cal/not-a-real-token.ics").status_code == 404


def test_team_sees_each_others_calendars(db, login):
    s_id = user(db, "seller@t.test")["id"]
    lead = login("lead@t.test").get(f"/calendar?who={s_id}").data.decode()
    assert "Shaheen Seller" in lead
    me = login("buyer@t.test").get(f"/calendar?who={s_id}").data.decode()
    assert '<select name="who"' in me  # D-85: team = everything except Finance


def test_week_start_is_monday():
    assert C.week_start("2026-11-26").isoformat() == "2026-11-23"
    assert C.week_start("nonsense").weekday() == 0


# -- lead ads ---------------------------------------------------------------------------------------------------
def test_simulated_buyer_lead_ad_becomes_a_buyer_with_first_call(db, login):
    r = login("admin@t.test").post("/admin/lead-ads", {"kind": "buyer", "full_name": "Lead Ad Person",
                                                       "phone_number": "9876501234", "city": "Hyderabad",
                                                       "area": "Kondapur", "what_is_your_budget?": "30-40 lakhs"},
                                   follow_redirects=True)
    assert "imported" in r.data.decode()
    ev = db.one("SELECT * FROM lead_ad_events ORDER BY id DESC LIMIT 1")
    opp = db.one("SELECT * FROM opportunities WHERE id = ?", (ev["opportunity_id"],))
    assert opp["kind"] == "buyer" and opp["primary_owner_id"]
    assert db.scalar("SELECT COUNT(*) FROM tasks WHERE entity_type = 'opportunity' AND entity_id = ? AND task_type = "
                     "'first_contact'", (opp["id"],)) == 1


def test_same_person_twice_is_not_duplicated(db):
    a = LA.simulate(db, "seller", {"full_name": "Twice Owner", "phone_number": "9876509999", "salon_name": "Twice Salon",
                                   "city": "Hyderabad"})
    b = LA.simulate(db, "seller", {"full_name": "Twice Owner", "phone_number": "9876509999", "monthly_rent": "40000",
                                   "city": "Hyderabad"})
    assert a["status"] == "imported" and b["status"] == "imported"
    assert db.scalar("SELECT COUNT(*) FROM contacts WHERE phone_normalized LIKE '%9876509999'") == 1


def test_lead_ad_needs_a_phone(db, login):
    r = login("admin@t.test").post("/admin/lead-ads", {"kind": "buyer", "full_name": "No Phone"})
    assert "always has a phone number" in r.data.decode()


def test_only_admins_reach_lead_ads(db, login):
    assert login("lead@t.test").get("/admin/lead-ads").status_code == 403


def test_webhook_needs_meta_signature_and_stores_each_lead_once(db, app):
    app.config["META_LEADS_APP_SECRET"] = "s3cret"
    c = app.test_client()
    payload = json.dumps({"entry": [{"changes": [{"field": "leadgen", "value": {"leadgen_id": "L1", "form_id": "F1"}}]}]})
    assert c.post("/webhooks/meta-leads", data=payload, content_type="application/json").status_code == 403
    sig = "sha256=" + hmac.new(b"s3cret", payload.encode(), hashlib.sha256).hexdigest()
    for _ in range(2):  # Meta retries: stored once
        r = c.post("/webhooks/meta-leads", data=payload, content_type="application/json",
                   headers={"X-Hub-Signature-256": sig})
        assert r.status_code == 200
    assert db.scalar("SELECT COUNT(*) FROM lead_ad_events WHERE leadgen_id = 'L1'") == 1
    # not live: no answers fetched, so nothing is created yet
    assert db.scalar("SELECT status FROM lead_ad_events WHERE leadgen_id = 'L1'") == "received"


def test_webhook_handshake(db, app):
    app.config["META_LEADS_VERIFY_TOKEN"] = "verify-me"
    c = app.test_client()
    ok = c.get("/webhooks/meta-leads?hub.mode=subscribe&hub.verify_token=verify-me&hub.challenge=42")
    assert ok.data == b"42"
    assert c.get("/webhooks/meta-leads?hub.mode=subscribe&hub.verify_token=wrong&hub.challenge=42").status_code == 403


def test_menu_has_calendar_and_tools_has_lead_ads(db, login):
    admin = login("admin@t.test")
    assert "Calendar" in admin.get("/").data.decode()
    assert "Lead ads" in admin.get("/tools").data.decode()
