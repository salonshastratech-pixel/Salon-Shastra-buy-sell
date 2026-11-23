"""Milestone 7.1 — one-click WhatsApp follow-ups per stage, and each team member's own WhatsApp number."""
import json
from datetime import timedelta

from conftest import SELLER_FORM, user
from test_matching import buyer

from teamos import messaging
from teamos.domain import inbox as I
from teamos.domain import quick_messages as Q
from teamos.timeutil import now_utc, to_iso


def new_seller(login, db, **extra):
    login("seller@t.test").post("/sellers/new", {**SELLER_FORM, **extra})
    return db.scalar("SELECT MAX(id) FROM opportunities")


def out_msgs(db):
    return db.all("SELECT * FROM messages WHERE direction = 'out' ORDER BY id")


def test_after_a_no_answer_call_the_callback_message_is_one_click_away(app, db, login):
    opp = new_seller(login, db)
    c = login("seller@t.test")
    r = c.post(f"/sellers/{opp}/call", {"outcome": "no_answer", "note": ""})
    assert r.status_code == 302 and "after_call=no_answer" in r.headers["Location"] and r.headers["Location"].endswith("#quick")
    page = c.get(f"/sellers/{opp}?after_call=no_answer").data.decode()
    assert "Call logged — send a WhatsApp follow-up" in page
    assert "Hello Test, this is Shaheen from Salon Shastra. We tried to reach you" in page  # filled, ready to send
    assert "Send on WhatsApp" in page and "test-only" in page
    c.post(f"/inbox/quick/{opp}", {"sid": "callback_request", "outcome": "no_answer"})
    m = out_msgs(db)
    assert len(m) == 1 and m[0]["template_key"] == "callback_request" and m[0]["opportunity_id"] == opp
    assert m[0]["body"].startswith("Hello Test, this is Shaheen")


def test_connected_seller_gets_thank_you_and_personal_form_link(app, db, login):
    opp = new_seller(login, db)
    c = login("seller@t.test")
    c.post(f"/sellers/{opp}/call", {"outcome": "connected"})
    with app.test_request_context():
        ids = [s["id"] for s in Q.suggest(db, user(db, "seller@t.test"), opp, "connected")]
    assert ids[:2] == ["call_thanks_seller", "form"]
    c.post(f"/inbox/quick/{opp}", {"sid": "form", "outcome": "connected"})
    inv = db.one("SELECT * FROM form_invitations WHERE opportunity_id = ?", (opp,))
    assert inv and inv["template_key"] == "seller_deep" and inv["status"] == "sent"
    m = out_msgs(db)[-1]
    assert m["template_key"] == "deep_form_link" and "[personal link]" in m["body"] and "/f/i/" not in m["body"]
    with app.test_request_context():  # a live link exists now: the reminder replaces "send form"
        ids = [s["id"] for s in Q.suggest(db, user(db, "seller@t.test"), opp)]
    assert "form" not in ids


def test_callback_requested_confirms_the_agreed_time(app, db, login):
    opp = new_seller(login, db)
    c = login("seller@t.test")
    c.post(f"/sellers/{opp}/call", {"outcome": "callback_requested", "next_title": "Call back",
                                    "next_due": "2099-01-05T16:30"})
    with app.test_request_context():
        s = Q.suggest(db, user(db, "seller@t.test"), opp, "callback_requested")
    assert [x["id"] for x in s] == ["callback_confirm"] and "05 Jan, 04:30 PM" in s[0]["preview"]


def test_stage_specific_messages_for_buyers(app, db, login):
    b = buyer(db, localities=("Madhapur", "Kondapur"))
    db.execute("UPDATE opportunities SET stage = 'qualified_shortlist' WHERE id = ?", (b,))
    with app.test_request_context():
        s = Q.suggest(db, user(db, "buyer@t.test"), b)
    assert s[0]["id"] == "shortlist_update" and "salons in Madhapur within your budget" in s[0]["preview"]
    assert not s[0]["blocks"]


def test_no_message_after_not_interested_or_without_consent_or_when_tampered(app, db, login):
    opp = new_seller(login, db)
    with app.test_request_context():
        assert Q.suggest(db, user(db, "seller@t.test"), opp, "not_interested") == []
        assert Q.suggest(db, user(db, "seller@t.test"), opp, "do_not_contact") == []
    no_consent = new_seller(login, db, phone="9876500000", consent_contact="", full_name="Other Owner", salon_name="Other Salon")
    assert no_consent != opp
    c = login("seller@t.test")
    page = c.get(f"/sellers/{no_consent}?after_call=no_answer").data.decode()
    assert "Can't send: " in page and "no consent to be contacted" in page
    r = c.post(f"/inbox/quick/{no_consent}", {"sid": "callback_request", "outcome": "no_answer"}, follow_redirects=True)
    assert b"Not sent" in r.data
    r = c.post(f"/inbox/quick/{opp}", {"sid": "listing_live"}, follow_redirects=True)  # not suggested at stage "new"
    assert b"no longer suggested" in r.data
    assert not out_msgs(db)
    # finance has no CRM rights: refused
    assert login("fin@t.test").post(f"/inbox/quick/{opp}", {"sid": "call_thanks_seller"}).status_code in (302, 403)
    assert not out_msgs(db)


def test_admin_connects_team_numbers_and_messages_use_them(app, db, login):
    admin = login("admin@t.test")
    sid = user(db, "seller@t.test")["id"]
    r = admin.post("/admin/integrations", {"action": "team_number", "user_id": sid, "wa_number": "98765 11111",
                                           "number_id": "abc"}, follow_redirects=True)
    assert b"phone number ID" in r.data
    admin.post("/admin/integrations", {"action": "team_number", "user_id": sid, "wa_number": "9876511111",
                                       "number_id": "109876543210"})
    assert db.scalar("SELECT wa_phone_number_id FROM users WHERE id = ?", (sid,)) == "109876543210"
    # the same number cannot be given to two people; team members cannot change numbers
    r = admin.post("/admin/integrations", {"action": "team_number", "user_id": user(db, "buyer@t.test")["id"],
                                           "wa_number": "9876511111", "number_id": "109876543210"}, follow_redirects=True)
    assert b"already connected to Shaheen" in r.data
    login("seller@t.test").post("/admin/integrations", {"action": "team_number", "user_id": sid, "wa_number": "",
                                                        "number_id": ""})
    assert db.scalar("SELECT wa_phone_number_id FROM users WHERE id = ?", (sid,)) == "109876543210"
    assert "Team WhatsApp numbers" in admin.get("/admin/integrations").data.decode()
    # Shaheen's follow-up goes from Shaheen's number
    opp = new_seller(login, db)
    c = login("seller@t.test")
    assert "Shaheen&#39;s WhatsApp (+919876511111)" in c.get(f"/sellers/{opp}?after_call=no_answer").data.decode()
    c.post(f"/inbox/quick/{opp}", {"sid": "callback_request", "outcome": "no_answer"})
    m = out_msgs(db)[-1]
    assert m["from_number_id"] == "109876543210"
    assert db.scalar("SELECT business_number_id FROM conversations WHERE id = ?", (m["conversation_id"],)) == "109876543210"


def test_customer_writing_to_a_team_number_lands_with_that_person(app, db, login):
    sid = user(db, "seller@t.test")["id"]
    db.update("users", sid, {"wa_number": "+919876511111", "wa_phone_number_id": "109876543210"})
    payload = {"entry": [{"changes": [{"value": {"metadata": {"phone_number_id": "109876543210"},
                                                 "messages": [{"id": "wamid.T1", "from": "919812399999",
                                                               "timestamp": str(int(now_utc().timestamp())),
                                                               "text": {"body": "Hi Shaheen, about my salon"}}]}}]}]}
    with app.test_request_context():
        I.ingest_webhook(db, payload)
    conv = db.one("SELECT * FROM conversations WHERE phone = '+919812399999'")
    assert conv["assigned_to"] == sid and conv["business_number_id"] == "109876543210"
    # the reply goes back from the number the customer wrote to
    with app.test_request_context():
        I.reply_unmatched(db, user(db, "lead@t.test"), conv["id"], "Thank you! May I know your name?")
    assert out_msgs(db)[-1]["from_number_id"] == "109876543210"


def test_live_template_send_uses_meta_template_name_params_and_sender_number(monkeypatch):
    sent = {}

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"messages": [{"id": "wamid.X"}]}).encode()

    def fake_urlopen(req, timeout=None):
        sent["url"], sent["body"] = req.full_url, json.loads(req.data.decode())
        return Resp()

    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    p = messaging.MetaCloudProvider("111111", "token")
    body = messaging.TEMPLATES["callback_confirm"][2]
    params = [{"name": "Arjun", "sender": "Harika", "when": "Sat 11 AM"}[k] for k in messaging.ordered_placeholders(body)]
    r = p.send("+919000000201", "x", "callback_confirm", template_name="callback_confirm_v1", params=params,
               from_number_id="222222")
    assert r["provider_message_id"] == "wamid.X" and "/222222/messages" in sent["url"]
    t = sent["body"]["template"]
    assert t["name"] == "callback_confirm_v1"
    assert [x["text"] for x in t["components"][0]["parameters"]] == ["Arjun", "Harika", "Sat 11 AM"]


def test_live_mode_refuses_templates_without_a_meta_name():
    t = {"name": "Call-back", "status": "provider_approved", "provider_name": None, "body": "Hi {name} STOP"}
    assert any("no Meta template name" in p for p in I.template_problems(t, {"name": "A"}, live=True))
    t["provider_name"] = "callback_v1"
    assert I.template_problems(t, {"name": "A"}, live=True) == []


def test_new_built_in_templates_are_added_to_existing_databases(app, db):
    with app.test_request_context():
        db.execute("DELETE FROM message_templates WHERE key IN ('next_steps', 'call_thanks_buyer')")
        I.ensure_templates(db)
    assert db.scalar("SELECT COUNT(*) FROM message_templates WHERE key IN ('next_steps', 'call_thanks_buyer')") == 2
    assert to_iso(now_utc() + timedelta(days=1))  # sanity: helpers import


def test_finance_can_never_message_a_customer_by_any_route(app, db, login):
    import pytest
    from teamos.domain.errors import PermissionDenied
    opp = new_seller(login, db)
    cid = db.scalar("SELECT contact_id FROM opportunities WHERE id = ?", (opp,))
    with app.test_request_context():
        with pytest.raises(PermissionDenied):
            I.send(db, user(db, "fin@t.test"), contact_id=cid, opportunity_id=opp, template_key="callback_request",
                   variables={"name": "A", "sender": "B"})
        conv = I.conversation_for(db, "+919876543210", cid)
    assert login("fin@t.test").post(f"/inbox/{conv['id']}/send", {"mode": "template",
                                                                 "template_key": "callback_request"}).status_code == 403
    assert login("fin@t.test").post(f"/inbox/quick/{opp}", {"sid": "callback_request"}).status_code == 403
    assert not out_msgs(db)


def test_never_thank_someone_for_a_call_that_did_not_happen(app, db, login):
    opp = new_seller(login, db)
    with app.test_request_context():
        s = Q.suggest(db, user(db, "seller@t.test"), opp)
        assert [x["id"] for x in s] == ["enquiry_ack", "callback_request"]
        db.execute("UPDATE opportunities SET stage = 'contact_attempted' WHERE id = ?", (opp,))
        s = Q.suggest(db, user(db, "seller@t.test"), opp)
        assert [x["id"] for x in s] == ["callback_request"]
        assert not any("speaking with me" in x["preview"] for x in s)
