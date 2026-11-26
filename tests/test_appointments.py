"""Appointments with Vikas: standard lengths (call 10 min, office meeting 3 hours, salon / place visit 5 hours),
no double-booking of his time, and the same appointment on every calendar that needs it (in-app week view, the
private calendar feed, and one-tap "Add to Google Calendar")."""
from datetime import timedelta

from conftest import user
from test_pilot import _token

from teamos.domain import appointments as AP
from teamos.domain import calendar as CAL
from teamos.domain import pilot as P
from teamos.domain import sellers as S
from teamos.domain.errors import ValidationError
from teamos.timeutil import IST, now_utc, parse_iso


def _seller(db, phone="9000011001"):
    admin = user(db, "admin@t.test")
    return S.create_lead(db, admin, {"full_name": f"Owner {phone}", "phone": phone, "salon_name": f"Salon {phone}",
                                     "city": "Hyderabad", "locality": "Kondapur", "source": "meta_lead",
                                     "consent_contact": "1", "owner_id": str(user(db, "seller@t.test")["id"])}
                         )["opportunity_id"]


def _day(n=3):
    return (now_utc().astimezone(IST) + timedelta(days=n)).strftime("%Y-%m-%d")


def test_standard_lengths():
    assert (AP.minutes_for("phone"), AP.minutes_for("video"), AP.minutes_for("office"),
            AP.minutes_for("salon_visit"), AP.minutes_for("visit")) == (10, 10, 180, 300, 300)
    assert AP.length_label(10) == "10 min" and AP.length_label(180) == "3 hours" and AP.length_label(300) == "5 hours"
    url = AP.gcal_url("Phone call with Vikas · SL-1", "2030-01-15T05:30:00Z", 10)
    assert "calendar.google.com" in url and "20300115T053000Z%2F20300115T054000Z" in url


def test_slot_length_is_set_by_type_and_overlaps_are_skipped(db, login):
    admin = login("admin@t.test")
    day = _day()
    admin.post("/pilot/vikas", {"day": day, "times": "10:00", "mode": "office"})          # 10:00–13:00
    admin.post("/pilot/vikas", {"day": day, "times": "11:00 13:00 13:10", "mode": "phone"})   # 11:00 clashes
    rows = db.all("SELECT mode, minutes, starts_at FROM vikas_slots ORDER BY starts_at")
    got = [(r["mode"], r["minutes"], parse_iso(r["starts_at"]).astimezone(IST).strftime("%H:%M")) for r in rows]
    assert got == [("office", 180, "10:00"), ("phone", 10, "13:00"), ("phone", 10, "13:10")]
    page = admin.get("/pilot/vikas").data.decode()
    assert "3 hours" in page and "10 min" in page and "Minutes each" not in page
    r = admin.post("/pilot/vikas", {"day": day, "times": "12:00", "mode": "phone"})
    assert db.scalar("SELECT COUNT(*) FROM vikas_slots") == 3                       # inside the office meeting


def test_consultation_blocks_vikas_time_and_shows_on_every_calendar(db, login, app):
    seller_user = user(db, "seller@t.test")
    opp = _seller(db)
    other = _seller(db, phone="9000011099")
    c = login("seller@t.test")
    admin = login("admin@t.test")
    day = _day()
    admin.post("/pilot/vikas", {"day": day, "times": "16:00 19:30", "mode": "phone"})
    # a salon visit 14:00–19:00: the 16:00 free time is withdrawn, 19:30 stays
    c.post(f"/sellers/{opp}/pilot/consultation", {"kind": "salon_visit", "in_hyderabad": "1",
                                                  "scheduled_at": f"{day}T14:00"})
    k = db.one("SELECT * FROM consultations WHERE opportunity_id = ?", (opp,))
    assert k and k["status"] == "booked"
    left = [parse_iso(s["starts_at"]).astimezone(IST).strftime("%H:%M") for s in P.open_slots(db)]
    assert left == ["19:30"]
    # Vikas cannot be double-booked: an office meeting at 18:00 the same day is refused
    c.post(f"/sellers/{other}/pilot/consultation", {"kind": "office", "in_hyderabad": "1", "scheduled_at": f"{day}T18:00"})
    assert db.scalar("SELECT COUNT(*) FROM consultations WHERE opportunity_id = ?", (other,)) == 0
    try:
        P.book_consultation(db, seller_user, S.load(db, other), {"kind": "office", "scheduled_at": f"{day}T18:00"})
        raise AssertionError("clash not caught")
    except ValidationError as e:
        assert "Vikas is busy then" in str(e.errors)
    # the owner books the 19:30 call through their link
    token = _token(c, opp, "form")
    pub = app.test_client()
    assert "(10 min)" in pub.get(f"/s/{token}/book").data.decode()
    pub.post(f"/s/{token}/book", data={"slot_id": str(P.open_slots(db)[0]["id"])})
    assert "Add to my Google Calendar" in pub.get(f"/s/{token}/book").data.decode()
    # record owner's calendar: both appointments, real lengths, add-to-Google links
    start = parse_iso(k["scheduled_at"]).astimezone(IST).date()
    evs = [e for e in CAL.events(db, seller_user["id"], start, 1) if e["kind"] in ("vikas_call", "consultation")]
    assert [(e["kind"], e["minutes"]) for e in evs] == [("consultation", 300), ("vikas_call", 10)]
    assert all(e["gcal"].startswith("https://calendar.google.com/") for e in evs)
    # Vikas (the consultant — admin here) sees them too, and the private feed blocks the real time
    admin_user = user(db, "admin@t.test")
    assert len([e for e in CAL.events(db, admin_user["id"], start, 1) if e["kind"] in ("vikas_call", "consultation")]) == 2
    ics = CAL.ics(db, seller_user, "https://x.test")
    begin = parse_iso(k["scheduled_at"])
    assert ("DTEND:" + (begin + timedelta(minutes=300)).strftime("%Y%m%dT%H%M%SZ")) in ics
    # people not on the record and not Vikas do not get them
    assert not [e for e in CAL.events(db, user(db, "buyer@t.test")["id"], start, 1) if e["kind"] == "consultation"]
    # pages
    week = c.get(f"/calendar?week={start.isoformat()}").data.decode()
    assert "5 hours" in week and "10 min" in week and "Add to Google Calendar" in week
    rec = c.get(f"/sellers/{opp}?tab=pilot").data.decode()
    assert "for 5 hours" in rec and "Add to Google Calendar" in rec and "Phone call with Vikas, 10 min" in rec
    vik = admin.get("/pilot/vikas").data.decode()
    assert "Vikas&#39;s appointments" in vik or "Vikas's appointments" in vik
    assert vik.count("Add to Google Calendar") >= 2


def test_confirming_a_requested_call_checks_vikas_is_free(db, login):
    opp, other = _seller(db), _seller(db, phone="9000011098")
    c = login("seller@t.test")
    day = _day(4)
    c.post(f"/sellers/{opp}/pilot/consultation", {"kind": "office", "in_hyderabad": "1", "scheduled_at": f"{day}T10:00"})
    bid = P.book(db, S.load(db, other), None, "that morning", "link")
    c.post(f"/sellers/{other}/pilot/booking/{bid}", {"status": "booked", "starts_at": f"{day}T11:00"})
    assert db.scalar("SELECT status FROM vikas_bookings WHERE id = ?", (bid,)) == "requested"   # inside the meeting
    c.post(f"/sellers/{other}/pilot/booking/{bid}", {"status": "booked", "starts_at": f"{day}T13:00"})
    assert db.scalar("SELECT status FROM vikas_bookings WHERE id = ?", (bid,)) == "booked"
