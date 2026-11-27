"""Alerts on phones and computers (Web Push): keys, subscribing, each person's choices, calm bursts, dead devices,
what the phone shows when woken, hot-lead alerts and call-back reminders."""
import base64
import json
from datetime import timedelta

import pytest
from conftest import user

from teamos.domain import push as PU
from teamos.domain import tasks as T
from teamos.timeutil import now_utc, to_iso

SUB = {"endpoint": "https://fcm.googleapis.com/fcm/send/abc123", "keys": {"p256dh": "BPk", "auth": "xyz"}}


@pytest.fixture
def sent(monkeypatch):
    class Calls(list):
        pass
    calls = Calls()
    status = {"code": 201}

    def fake(endpoint, auth):
        calls.append((endpoint, auth))
        return status["code"]
    monkeypatch.setattr(PU, "_sender", fake)
    calls.status = status
    return calls


def _json_post(c, url, body):
    return c.c.post(url, data=json.dumps(body), content_type="application/json", headers={"X-CSRF-Token": c.csrf})


def _unb64(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def test_keys_are_made_once_kept_encrypted_and_sign_a_valid_token(app, db):
    with app.app_context():
        pem, pub = PU.keys(db)
        assert PU.keys(db) == (pem, pub)                              # made once
    row = db.one("SELECT value, is_secret FROM integration_settings WHERE key = 'PUSH_VAPID_PRIVATE'")
    assert row["is_secret"] == 1 and "PRIVATE KEY" not in row["value"]   # encrypted at rest
    raw = _unb64(pub)
    assert len(raw) == 65 and raw[0] == 4                             # uncompressed P-256 point
    hdr = PU._vapid_header(pem, SUB["endpoint"])
    assert hdr.startswith("vapid t=") and hdr.endswith(", k=" + pub)
    token = hdr[len("vapid t="):hdr.index(",")]
    h, c, s = token.split(".")
    claims = json.loads(_unb64(c))
    assert claims["aud"] == "https://fcm.googleapis.com" and claims["sub"].startswith("mailto:")
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
    sig = _unb64(s)
    key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), raw)
    key.verify(encode_dss_signature(int.from_bytes(sig[:32], "big"), int.from_bytes(sig[32:], "big")),
               f"{h}.{c}".encode(), ec.ECDSA(hashes.SHA256()))       # raises if the signature is wrong


def test_subscribe_then_alerts_follow_each_persons_choices(app, db, login, sent):
    c = login("seller@t.test")
    assert len(_unb64(c.get("/me/push/key").get_json()["key"])) == 65
    assert _json_post(c, "/me/push/subscribe", {"subscription": SUB, "device": "Android · Chrome"}).status_code == 200
    assert _json_post(c, "/me/push/subscribe", {"subscription": {"endpoint": "http://evil"}}).status_code == 400
    me = user(db, "seller@t.test")
    assert [d["device"] for d in PU.devices(db, me["id"])] == ["Android · Chrome"]
    with app.app_context():
        T.notify(db, me["id"], "assignment", "You now own S-1 (Glow)", "/sellers/1")
    assert len(sent) == 1 and sent[0][0] == SUB["endpoint"] and sent[0][1].startswith("vapid t=")
    # a second alert within a minute does not ping again — the phone shows "2 updates waiting" instead
    with app.app_context():
        T.notify(db, me["id"], "hot_lead", "Hot lead for you: Anji (S-2)", "/sellers/2")
    assert len(sent) == 1
    shown = c.get("/me/push/latest").get_json()
    assert shown["count"] == 2 and "Hot lead" in shown["title"] and "2 updates" in shown["title"]
    assert shown["body"].startswith("Hot lead for you") and shown["url"] == "/sellers/2"
    # switch hot leads off → no push for them, even after the calm minute
    page = c.get("/me/notifications").data.decode()
    assert "Alerts on my phone" in page and "Android · Chrome" in page and 'value="hot"' in page
    c.post("/me/notifications", {"on": ["new_lead", "followup"]})
    assert PU.prefs(db, me["id"]) == {"new_lead": True, "hot": False, "followup": True, "vikas": False,
                                      "messages": False, "team": False, "reminders": False, "team_work": False}
    db.execute("UPDATE push_subscriptions SET last_push_at = NULL")
    with app.app_context():
        T.notify(db, me["id"], "hot_lead", "Hot lead: Ravi", "/sellers/3")
        T.notify(db, me["id"], "mention", "Asha mentioned you", "/sellers/3")
    assert len(sent) == 1
    with app.app_context():
        T.notify(db, me["id"], "overdue", "Overdue: call Ravi", "/tasks/1")
    assert len(sent) == 2
    # nobody else is pinged, and people without a device cost nothing
    with app.app_context():
        T.notify(db, user(db, "buyer@t.test")["id"], "assignment", "x", "/")
    assert len(sent) == 2


def test_dead_devices_are_dropped_and_test_button_reports(app, db, login, sent):
    c = login("buyer@t.test")
    r = c.post("/me/push/test", follow_redirects=True).data.decode()
    assert "No device took the test" in r
    _json_post(c, "/me/push/subscribe", {"subscription": SUB, "device": "Windows · Edge"})
    r = c.post("/me/push/test", follow_redirects=True).data.decode()
    assert "Test sent to 1 device" in r and len(sent) == 1
    sent.status["code"] = 410                                          # the browser threw the subscription away
    me = user(db, "buyer@t.test")
    with app.app_context():
        assert PU.send_now(db, me["id"], force=True) == 0
    assert PU.devices(db, me["id"]) == []
    _json_post(c, "/me/push/subscribe", {"subscription": SUB})
    _json_post(c, "/me/push/unsubscribe", {"endpoint": SUB["endpoint"]})
    assert PU.devices(db, me["id"]) == []


def test_a_subscription_moves_with_the_person_signed_in(db, login, sent):
    _json_post(login("seller@t.test"), "/me/push/subscribe", {"subscription": SUB})
    _json_post(login("buyer@t.test"), "/me/push/subscribe", {"subscription": SUB})   # same phone, other person
    rows = db.all("SELECT user_id FROM push_subscriptions")
    assert [r["user_id"] for r in rows] == [user(db, "buyer@t.test")["id"]]


def test_marking_someone_elses_lead_hot_alerts_the_owner(db, login):
    from teamos.domain import sellers as S
    admin = user(db, "admin@t.test")
    sid = S.create_lead(db, admin, {"full_name": "Anji", "phone": "9300400001", "salon_name": "Morning Kisses",
                                    "city": "Hyderabad", "locality": "Kondapur", "source": "manual_call",
                                    "consent_contact": "1", "owner_id": str(admin["id"])})["opportunity_id"]
    login("seller@t.test").post(f"/records/{sid}/basics", {"full_name": "Anji", "salon_name": "Morning Kisses",
                                                           "phone": "9300400001", "city": "Hyderabad",
                                                           "locality": "Kondapur", "lead_temperature": "hot"})
    n = db.one("SELECT * FROM notifications WHERE user_id = ? AND kind = 'hot_lead'", (admin["id"],))
    assert n and "Shaheen Seller marked" in n["title"] and "hot" in n["title"] and n["link"] == f"/sellers/{sid}"
    # the owner marking their own lead hot is not an alert to themselves
    login("admin@t.test").post(f"/records/{sid}/basics", {"full_name": "Anji", "salon_name": "Morning Kisses",
                                                          "phone": "9300400001", "lead_temperature": "warm"})
    login("admin@t.test").post(f"/records/{sid}/basics", {"full_name": "Anji", "salon_name": "Morning Kisses",
                                                          "phone": "9300400001", "lead_temperature": "hot"})
    assert db.scalar("SELECT COUNT(*) FROM notifications WHERE kind = 'hot_lead'") == 1


def test_call_back_reminder_comes_once_shortly_before(db):
    from teamos import jobs
    me = user(db, "seller@t.test")
    now = to_iso(now_utc())
    for title, kind, mins in (("Call back Anji", "follow_up", 12), ("First call: Ravi", "first_contact", 5),
                              ("Visit Glow", "visit", 90)):
        db.insert("tasks", {"title": title, "task_type": kind, "assignee_id": me["id"], "creator_id": me["id"],
                            "due_at": to_iso(now_utc() + timedelta(minutes=mins)), "created_at": now, "updated_at": now})
    jobs.sla_sweep(db, {})
    jobs.sla_sweep(db, {})
    rows = db.all("SELECT title FROM notifications WHERE user_id = ? AND kind = 'due_soon'", (me["id"],))
    assert len(rows) == 1 and rows[0]["title"].startswith("At ") and "Call back Anji" in rows[0]["title"]
    assert PU.KIND_CATEGORY["due_soon"] == "followup"


def test_service_worker_shows_alerts_and_my_day_offers_them(login):
    c = login("seller@t.test")
    sw = c.get("/sw.js").data.decode()
    assert "addEventListener('push'" in sw and "/me/push/latest" in sw and "notificationclick" in sw
    page = c.get("/").data.decode()
    assert "data-push-nudge" in page and "Phone alerts" in page
