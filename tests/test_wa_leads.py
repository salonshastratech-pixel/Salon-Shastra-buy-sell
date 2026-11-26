"""Meta "Click to WhatsApp" ad chats become leads directly (no third-party inbox): once per number, shared in turn,
first call in 10 minutes; existing people are not duplicated; ordinary new chats only when switched on."""
from conftest import user

from teamos.domain import inbox as I
from teamos.timeutil import now_utc, parse_iso


def _payload(mid, frm, text, name="Ravi Kiran", referral=None):
    msg = {"from": frm, "id": mid, "timestamp": str(int(now_utc().timestamp())), "type": "text", "text": {"body": text}}
    if referral:
        msg["referral"] = referral
    return {"entry": [{"changes": [{"value": {"metadata": {"phone_number_id": "111"},
                                              "contacts": [{"wa_id": frm, "profile": {"name": name}}],
                                              "messages": [msg]}}]}]}


AD = {"source_type": "ad", "headline": "Own a running salon in Hyderabad", "body": "Salons for sale from ₹10L",
      "source_url": "https://fb.me/ad", "ctwa_clid": "abc"}


def test_ad_chat_becomes_a_lead_once_and_is_shared_in_turn(app, db, login):
    with app.app_context():
        I.ingest_webhook(db, _payload("wamid.1", "919300500001", "Hi, I want details", referral=AD))
        I.ingest_webhook(db, _payload("wamid.2", "919300500001", "What is the price?", referral=AD))   # same person
    opps = db.all("SELECT o.*, c.full_name FROM opportunities o JOIN contacts c ON c.id = o.contact_id")
    assert len(opps) == 1
    o = opps[0]
    assert o["kind"] == "buyer" and o["source"] == "whatsapp" and "Own a running salon" in o["source_detail"]
    assert o["full_name"] == "Ravi Kiran" and o["primary_owner_id"] and o["lead_created_at"]
    t = db.one("SELECT * FROM tasks WHERE entity_id = ? AND status = 'open'", (o["id"],))
    assert (parse_iso(t["due_at"]) - now_utc()).total_seconds() <= 11 * 60
    conv = db.one("SELECT * FROM conversations")
    assert conv["contact_id"] == o["contact_id"]                               # the chat sits on the lead
    assert db.scalar("SELECT COUNT(*) FROM messages") == 2


def test_seller_ads_existing_people_and_ordinary_chats(app, db, login):
    from test_records import seller
    s = seller(db, phone="9300500009", salon="Known Salon")
    with app.app_context():
        I.ingest_webhook(db, _payload("wamid.3", "919300500002", "I want to sell my salon in Kukatpally",
                                      referral={"source_type": "ad", "headline": "Sell your salon"}))
        I.ingest_webhook(db, _payload("wamid.4", "919300500009", "hello again", referral=AD))     # already in TeamOS
        I.ingest_webhook(db, _payload("wamid.5", "919300500003", "hi"))                            # not from an ad
        I.ingest_webhook(db, _payload("wamid.6", "919300500004", "looking for a job as stylist", referral=AD))
    kinds = {r["full_name"]: r["kind"] for r in db.all("SELECT o.kind, c.full_name, c.phone_normalized FROM "
                                                       "opportunities o JOIN contacts c ON c.id = o.contact_id")}
    assert db.scalar("SELECT kind FROM opportunities o JOIN contacts c ON c.id = o.contact_id WHERE "
                     "c.phone_normalized = '+919300500002'") == "seller"
    assert db.scalar("SELECT COUNT(*) FROM opportunities") == 2                   # known person not duplicated
    assert "Meta ad" in db.scalar("SELECT body FROM activities WHERE entity_id = ? AND kind = 'system' ORDER BY id "
                                  "DESC LIMIT 1", (s,))
    assert kinds  # names present
    # switch on: ordinary new chats become leads too
    login("admin@t.test").post("/records/wa-chats", {"on": "1"})
    with app.app_context():
        I.ingest_webhook(db, _payload("wamid.7", "919300500005", "hi, interested"))
    assert db.scalar("SELECT COUNT(*) FROM opportunities") == 3
    assert "WhatsApp chats" in login("admin@t.test").get("/records/manage").data.decode()
