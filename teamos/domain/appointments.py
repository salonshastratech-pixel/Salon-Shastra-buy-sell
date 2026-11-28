"""Appointments: one place for how long each kind of appointment takes, clash checks for Vikas's time, and
"Add to Google Calendar" links.

  Phone / video call with Vikas  →  10 minutes
  Meeting at our office          →  3 hours
  Salon visit / place visit      →  5 hours
"""
from __future__ import annotations

from datetime import timedelta
from urllib.parse import urlencode

from ..timeutil import parse_iso

MINUTES = {"phone": 10, "video": 10, "office": 180, "salon_visit": 300, "visit": 300}
LABELS = {"phone": "Phone call with Vikas", "video": "Video call with Vikas", "office": "Meeting at our office",
          "salon_visit": "Salon visit", "visit": "Salon / place visit"}
TASK_MINUTES = {"first_contact": 10, "call": 10, "follow_up": 10, "visit": 60}   # visit / meeting / appointment


def minutes_for(kind: str | None) -> int:
    return MINUTES.get(kind or "", 30)


def length_label(minutes: int) -> str:
    if minutes >= 60:
        h = minutes / 60
        return f"{int(h)} hour{'s' if h != 1 else ''}" if h == int(h) else f"{h:.1f} hours"
    return f"{minutes} min"


def end_of(start_iso: str, minutes: int) -> str:
    from ..timeutil import to_iso
    return to_iso(parse_iso(start_iso) + timedelta(minutes=minutes))


def vikas_busy(db, start_iso: str, minutes: int, ignore: tuple[str, int] | None = None) -> list[dict]:
    """Vikas's appointments that overlap [start, start + minutes): booked calls and booked consultations."""
    start = parse_iso(start_iso)
    end = start + timedelta(minutes=minutes)
    from ..timeutil import to_iso
    window_a, window_b = to_iso(start - timedelta(hours=6)), to_iso(end)   # nothing lasts longer than 5 hours
    out = []
    for b in db.all("SELECT vb.id, vb.starts_at, COALESCE(s.mode, 'phone') AS mode, o.ref_code FROM vikas_bookings vb "
                    "LEFT JOIN vikas_slots s ON s.id = vb.slot_id JOIN opportunities o ON o.id = vb.opportunity_id "
                    "WHERE vb.status = 'booked' AND vb.starts_at IS NOT NULL AND vb.starts_at >= ? AND vb.starts_at < ?",
                    (window_a, window_b)):
        if ignore == ("call", b["id"]):
            continue
        out.append({"what": LABELS.get(b["mode"], "Call with Vikas"), "at": b["starts_at"],
                    "minutes": minutes_for(b["mode"]), "ref": b["ref_code"]})
    for c in db.all("SELECT k.id, k.kind, k.scheduled_at, o.ref_code FROM consultations k JOIN opportunities o ON "
                    "o.id = k.opportunity_id WHERE k.status = 'booked' AND k.scheduled_at IS NOT NULL AND "
                    "k.scheduled_at >= ? AND k.scheduled_at < ?", (window_a, window_b)):
        if ignore == ("consultation", c["id"]):
            continue
        out.append({"what": LABELS.get(c["kind"], "Consultation"), "at": c["scheduled_at"],
                    "minutes": minutes_for(c["kind"]), "ref": c["ref_code"]})
    return [x for x in out if parse_iso(x["at"]) < end and start < parse_iso(x["at"]) + timedelta(minutes=x["minutes"])]


def clash_message(busy: list[dict]) -> str:
    from ..timeutil import fmt_ist
    x = busy[0]
    return (f"Vikas is busy then: {x['what']} ({x['ref']}) at {fmt_ist(x['at'], '%a %d %b, %I:%M %p')} for "
            f"{length_label(x['minutes'])}. Pick another time.")


def gcal_url(title: str, start_iso: str | None, minutes: int, details: str = "", location: str = "") -> str | None:
    """A Google Calendar "add event" link, pre-filled. Nothing is sent anywhere until the person saves it."""
    if not start_iso:
        return None
    a = parse_iso(start_iso)
    b = a + timedelta(minutes=minutes)
    q = {"action": "TEMPLATE", "text": title, "dates": a.strftime("%Y%m%dT%H%M%SZ") + "/" + b.strftime("%Y%m%dT%H%M%SZ"),
         "details": details, "ctz": "Asia/Kolkata"}
    if location:
        q["location"] = location
    return "https://calendar.google.com/calendar/render?" + urlencode(q)


def upcoming(db, days: int = 30) -> list[dict]:
    """Every booked appointment with Vikas in the next `days` days: calls and paid consultations, with lengths."""
    from ..timeutil import now_iso, now_utc, to_iso
    a, b = now_iso(), to_iso(now_utc() + timedelta(days=days))
    out = []
    for r in db.all("SELECT vb.id, vb.starts_at AS at, COALESCE(s.mode, 'phone') AS kind, o.id AS opp_id, o.kind AS opp_kind, "
                    "o.ref_code, COALESCE(bs.locality, c.locality) AS area FROM vikas_bookings vb LEFT JOIN vikas_slots s "
                    "ON s.id = vb.slot_id JOIN opportunities o ON o.id = vb.opportunity_id LEFT JOIN salon_businesses bs "
                    "ON bs.id = o.business_id LEFT JOIN contacts c ON c.id = o.contact_id WHERE vb.status = 'booked' "
                    "AND vb.starts_at >= ? AND vb.starts_at < ?", (a, b)):
        tab = "investor" if r["opp_kind"] == "buyer" else "pilot"
        out.append({**r, "link": f"/{'buyers' if r['opp_kind'] == 'buyer' else 'sellers'}/{r['opp_id']}?tab={tab}#vikas"})
    for r in db.all("SELECT k.id, k.scheduled_at AS at, k.kind, o.id AS opp_id, o.kind AS opp_kind, o.ref_code, "
                    "COALESCE(bs.locality, c.locality) AS area FROM consultations k JOIN opportunities o ON "
                    "o.id = k.opportunity_id LEFT JOIN salon_businesses bs ON bs.id = o.business_id LEFT JOIN contacts c "
                    "ON c.id = o.contact_id WHERE k.status = 'booked' AND k.scheduled_at >= ? AND k.scheduled_at < ?",
                    (a, b)):
        out.append({**r, "link": f"/sellers/{r['opp_id']}?tab=pilot#consult"})
    for r in out:
        m = minutes_for(r["kind"])
        r.update(type_label=LABELS.get(r["kind"], "Appointment"), minutes=m, length=length_label(m),
                 end_at=end_of(r["at"], m),
                 gcal=gcal_url(f"{LABELS.get(r['kind'], 'Appointment')} · {r['ref_code']}", r["at"], m,
                               f"Salon Shastra — {r['ref_code']}", r["area"] or ""))
    out.sort(key=lambda r: r["at"])
    return out
