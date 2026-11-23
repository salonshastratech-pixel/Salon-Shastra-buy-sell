"""Milestone 7 — WhatsApp inbox, consent, webhooks, batches, digests, reports.

UAT-22 (IST period boundaries + export reconciles), UAT-23 (opt-out blocks outbound), UAT-24 (duplicate webhook event
processed once), UAT-25 (ambiguous timeout → no blind resend, manual review), UAT-29 (missing placeholder blocks
dispatch), UAT-30 (service consent ≠ marketing consent), UAT-43 (unknown number → unmatched queue), UAT-44 (two
employees messaging the same lead → warning), UAT-45 (opt-out just before batch → suppressed at send time),
UAT-48 (integration reports test-only, never "live").
"""
import hashlib
import hmac
import json
import re
from datetime import datetime, timedelta

import pytest
from test_matching import buyer, consent, seller

from conftest import PW, user

from teamos import messaging
from teamos.domain import inbox as I
from teamos.timeutil import IST, to_iso


def contact_of(db, opp_id):
    return db.scalar("SELECT contact_id FROM opportunities WHERE id = ?", (opp_id,))


def conv_of(db, opp_id):
    phone = db.scalar("SELECT phone_normalized FROM contacts WHERE id = ?", (contact_of(db, opp_id),))
    return db.one("SELECT * FROM conversations WHERE phone = ?", (phone,))


def tmpl(login, opp_id, key="callback_request", email="buyer@t.test", **extra):
    c = login(email)
    c.post(f"/inbox/start/{opp_id}")
    return c


def send_tmpl(app, db, actor_email, opp_id, key="callback_request", variables=None, ack=False):
    with app.test_request_context():
        return I.send(db, user(db, actor_email), contact_id=contact_of(db, opp_id), opportunity_id=opp_id,
                      template_key=key, variables={"name": "A", "sender": "B", **(variables or {})}, acknowledge=ack)


# -- UAT-48 -------------------------------------------------------------------------------------
def test_uat48_integration_is_reported_test_only_never_live(app, db, login):
    st = messaging.whatsapp_status(app.config)
    assert not st["live"] and "Test-only" in st["label"] and len(st["missing"]) == 4
    cfg = {"WHATSAPP_PHONE_NUMBER_ID": "1", "WHATSAPP_ACCESS_TOKEN": "t", "WHATSAPP_APP_SECRET": "s",
           "WHATSAPP_VERIFY_TOKEN": "v"}
    st = messaging.whatsapp_status(cfg)
    assert not st["live"] and "NOT approved" in st["label"]  # credentials alone are not enough
    assert isinstance(messaging.provider(cfg), messaging.MockWhatsAppProvider)
    assert messaging.whatsapp_status({**cfg, "WHATSAPP_LIVE_APPROVED": "1"})["live"]
    html = login("admin@t.test").get("/admin/integrations").data.decode()
    assert "TEST-ONLY" in html and "LIVE —" not in html
    for url in ("/inbox/", "/inbox/batches"):
        assert "Test-only" in login("buyer@t.test").get(url).data.decode()


# -- UAT-29 -------------------------------------------------------------------------------------
def test_uat29_missing_placeholder_or_unapproved_template_blocks_dispatch(app, db, login):
    b = buyer(db)
    with pytest.raises(I.ValidationError) as e:
        send_tmpl(app, db, "buyer@t.test", b, "visit_confirm")  # needs {when}
    assert "{when}" in str(e.value)
    assert db.scalar("SELECT COUNT(*) FROM messages") == 0
    db.execute("UPDATE message_templates SET status = 'draft' WHERE key = 'callback_request'")
    with pytest.raises(I.ValidationError) as e:
        send_tmpl(app, db, "buyer@t.test", b)
    assert "must be approved" in str(e.value)
    r = login("admin@t.test").post("/admin/templates", {"key": "x_test", "name": "X", "category": "service",
                                                         "body": "Hello {name}", "status": "approved_local"},
                                   follow_redirects=True)
    assert b"opt out" in r.data  # every template must say how to stop messages


# -- UAT-30 -------------------------------------------------------------------------------------
def test_uat30_service_consent_does_not_allow_marketing(app, db):
    b = buyer(db)
    m = send_tmpl(app, db, "buyer@t.test", b)  # service template: contact consent is enough
    assert m["status"] == "provider_accepted"
    with pytest.raises(I.ValidationError) as e:
        send_tmpl(app, db, "buyer@t.test", b, "market_update", {"area": "Kondapur"})
    assert "no marketing consent" in str(e.value)
    consent(db, b, "marketing")
    assert send_tmpl(app, db, "buyer@t.test", b, "market_update", {"area": "Kondapur"})["status"] == "provider_accepted"


# -- UAT-23 / UAT-43 ---------------------------------------------------------------------------------
def test_uat23_uat43_stop_blocks_outbound_and_unknown_numbers_go_to_unmatched(app, db, login):
    b = buyer(db)
    with app.test_request_context():
        I.record_inbound(db, "919100011001", "STOP", "wamid.in1")
    assert I.opted_out(db, contact_of(db, b))
    with pytest.raises(I.ValidationError) as e:
        send_tmpl(app, db, "buyer@t.test", b)
    assert "opted out" in str(e.value)
    assert db.scalar("SELECT COUNT(*) FROM tasks WHERE title LIKE 'Customer opted out%'") == 1
    assert db.scalar("SELECT COUNT(*) FROM audit_events WHERE action = 'consent.opt_out'") == 1
    assert db.scalar("SELECT template_key FROM messages WHERE direction = 'out' ORDER BY id DESC") == "optout_confirm"
    # unknown number: kept in an unmatched queue, never guessed onto a record
    with app.test_request_context():
        m = I.record_inbound(db, "919999988888", "Is the salon in Kondapur still available?", "wamid.in2")
    conv = db.one("SELECT * FROM conversations WHERE id = ?", (m["conversation_id"],))
    assert conv["contact_id"] is None
    page = login("lead@t.test").get("/inbox/?q=unmatched").data.decode()
    assert "+919999988888" in page and "unknown number" in page
    login("lead@t.test").post(f"/inbox/{conv['id']}/action", {"action": "link", "opportunity_id": b})
    assert db.scalar("SELECT contact_id FROM conversations WHERE id = ?", (conv["id"],)) == contact_of(db, b)


# -- UAT-24 -------------------------------------------------------------------------------------
def _signed(app, body: dict):
    raw = json.dumps(body).encode()
    sig = "sha256=" + hmac.new(b"test-secret", raw, hashlib.sha256).hexdigest()
    return raw, {"X-Hub-Signature-256": sig, "Content-Type": "application/json"}


def test_uat24_duplicate_webhook_event_creates_one_message_and_bad_signatures_are_rejected(app, db):
    app.config["WHATSAPP_APP_SECRET"] = "test-secret"
    buyer(db)
    payload = {"entry": [{"changes": [{"value": {"messages": [{"id": "wamid.X1", "from": "919100011001",
                                                                "timestamp": "1795400000", "text": {"body": "Hi"}}]}}]}]}
    raw, headers = _signed(app, payload)
    c = app.test_client()
    assert c.post("/webhooks/whatsapp", data=raw, headers={"Content-Type": "application/json"}).status_code == 403
    assert c.post("/webhooks/whatsapp", data=raw, headers={**headers, "X-Hub-Signature-256": "sha256=bad"}).status_code == 403
    for _ in range(3):
        assert c.post("/webhooks/whatsapp", data=raw, headers=headers).status_code == 200
    assert db.scalar("SELECT COUNT(*) FROM messages WHERE provider_message_id = 'wamid.X1'") == 1
    assert db.scalar("SELECT COUNT(*) FROM webhook_events") == 1
    assert db.scalar("SELECT status FROM webhook_events") == "processed"
    # handshake
    app.config["WHATSAPP_VERIFY_TOKEN"] = "vt"
    r = c.get("/webhooks/whatsapp?hub.mode=subscribe&hub.verify_token=vt&hub.challenge=42")
    assert r.data == b"42" and c.get("/webhooks/whatsapp?hub.mode=subscribe&hub.verify_token=no").status_code == 403


def test_delivery_statuses_only_move_forward_and_failures_are_visible(app, db):
    b = buyer(db)
    m = send_tmpl(app, db, "buyer@t.test", b)
    pid = m["provider_message_id"]
    with app.test_request_context():
        I.ingest_webhook(db, {"entry": [{"changes": [{"value": {"statuses": [{"id": pid, "status": "read"}]}}]}]})
        I.ingest_webhook(db, {"entry": [{"changes": [{"value": {"statuses": [{"id": pid, "status": "delivered"}]}}]}]})
    assert db.scalar("SELECT status FROM messages WHERE id = ?", (m["id"],)) == "read"  # never goes backwards


# -- UAT-25 -------------------------------------------------------------------------------------
def test_uat25_timeout_is_uncertain_needs_review_and_is_not_resent(app, db, login, monkeypatch):
    b = buyer(db)

    def boom(self, *a, **k):
        raise messaging.ProviderTimeout("timed out after 10s")
    monkeypatch.setattr(messaging.MockWhatsAppProvider, "send", boom)
    m = send_tmpl(app, db, "buyer@t.test", b)
    assert m["status"] == "uncertain" and db.scalar("SELECT COUNT(*) FROM messages") == 1  # exactly one attempt
    monkeypatch.undo()
    lead = login("buyer@t.test")
    assert b"Messages needing a check" in lead.get("/inbox/").data
    lead.post(f"/inbox/messages/{m['id']}/resolve", {"outcome": "failed"})
    row = db.one("SELECT * FROM messages WHERE id = ?", (m["id"],))
    assert row["status"] == "failed" and row["resolved_by"] == user(db, "buyer@t.test")["id"]


# -- UAT-44 -------------------------------------------------------------------------------------
def test_uat44_second_employee_is_warned_before_messaging_the_same_lead(app, db, login):
    b = buyer(db)
    send_tmpl(app, db, "buyer@t.test", b)
    with pytest.raises(I.CollisionWarning) as w:
        send_tmpl(app, db, "seller@t.test", b, "next_steps")
    assert "Harika Buyer messaged this person" in w.value.message
    assert db.scalar("SELECT COUNT(*) FROM messages WHERE direction = 'out'") == 1  # nothing sent without confirmation
    conv = conv_of(db, b)
    r = login("seller@t.test").post(f"/inbox/{conv['id']}/send", {"mode": "template", "template_key": "next_steps"},
                                    follow_redirects=True)
    assert b"Send anyway" in r.data or b"Tick" in r.data
    send_tmpl(app, db, "seller@t.test", b, "next_steps", ack=True)
    assert db.scalar("SELECT COUNT(*) FROM messages WHERE direction = 'out'") == 2


def test_free_text_only_inside_24h_window_and_frequency_cap(app, db, login):
    b = buyer(db)
    conv_page = login("buyer@t.test")
    conv_page.post(f"/inbox/start/{b}")
    conv = conv_of(db, b)
    r = conv_page.post(f"/inbox/{conv['id']}/send", {"mode": "text", "text": "Hi"}, follow_redirects=True)
    assert b"only allowed within 24 hours" in r.data
    with app.test_request_context():
        I.record_inbound(db, "919100011001", "Hello, any update?", "wamid.w1")
    conv_page.post(f"/inbox/{conv['id']}/send", {"mode": "text", "text": "Yes — calling you at 4 pm"})
    assert db.scalar("SELECT COUNT(*) FROM messages WHERE kind = 'text' AND direction = 'out'") == 1
    send_tmpl(app, db, "buyer@t.test", b)
    send_tmpl(app, db, "buyer@t.test", b)
    with pytest.raises(I.ValidationError) as e:
        send_tmpl(app, db, "buyer@t.test", b)
    assert "limit 3" in str(e.value)


# -- UAT-45 -------------------------------------------------------------------------------------
def test_uat45_batch_rechecks_consent_at_send_time(app, db, login):
    b1 = buyer(db, name="Alpha Buyer")
    b2 = buyer(db, phone="9100011002", name="Beta Buyer")
    member, lead = login("buyer@t.test"), login("lead@t.test")
    r = member.post("/inbox/batches", {"name": "Callbacks", "template_key": "callback_request", "audience": "buyers_active"})
    bid = int(r.headers["Location"].rstrip("/").split("/")[-1])
    assert member.post(f"/inbox/batches/{bid}", {"action": "approve"}).status_code == 403  # creator can't approve
    lead.post(f"/inbox/batches/{bid}", {"action": "approve"})
    assert db.scalar("SELECT status FROM batches") == "approved"
    with app.test_request_context():
        I.record_inbound(db, "919100011002", "stop", "wamid.stop2")  # Beta opts out after approval
    lead.post(f"/inbox/batches/{bid}", {"action": "send"})
    rec = {r["contact_id"]: r for r in db.all("SELECT * FROM batch_recipients WHERE batch_id = ?", (bid,))}
    assert rec[contact_of(db, b1)]["status"] == "sent" and rec[contact_of(db, b2)]["status"] == "suppressed"
    counts = json.loads(db.scalar("SELECT counts FROM batches"))
    assert counts["sent"] == 1 and counts["suppressed"] == 1
    assert db.scalar("SELECT COUNT(*) FROM messages WHERE batch_id = ?", (bid,)) == 1


# -- UAT-22 -------------------------------------------------------------------------------------
def test_uat22_report_periods_use_ist_boundaries_and_csv_reconciles(app, db, login):
    from teamos.domain import reports as R
    a, b = buyer(db, name="Late Buyer"), buyer(db, phone="9100011003", name="Early Buyer")
    today = datetime.now(IST).replace(hour=0, minute=0, second=0, microsecond=0)
    db.execute("UPDATE opportunities SET created_at = ? WHERE id = ?", (to_iso(today - timedelta(minutes=10)), a))  # 23:50 yesterday IST
    db.execute("UPDATE opportunities SET created_at = ? WHERE id = ?", (to_iso(today + timedelta(minutes=10)), b))  # 00:10 today IST
    admin = user(db, "admin@t.test")
    rt = R.build(db, admin, R.period_bounds("today"))
    ry = R.build(db, admin, R.period_bounds("yesterday"))
    assert rt["lead_total"] == 1 and ry["lead_total"] == 1
    p = R.period_bounds("custom", (today - timedelta(days=1)).date().isoformat(), today.date().isoformat())
    assert R.build(db, admin, p)["lead_total"] == 2
    browser = login("admin@t.test")
    html = browser.get("/reports/?period=today").data.decode()
    csv_text = browser.get("/reports/?period=today&format=csv").data.decode()
    assert "new opportunities,1" in csv_text
    assert re.search(r"New buyer \+ seller opportunities</td><td>1<", html)
    assert "finance" in csv_text  # admin sees company fees section
    member_csv = login("buyer@t.test").get("/reports/?period=today&format=csv").data.decode()
    assert "finance" not in member_csv  # team members never get company fee numbers
    assert "+91" not in csv_text  # aggregates only — no phone numbers


# -- Digest ---------------------------------------------------------------------------------------
def test_digest_is_personal_once_a_day_and_held_without_mail_server(app, db, monkeypatch):
    from teamos.domain import digests
    buyer(db)
    with app.test_request_context():
        out1 = digests.send_all(db)
        out2 = digests.send_all(db)
    n_users = db.scalar("SELECT COUNT(*) FROM users WHERE is_active = 1 AND digest_opt_in = 1")
    assert out1.startswith(f"{n_users} digest") and out2.startswith("0 digest")
    rows = db.all("SELECT * FROM email_outbox")
    assert all(r["status"] == "held" and r["provider"] == "outbox" for r in rows)
    member = next(r for r in rows if r["to_addr"] == "buyer@t.test")
    assert "First call" in member["body"] and "Fees outstanding" not in member["body"]
    assert "+91" not in member["body"]
    fin = next(r for r in rows if r["to_addr"] == "fin@t.test")
    assert "Fees outstanding" in fin["body"]


def test_m7_pages_render_with_real_csrf_tokens_and_finance_has_no_inbox(app, db, login):
    b = buyer(db)
    with app.test_request_context():
        I.record_inbound(db, "919100011001", "Hello", "wamid.p1")
    conv = conv_of(db, b)
    br = app.test_client()
    br.post("/login", data={"email": "admin@t.test", "password": PW})
    for url in ("/inbox/", f"/inbox/{conv['id']}", "/inbox/batches", "/reports/", "/reports/?period=week",
                "/admin/integrations", "/admin/templates", "/admin/outbox", "/me/digest/preview", "/", f"/buyers/{b}"):
        html = br.get(url).data.decode()
        assert "Traceback" not in html, url
        for f in re.findall(r'<form[^>]*method="post"[^>]*>(.*?)</form>', html, re.S):
            m = re.search(r'name="csrf_token" value="([^"]*)"', f)
            assert m and m.group(1), url
    fin = login("fin@t.test")
    for url in ("/inbox/", f"/inbox/{conv['id']}", "/reports/", "/admin/integrations"):
        assert fin.get(url).status_code == 403, url


def test_unknown_number_can_get_a_reply_and_becomes_a_lead_with_the_chat_linked(app, db, login):
    """A new enquirer writes from an unknown number: the team replies inside the 24-hour window, then creates the lead
    from the chat (mobile prefilled) and the conversation links itself — no copy-paste, nothing lost."""
    from conftest import SELLER_FORM
    with app.test_request_context():
        m = I.record_inbound(db, "919812345678", "Hello, I want to sell my salon in Ameerpet", "wamid.u1")
    cid = m["conversation_id"]
    c = login("seller@t.test")
    page = c.get(f"/inbox/{cid}").data.decode()
    assert "Send reply" in page and "New seller lead" in page and "from_conv=" in page
    c.post(f"/inbox/{cid}/send", {"mode": "text", "text": "Thank you! May I know your name?"})
    out = db.one("SELECT * FROM messages WHERE conversation_id = ? AND direction = 'out'", (cid,))
    assert out and out["status"] == "provider_accepted" and out["sent_by"] == user(db, "seller@t.test")["id"]
    assert db.scalar("SELECT assigned_to FROM conversations WHERE id = ?", (cid,)) == out["sent_by"]  # replier owns it
    # templates still need a record with consent
    r = c.post(f"/inbox/{cid}/send", {"mode": "template", "template_key": "callback_request"}, follow_redirects=True)
    assert b"Link this number" in r.data
    # STOP from an unknown number blocks further replies
    with app.test_request_context():
        I.record_inbound(db, "919812345678", "stop", "wamid.u2")
    r = c.post(f"/inbox/{cid}/send", {"mode": "text", "text": "ok"}, follow_redirects=True)
    assert b"asked to stop" in r.data
    # create the lead straight from the chat: mobile prefilled, chat linked afterwards
    form = c.get(f"/sellers/new?phone=9812345678&source=whatsapp&from_conv={cid}").data.decode()
    assert 'value="9812345678"' in form and f'name="from_conv" value="{cid}"' in form
    c.post("/sellers/new", {**SELLER_FORM, "phone": "9812345678", "source": "whatsapp", "from_conv": str(cid)})
    opp = db.scalar("SELECT o.id FROM opportunities o JOIN contacts k ON k.id = o.contact_id "
                    "WHERE k.phone_normalized = '+919812345678'")
    assert opp and db.scalar("SELECT COUNT(*) FROM conversation_links WHERE conversation_id = ? AND opportunity_id = ?",
                             (cid, opp)) == 1
    assert db.scalar("SELECT contact_id FROM conversations WHERE id = ?", (cid,)) is not None


def test_reply_to_unknown_number_needs_the_window_open(app, db, login):
    old = to_iso(datetime.now(IST) - timedelta(hours=30))
    with app.test_request_context():
        m = I.record_inbound(db, "919812300000", "hi", "wamid.u3", at=old)
    r = login("seller@t.test").post(f"/inbox/{m['conversation_id']}/send", {"mode": "text", "text": "hello"},
                                    follow_redirects=True)
    assert b"more than 24 hours ago" in r.data
    assert not db.scalar("SELECT COUNT(*) FROM messages WHERE direction = 'out'")


def test_batches_never_carry_personal_links_or_system_templates(app, db, login):
    with app.test_request_context():
        I.ensure_templates(db)
        for key in ("deep_form_link", "teaser_share", "targeted_request", "optout_confirm"):
            with pytest.raises(I.ValidationError) as e:
                I.create_batch(db, user(db, "buyer@t.test"), "x", key, "buyers_active", {"link": "https://x", "expires": "1"})
            assert "can't go in a batch" in str(e.value)
    page = login("buyer@t.test").get("/inbox/batches").data.decode()
    assert "Polite follow-up" in page and "Detailed form link" not in page and "Opt-out confirmation" not in page
    assert not db.scalar("SELECT COUNT(*) FROM batches")


def test_page_titles_are_plain_text_and_digest_toggle_is_visible(login):
    c = login("buyer@t.test")
    for url in ("/", "/inbox/", "/inbox/batches", "/tasks/"):
        html = c.get(url).data.decode()
        title = re.search(r"<title>(.*?)</title>", html, re.S).group(1)
        assert "<" not in title, (url, title)
    assert "Morning e-mail summary" in c.get("/").data.decode()
