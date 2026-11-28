"""@tags: anywhere the team writes, a tagged colleague gets an alert (pushed to their phone when alerts are on) saying
who tagged them, where and what was written; the writer is told; the note shows the tag highlighted; the page
offers the team for the @ picker."""
from conftest import user

from teamos.domain import inquiries as INQ
from teamos.domain import push as PU
from teamos.domain import sellers as S


def _seller(db, phone="9300330001"):
    admin = user(db, "admin@t.test")
    return S.create_lead(db, admin, {"full_name": "Lakshmi", "phone": phone, "salon_name": "Glow", "city": "Hyderabad",
                                     "locality": "Kondapur", "source": "manual_call", "consent_contact": "1",
                                     "owner_id": str(user(db, "seller@t.test")["id"])})["opportunity_id"]


def _alerts(db, email):
    return db.all("SELECT * FROM notifications WHERE user_id = ? AND kind = 'mention' ORDER BY id",
                  (user(db, email)["id"],))


def test_tag_in_a_call_note_reaches_the_colleague_and_the_writer_is_told(db, login):
    oid = _seller(db)
    c = login("seller@t.test")
    r = c.post(f"/sellers/{oid}/call", {"outcome": "connected", "note": "Owner may invest too @harika please call him",
                                        "next_title": "Call back", "next_due": "2030-01-05T11:00"}, follow_redirects=True)
    a = _alerts(db, "buyer@t.test")
    assert len(a) == 1 and a[0]["link"] == f"/sellers/{oid}"
    assert a[0]["title"].startswith("Shaheen Seller tagged you on SL-") and "(Glow)" in a[0]["title"]
    assert "please call him" in a[0]["title"]
    assert "Tagged Harika Buyer" in r.data.decode()
    page = c.get(f"/sellers/{oid}").data.decode()
    assert '<span class="tag">@harika</span>' in page
    assert PU.KIND_CATEGORY["mention"] == "team"                      # phone alert: "Mentions, approvals and forms"


def test_tags_work_in_every_place_people_write(db, login):
    oid = _seller(db, "9300330002")
    c = login("seller@t.test")
    admin_id, harika = user(db, "admin@t.test")["id"], user(db, "buyer@t.test")
    c.post(f"/sellers/{oid}/note", {"body": "@HarikaBuyer and @asha see the lease"})          # full name, no space
    tid = db.scalar("SELECT id FROM tasks WHERE entity_id = ? AND status = 'open'", (oid,))
    c.post(f"/tasks/{tid}/complete", {"outcome": "Spoke - @Ravi check the price", "next_title": "Visit",
                                      "next_due": "2030-01-06T11:00"})
    c.post("/tasks/new", {"title": "Collect lease copy, @Harika knows the landlord", "assignee_id": str(admin_id),
                          "due_at": "2030-01-05T11:00", "next": "/"})
    c.post(f"/records/{oid}/pass", {"to_id": str(admin_id), "note": "@harika helped on this one", "next": "/"})
    iid = INQ.create(db, user(db, "admin@t.test"), "job", {"full_name": "Sana", "phone": "9300330009",
                                                            "source": "incoming_call"})
    c.post(f"/others/{iid}", {"note": "@Ravi she can start Monday", "status": "contacted", "was_call": "1"})
    harika_alerts = _alerts(db, "buyer@t.test")
    assert len(harika_alerts) == 3                           # note, task given (besides the assignee), pass note
    assert len(_alerts(db, "admin@t.test")) == 1             # @asha in the note
    ravi = _alerts(db, "lead@t.test")
    assert len(ravi) == 2 and any(r["link"] == f"/others/{iid}" for r in ravi)
    assert not _alerts(db, "seller@t.test")                  # the writer is never alerted about their own tag


def test_no_tag_no_alert_and_finance_cannot_be_tagged(db, login):
    oid = _seller(db, "9300330003")
    c = login("seller@t.test")
    c.post(f"/sellers/{oid}/note", {"body": "email me at ravi@salon.com and tell @Fiona"})
    assert db.scalar("SELECT COUNT(*) FROM notifications WHERE kind = 'mention'") == 0


def test_page_offers_the_team_for_the_at_picker(db, login):
    page = login("seller@t.test").get("/").data.decode()
    assert 'data-team="[' in page and "Harika" in page and "Fiona" not in page.split('data-team="', 1)[1].split('"', 1)[0]
