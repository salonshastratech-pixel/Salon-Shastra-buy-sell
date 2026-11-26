"""Milestone 9 — calendar: each person's calls, call-backs, follow-ups and salon visits by day.

In the app the week view shows full details. The private subscribe link (for Google Calendar or a phone) shows only
the kind of task, the reference code and the area — never customer names or numbers — because calendar apps keep
copies outside TeamOS. The link's secret is stored only as a hash and can be revoked at any time.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta

from .. import audit
from ..timeutil import IST, now_iso, now_utc, parse_iso, to_iso
from . import appointments as AP
from .tasks import TASK_TYPES

TYPE_LABELS = dict(TASK_TYPES)


def week_start(day: str | None = None):
    """Monday (IST) of the week containing `day` (YYYY-MM-DD) or today."""
    try:
        d = datetime.strptime(day, "%Y-%m-%d").date() if day else now_utc().astimezone(IST).date()
    except ValueError:
        d = now_utc().astimezone(IST).date()
    return d - timedelta(days=d.weekday())


def _bounds(start_date, days: int) -> tuple[str, str]:
    start = datetime(start_date.year, start_date.month, start_date.day, tzinfo=IST)
    return to_iso(start), to_iso(start + timedelta(days=days))


def events(db, user_id: int, start_date, days: int = 7) -> list[dict]:
    """Open tasks due in the window + scheduled salon visits on this person's deals."""
    a, b = _bounds(start_date, days)
    out = []
    for t in db.all(
            "SELECT t.id, t.title, t.task_type, t.due_at, t.priority, o.ref_code, o.kind AS opp_kind, o.id AS opp_id, "
            "COALESCE(bs.locality, c.locality) AS area FROM tasks t "
            "LEFT JOIN opportunities o ON t.entity_type = 'opportunity' AND o.id = t.entity_id "
            "LEFT JOIN salon_businesses bs ON bs.id = o.business_id LEFT JOIN contacts c ON c.id = o.contact_id "
            "WHERE t.status = 'open' AND t.assignee_id = ? AND t.due_at >= ? AND t.due_at < ? ORDER BY t.due_at",
            (user_id, a, b)):
        out.append({"kind": "task", "id": t["id"], "at": t["due_at"], "title": t["title"], "type": t["task_type"],
                    "minutes": AP.TASK_MINUTES.get(t["task_type"], 15),
                    "type_label": TYPE_LABELS.get(t["task_type"], "Task"), "ref": t["ref_code"], "area": t["area"],
                    "priority": t["priority"], "link": f"/tasks/{t['id']}"})
    for v in db.all(
            "SELECT v.id, v.scheduled_at, v.deal_id, d.ref_code, b.locality AS area FROM deal_visits v "
            "JOIN deals d ON d.id = v.deal_id JOIN opportunities so ON so.id = d.seller_opp_id "
            "LEFT JOIN salon_businesses b ON b.id = so.business_id "
            "WHERE v.status = 'scheduled' AND (d.owner_id = ? OR v.created_by = ?) AND v.scheduled_at >= ? "
            "AND v.scheduled_at < ? ORDER BY v.scheduled_at", (user_id, user_id, a, b)):
        out.append({"kind": "visit", "id": v["id"], "at": v["scheduled_at"], "title": "Salon visit", "type": "visit",
                    "minutes": AP.minutes_for("visit"),
                    "type_label": "Salon visit", "ref": v["ref_code"], "area": v["area"], "priority": "high",
                    "link": f"/deals/{v['deal_id']}"})
    # appointments with Vikas: on Vikas's own calendar, and on the calendar of whoever owns the record
    from .pilot import consultant
    c = consultant(db)
    is_vikas = 1 if c and c["id"] == user_id else 0
    for bk in db.all(
            "SELECT vb.id, vb.starts_at, COALESCE(s.mode, 'phone') AS mode, o.ref_code, o.kind AS opp_kind, o.id AS opp_id, "
            "COALESCE(bs.locality, c.locality) AS area FROM vikas_bookings vb LEFT JOIN vikas_slots s ON s.id = vb.slot_id "
            "JOIN opportunities o ON o.id = vb.opportunity_id LEFT JOIN salon_businesses bs ON bs.id = o.business_id "
            "LEFT JOIN contacts c ON c.id = o.contact_id WHERE vb.status = 'booked' AND vb.starts_at >= ? AND "
            "vb.starts_at < ? AND (o.primary_owner_id = ? OR ? = 1)", (a, b, user_id, is_vikas)):
        tab = "investor" if bk["opp_kind"] == "buyer" else "pilot"
        out.append({"kind": "vikas_call", "id": bk["id"], "at": bk["starts_at"], "title": AP.LABELS[bk["mode"]],
                    "type": "call", "type_label": AP.LABELS[bk["mode"]], "minutes": AP.minutes_for(bk["mode"]),
                    "ref": bk["ref_code"], "area": bk["area"], "priority": "high",
                    "link": f"/{'buyers' if bk['opp_kind'] == 'buyer' else 'sellers'}/{bk['opp_id']}?tab={tab}#vikas"})
    for k in db.all(
            "SELECT k.id, k.kind, k.scheduled_at, o.ref_code, o.kind AS opp_kind, o.id AS opp_id, "
            "COALESCE(bs.locality, c.locality) AS area FROM consultations k JOIN opportunities o ON o.id = k.opportunity_id "
            "LEFT JOIN salon_businesses bs ON bs.id = o.business_id LEFT JOIN contacts c ON c.id = o.contact_id "
            "WHERE k.status = 'booked' AND k.scheduled_at >= ? AND k.scheduled_at < ? AND "
            "(o.primary_owner_id = ? OR k.created_by = ? OR ? = 1)", (a, b, user_id, user_id, is_vikas)):
        out.append({"kind": "consultation", "id": k["id"], "at": k["scheduled_at"], "title": AP.LABELS[k["kind"]],
                    "type": "visit" if k["kind"] == "salon_visit" else "office", "type_label": AP.LABELS[k["kind"]],
                    "minutes": AP.minutes_for(k["kind"]), "ref": k["ref_code"], "area": k["area"], "priority": "high",
                    "link": f"/sellers/{k['opp_id']}?tab=pilot#consult"})
    for e in out:
        e["length"] = AP.length_label(e["minutes"])
        e["gcal"] = AP.gcal_url(e["type_label"] + (f" · {e['ref']}" if e["ref"] else ""), e["at"], e["minutes"],
                                "Salon Shastra TeamOS" + (f" — {e['ref']}" if e["ref"] else ""), e["area"] or "")
    out.sort(key=lambda e: e["at"])
    return out


def week(db, user_id: int, start_date) -> list[dict]:
    """Seven days, each with its events (times in IST)."""
    evs = events(db, user_id, start_date, 7)
    days = []
    for i in range(7):
        d = start_date + timedelta(days=i)
        items = [e for e in evs if parse_iso(e["at"]).astimezone(IST).date() == d]
        for e in items:
            e["time"] = parse_iso(e["at"]).astimezone(IST).strftime("%I:%M %p").lstrip("0")
            e["end_time"] = (parse_iso(e["at"]) + timedelta(minutes=e["minutes"])).astimezone(IST).strftime(
                "%I:%M %p").lstrip("0")
        days.append({"date": d, "events": items, "today": d == now_utc().astimezone(IST).date()})
    return days


# -- Private subscribe link -----------------------------------------------------------------------------------
def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_feed(db, actor: dict) -> str:
    """Create a new private link for this person (any older link stops working). Returns the secret once."""
    now = now_iso()
    db.execute("UPDATE calendar_feeds SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL", (now, actor["id"]))
    token = secrets.token_urlsafe(24)
    db.insert("calendar_feeds", {"user_id": actor["id"], "token_hash": _hash(token), "created_at": now})
    audit.record(db, actor["id"], "calendar.feed_created", "user", actor["id"], {})
    return token


def revoke_feed(db, actor: dict) -> None:
    db.execute("UPDATE calendar_feeds SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL",
               (now_iso(), actor["id"]))
    audit.record(db, actor["id"], "calendar.feed_revoked", "user", actor["id"], {})


def has_feed(db, user_id: int) -> dict | None:
    return db.one("SELECT created_at, last_used_at FROM calendar_feeds WHERE user_id = ? AND revoked_at IS NULL",
                  (user_id,))


def feed_user(db, token: str) -> dict | None:
    row = db.one("SELECT u.* FROM calendar_feeds f JOIN users u ON u.id = f.user_id WHERE f.token_hash = ? AND "
                 "f.revoked_at IS NULL AND u.is_active = 1", (_hash(token or ""),))
    if row:
        db.execute("UPDATE calendar_feeds SET last_used_at = ? WHERE token_hash = ?", (now_iso(), _hash(token)))
    return row


def _ics_text(s: str) -> str:
    return (s or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def ics(db, user: dict, base_url: str) -> str:
    """iCalendar text for the next 60 days (and the last 7). Privacy: no names, numbers or free-text titles."""
    start = now_utc().astimezone(IST).date() - timedelta(days=7)
    stamp = now_utc().strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Salon Shastra//TeamOS//EN", "CALSCALE:GREGORIAN",
             "X-WR-CALNAME:Salon Shastra work", "X-PUBLISHED-TTL:PT30M"]
    for e in events(db, user["id"], start, 67):
        begin = parse_iso(e["at"])
        minutes = e["minutes"]
        summary = e["type_label"] + (f" · {e['ref']}" if e["ref"] else "") + (f" ({e['area']})" if e["area"] else "")
        lines += ["BEGIN:VEVENT", f"UID:teamos-{e['kind']}-{e['id']}@salonshastra", f"DTSTAMP:{stamp}",
                  "DTSTART:" + begin.strftime("%Y%m%dT%H%M%SZ"),
                  "DTEND:" + (begin + timedelta(minutes=minutes)).strftime("%Y%m%dT%H%M%SZ"),
                  "SUMMARY:" + _ics_text(summary),
                  "DESCRIPTION:" + _ics_text("Open in TeamOS (sign-in needed): " + base_url + e["link"]),
                  "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"
