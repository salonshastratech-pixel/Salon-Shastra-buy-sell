"""Work log: hours and output per person per day, for a team that works flexibly from home.

Time comes from the log of sign-ins, sign-outs and status changes; output comes from what they did in TeamOS
(calls, WhatsApps, tasks, forms, new leads, speed to first contact). An open status that was never closed ends at
the person's last activity (+15 min), so a forgotten sign-out does not count a whole night as work.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

from ..timeutil import IST, now_iso, now_utc, parse_iso, to_iso

WORKING = {"available", "busy", "in_field"}
GRACE = timedelta(minutes=15)


def log(db, user_id: int, event: str, status: str | None = None, note: str | None = None) -> None:
    db.insert("work_log", {"user_id": user_id, "event": event, "status": status, "note": (note or "")[:120] or None,
                           "at": now_iso()})


def on_login(db, user: dict) -> None:
    """Signing in starts the day: logged, and someone 'off for the day' becomes 'available'."""
    log(db, user["id"], "login")
    if (user.get("work_status") or "offline") == "offline":
        db.execute("UPDATE users SET work_status = 'available', work_status_note = NULL, work_status_at = ? "
                   "WHERE id = ?", (now_iso(), user["id"]))
        log(db, user["id"], "status", "available", "signed in")


def on_logout(db, user: dict) -> None:
    log(db, user["id"], "logout")
    db.execute("UPDATE users SET work_status = 'offline', work_status_note = NULL, work_status_at = ? WHERE id = ?",
               (now_iso(), user["id"]))
    log(db, user["id"], "status", "offline", "signed out")


def _bounds(day: date) -> tuple[str, str]:
    s = datetime.combine(day, time(0, 0), tzinfo=IST)
    return to_iso(s), to_iso(s + timedelta(days=1))


def _fmt(td: timedelta) -> str:
    mins = int(td.total_seconds() // 60)
    return f"{mins // 60} h {mins % 60:02d} min" if mins >= 60 else f"{mins} min"


def day_summary(db, user: dict, day: date) -> dict:
    start, end = _bounds(day)
    events = db.all("SELECT event, status, note, at FROM work_log WHERE user_id = ? AND at >= ? AND at < ? ORDER BY at, id",
                    (user["id"], start, end))
    before = db.one("SELECT status FROM work_log WHERE user_id = ? AND event = 'status' AND at < ? ORDER BY at DESC, "
                    "id DESC LIMIT 1", (user["id"], start))
    last_act = db.scalar("SELECT MAX(at) FROM activities WHERE actor_id = ? AND at >= ? AND at < ?",
                         (user["id"], start, end))
    seen = db.scalar("SELECT MAX(last_seen_at) FROM sessions WHERE user_id = ?", (user["id"],))
    cap = min(parse_iso(end), now_utc())
    alive = max([parse_iso(x) for x in (last_act, seen) if x] or [parse_iso(start)]) + GRACE
    cap = min(cap, max(alive, parse_iso(start)))
    cur = (before or {}).get("status") or "offline"
    t = parse_iso(start)
    totals = {"work": timedelta(), "break": timedelta(), "field": timedelta()}
    breaks = 0

    def add(until):
        nonlocal t
        until = min(until, cap)
        if until > t:
            d = until - t
            if cur in WORKING:
                totals["work"] += d
            if cur == "in_field":
                totals["field"] += d
            if cur == "on_break":
                totals["break"] += d
        t = max(t, until)
    for e in events:
        if e["event"] != "status":
            continue
        add(parse_iso(e["at"]))
        cur = e["status"]
        breaks += cur == "on_break"
    add(cap)
    logins = [e["at"] for e in events if e["event"] == "login"]
    logouts = [e["at"] for e in events if e["event"] == "logout"]
    return {"events": events, "first_in": logins[0] if logins else None, "last_out": logouts[-1] if logouts else None,
            "work": _fmt(totals["work"]), "work_min": int(totals["work"].total_seconds() // 60),
            "break": _fmt(totals["break"]), "breaks": breaks, "field": _fmt(totals["field"]),
            "last_activity": last_act}


def output(db, user_id: int, day: date) -> dict:
    start, end = _bounds(day)

    def n(sql, *p):
        return db.scalar(sql, p) or 0
    calls = n("SELECT COUNT(*) FROM activities WHERE actor_id = ? AND kind = 'call' AND at >= ? AND at < ?",
              user_id, start, end)
    connected = n("SELECT COUNT(*) FROM activities WHERE actor_id = ? AND kind = 'call' AND call_outcome = 'connected' "
                  "AND at >= ? AND at < ?", user_id, start, end)
    whatsapp = n("SELECT COUNT(*) FROM activities WHERE actor_id = ? AND kind = 'note' AND body LIKE 'WhatsApp%' "
                 "AND at >= ? AND at < ?", user_id, start, end) + \
        n("SELECT COUNT(*) FROM messages WHERE sent_by = ? AND direction = 'out' AND created_at >= ? AND created_at < ?",
          user_id, start, end)
    tasks = n("SELECT COUNT(*) FROM tasks WHERE completed_by = ? AND status = 'done' AND completed_at >= ? AND "
              "completed_at < ?", user_id, start, end)
    forms = n("SELECT COUNT(*) FROM audit_events WHERE actor_id = ? AND action IN ('pilot.link_created', "
              "'investor.link_created', 'form.invitation_created') AND at >= ? AND at < ?", user_id, start, end)
    moved = n("SELECT COUNT(*) FROM stage_history WHERE actor_id = ? AND at >= ? AND at < ?", user_id, start, end)
    firsts = db.all("SELECT o.lead_created_at, o.created_at, o.first_contact_at FROM opportunities o WHERE "
                    "o.primary_owner_id = ? AND o.first_contact_at >= ? AND o.first_contact_at < ?", (user_id, start, end))
    speeds = [(parse_iso(r["first_contact_at"]) - parse_iso(r["lead_created_at"] or r["created_at"])).total_seconds()
              for r in firsts if (r["lead_created_at"] or r["created_at"])]
    avg = _fmt(timedelta(seconds=sum(speeds) / len(speeds))) if speeds else None
    waiting = n("SELECT COUNT(*) FROM opportunities WHERE primary_owner_id = ? AND status = 'active' AND "
                "first_contact_at IS NULL", user_id)
    return {"calls": calls, "connected": connected, "whatsapp": whatsapp, "tasks": tasks, "forms": forms,
            "moved": moved, "first_contacts": len(firsts), "avg_first_contact": avg, "waiting_first_contact": waiting}


def team_day(db, day: date) -> list[dict]:
    users = db.all("SELECT id, full_name, role, team, work_status, work_status_at FROM users WHERE is_active = 1 AND "
                   "role IN ('team_member', 'team_lead', 'super_admin') ORDER BY full_name")
    return [{**u, **day_summary(db, u, day), "out": output(db, u["id"], day)} for u in users]


def week(db, user: dict, end_day: date) -> list[dict]:
    out = []
    for i in range(6, -1, -1):
        d = end_day - timedelta(days=i)
        s = day_summary(db, user, d)
        out.append({"day": d, "work": s["work"], "work_min": s["work_min"], "break": s["break"],
                    "first_in": s["first_in"], "last_out": s["last_out"], **output(db, user["id"], d)})
    return out
