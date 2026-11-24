"""Team availability and activity — modelled on how leading CRMs track relationship managers.

* Live status (Available / Busy / Out on a visit / On a break / Off for the day), set from the top bar.
* Weekly working hours (default Mon–Sat 10:00–19:00 IST, Sunday off).
* Time off: leave, sick leave, half day, field work, training, public holiday. Full-day leave (leave, sick, holiday)
  stops new leads being given to that person, the same as "away".
* Activity: calls, WhatsApp messages, tasks done, new records, stage moves, first and last activity of the day.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from .. import audit
from ..permissions import user_can
from ..timeutil import IST, now_iso, now_utc, parse_iso, to_iso
from .errors import PermissionDenied, ValidationError

STATUSES = [("available", "Available"), ("busy", "Busy / in a meeting"), ("in_field", "Out on a salon visit"),
            ("on_break", "On a break"), ("offline", "Off for the day")]
STATUS_LABELS = dict(STATUSES)
KINDS = [("leave", "Leave"), ("sick", "Sick leave"), ("half_day", "Half day"), ("field", "Field work (salon visits)"),
         ("training", "Training"), ("holiday", "Public holiday")]
KIND_LABELS = dict(KINDS)
AWAY_KINDS = {"leave", "sick", "holiday"}  # not working at all that day
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
DEFAULT = {"start_time": "10:00", "end_time": "19:00"}
STAFF_ROLES = ("team_member", "team_lead", "super_admin")


def _ist_today() -> date:
    return now_utc().astimezone(IST).date()


def _can_edit(db, actor, user_id) -> None:
    if actor["id"] != user_id and not user_can(db, actor, "crm.assign"):
        raise PermissionDenied("Only a team lead can change someone else's availability.")


# -- Working hours ------------------------------------------------------------------------------------------------
def hours(db, user_id: int) -> list[dict]:
    rows = {r["weekday"]: r for r in db.all("SELECT * FROM working_hours WHERE user_id = ?", (user_id,))}
    out = []
    for d in range(7):
        r = rows.get(d)
        if r:
            out.append({"weekday": d, "day": DAYS[d], "is_off": bool(r["is_off"]), "start_time": r["start_time"],
                        "end_time": r["end_time"]})
        else:
            out.append({"weekday": d, "day": DAYS[d], "is_off": d == 6, **DEFAULT})
    return out


def save_hours(db, actor, user_id: int, form) -> None:
    _can_edit(db, actor, user_id)
    rows = []
    for d in range(7):
        off = form.get(f"d{d}_off") == "1"
        s, e = (form.get(f"d{d}_start") or "").strip(), (form.get(f"d{d}_end") or "").strip()
        if not off:
            if not (re.fullmatch(r"\d{2}:\d{2}", s) and re.fullmatch(r"\d{2}:\d{2}", e)):
                raise ValidationError({"hours": f"{DAYS[d]}: give a start and end time, or tick Off."})
            if e <= s:
                raise ValidationError({"hours": f"{DAYS[d]}: the end time must be after the start time."})
        rows.append((d, 1 if off else 0, None if off else s, None if off else e))
    with db:
        db.execute("DELETE FROM working_hours WHERE user_id = ?", (user_id,))
        for d, off, s, e in rows:
            db.insert("working_hours", {"user_id": user_id, "weekday": d, "is_off": off, "start_time": s,
                                        "end_time": e})
        audit.record(db, actor["id"], "availability.hours", "user", user_id, {})


# -- Live status ----------------------------------------------------------------------------------------------------
def set_status(db, actor, status: str, note: str | None = None, user_id: int | None = None) -> None:
    uid = user_id or actor["id"]
    _can_edit(db, actor, uid)
    if status not in STATUS_LABELS:
        raise ValidationError({"status": "Choose a status."})
    db.execute("UPDATE users SET work_status = ?, work_status_note = ?, work_status_at = ? WHERE id = ?",
               (status, (note or "").strip()[:120] or None, now_iso(), uid))


# -- Time off -------------------------------------------------------------------------------------------------------
def add_time_off(db, actor, user_id: int, start: str, end: str | None, kind: str, note: str | None) -> int:
    _can_edit(db, actor, user_id)
    end = end or start
    if kind not in KIND_LABELS:
        raise ValidationError({"kind": "Choose the type of time off."})
    if not (re.fullmatch(r"\d{4}-\d{2}-\d{2}", start or "") and re.fullmatch(r"\d{4}-\d{2}-\d{2}", end)):
        raise ValidationError({"dates": "Choose the dates."})
    if end < start:
        raise ValidationError({"dates": "The last day must be on or after the first day."})
    if end < _ist_today().isoformat():
        raise ValidationError({"dates": "These dates are in the past."})
    if (date.fromisoformat(end) - date.fromisoformat(start)).days > 60:
        raise ValidationError({"dates": "Time off can be at most 60 days at a time."})
    if kind == "half_day" and end != start:
        raise ValidationError({"dates": "A half day is a single date."})
    with db:
        tid = db.insert("time_off", {"user_id": user_id, "start_date": start, "end_date": end, "kind": kind,
                                     "note": (note or "").strip()[:200] or None, "created_by": actor["id"],
                                     "created_at": now_iso()})
        audit.record(db, actor["id"], "availability.time_off", "user", user_id,
                     {"start": start, "end": end, "kind": kind})
    return tid


def cancel_time_off(db, actor, tid: int) -> None:
    row = db.one("SELECT * FROM time_off WHERE id = ?", (tid,))
    if not row or row["cancelled_at"]:
        raise ValidationError({"time_off": "Not found."})
    _can_edit(db, actor, row["user_id"])
    db.execute("UPDATE time_off SET cancelled_at = ? WHERE id = ?", (now_iso(), tid))
    audit.record(db, actor["id"], "availability.time_off_cancelled", "user", row["user_id"], {"time_off": tid})


def time_off(db, user_id: int | None = None, upcoming_only: bool = True) -> list[dict]:
    sql = ("SELECT t.*, u.full_name FROM time_off t JOIN users u ON u.id = t.user_id WHERE t.cancelled_at IS NULL")
    params: list = []
    if user_id:
        sql += " AND t.user_id = ?"
        params.append(user_id)
    if upcoming_only:
        sql += " AND t.end_date >= ?"
        params.append(_ist_today().isoformat())
    return db.all(sql + " ORDER BY t.start_date", params)


def away_ids(db, day: date | None = None) -> set[int]:
    """People on full-day leave on this IST date (default today) — no new leads for them."""
    d = (day or _ist_today()).isoformat()
    return {r["user_id"] for r in db.all("SELECT user_id FROM time_off WHERE cancelled_at IS NULL AND start_date <= ? "
                                         "AND end_date >= ? AND kind IN ('leave','sick','holiday')", (d, d))}


# -- "Where is everyone?" ---------------------------------------------------------------------------------------------
def _day_off(db, uid, d: date, offs: list[dict], hrs: list[dict]) -> tuple[str, str] | None:
    ds = d.isoformat()
    for t in offs:
        if t["user_id"] == uid and t["start_date"] <= ds <= t["end_date"]:
            return t["kind"], KIND_LABELS[t["kind"]]
    if hrs[d.weekday()]["is_off"]:
        return "weekly_off", "Weekly off"
    return None


def state(db, user: dict, now: datetime | None = None, offs: list[dict] | None = None,
          hrs: list[dict] | None = None) -> dict:
    """What a person is doing right now: label, tone (ok / warn / grey) and a detail line."""
    now_l = (now or now_utc()).astimezone(IST)
    offs = time_off(db, user["id"]) if offs is None else offs
    hrs = hours(db, user["id"]) if hrs is None else hrs
    off = _day_off(db, user["id"], now_l.date(), offs, hrs)
    if off and off[0] in AWAY_KINDS | {"weekly_off"}:
        back = _next_working(db, user["id"], now_l, offs, hrs)
        return {"key": "off", "label": off[1], "tone": "grey", "detail": f"back {back}" if back else ""}
    h = hrs[now_l.weekday()]
    hm = now_l.strftime("%H:%M")
    if not h["is_off"] and not (h["start_time"] <= hm < h["end_time"]):
        when = (f"from {h['start_time']} today" if hm < h["start_time"]
                else f"back {_next_working(db, user['id'], now_l, offs, hrs)}")
        return {"key": "off_hours", "label": "Off duty", "tone": "grey", "detail": when}
    st = user.get("work_status") or "available"
    # a status set on an earlier day doesn't carry over (except "available")
    at = parse_iso(user.get("work_status_at")) if user.get("work_status_at") else None
    if at and at.astimezone(IST).date() != now_l.date():
        st = "available"
    label = STATUS_LABELS.get(st, "Available")
    if off:  # half day / field / training
        label = off[1] if st == "available" else label
    tone = "ok" if st == "available" else "grey" if st == "offline" else "warn"
    detail = (user.get("work_status_note") or "") if st != "available" else (
        f"until {h['end_time']}" if not h["is_off"] else "")
    return {"key": st, "label": label, "tone": tone, "detail": detail}


def _next_working(db, uid, now_l: datetime, offs, hrs) -> str:
    for i in range(1, 62):
        d = (now_l + timedelta(days=i)).date()
        if not _day_off(db, uid, d, offs, hrs):
            h = hrs[d.weekday()]
            return ("tomorrow" if i == 1 else d.strftime("%a %d %b")) + f" {h['start_time']}"
    return ""


def week(db, start: date | None = None, days: int = 7) -> dict:
    """Everyone's next `days` days: working hours, weekly off or time off per cell."""
    start = start or _ist_today()
    users = db.all("SELECT id, full_name, role, team, work_status, work_status_note, work_status_at FROM users "
                   f"WHERE is_active = 1 AND role IN {STAFF_ROLES} ORDER BY full_name")
    offs = time_off(db)
    dates = [start + timedelta(days=i) for i in range(days)]
    rows = []
    for u in users:
        hrs = hours(db, u["id"])
        cells = []
        for d in dates:
            off = _day_off(db, u["id"], d, offs, hrs)
            if off and off[0] in AWAY_KINDS | {"weekly_off"}:
                cells.append({"tone": "off", "text": off[1]})
            else:
                h = hrs[d.weekday()]
                text = f"{h['start_time']}–{h['end_time']}"
                cells.append({"tone": "part" if off else "on", "text": text, "note": off[1] if off else ""})
        rows.append({**u, "now": state(db, u, offs=offs, hrs=hrs), "cells": cells})
    return {"dates": dates, "rows": rows}


# -- Activity (relationship-manager tracking) -----------------------------------------------------------------------
def activity(db, period: str = "today") -> dict:
    from ..timeutil import ist_day_bounds
    day_start, day_end = ist_day_bounds()
    if period == "week":
        local = now_utc().astimezone(IST)
        start = to_iso((local - timedelta(days=local.weekday())).replace(hour=0, minute=0, second=0, microsecond=0))
    elif period == "month":
        local = now_utc().astimezone(IST)
        start = to_iso(local.replace(day=1, hour=0, minute=0, second=0, microsecond=0))
    else:
        period, start = "today", day_start
    end = day_end
    users = db.all(f"SELECT id, full_name, role, team, work_status, work_status_note, work_status_at FROM users "
                   f"WHERE is_active = 1 AND role IN {STAFF_ROLES} ORDER BY full_name")

    def by_user(sql, params):
        return {r["uid"]: r for r in db.all(sql, params)}
    acts = by_user("SELECT actor_id AS uid, SUM(CASE WHEN kind = 'call' THEN 1 ELSE 0 END) AS calls, "
                   "SUM(CASE WHEN kind = 'call' AND call_outcome = 'connected' THEN 1 ELSE 0 END) AS connected, "
                   "SUM(CASE WHEN kind = 'note' THEN 1 ELSE 0 END) AS notes, COUNT(*) AS actions, "
                   "MIN(at) AS first_at, MAX(at) AS last_at FROM activities WHERE at >= ? AND at < ? "
                   "AND actor_id IS NOT NULL GROUP BY actor_id", (start, end))
    msgs = by_user("SELECT sent_by AS uid, COUNT(*) AS n FROM messages WHERE direction = 'out' AND created_at >= ? "
                   "AND created_at < ? AND sent_by IS NOT NULL GROUP BY sent_by", (start, end))
    done = by_user("SELECT completed_by AS uid, COUNT(*) AS n FROM tasks WHERE status = 'done' AND completed_at >= ? "
                   "AND completed_at < ? AND completed_by IS NOT NULL GROUP BY completed_by", (start, end))
    new = by_user("SELECT created_by AS uid, COUNT(*) AS n FROM opportunities WHERE created_at >= ? AND created_at < ? "
                  "AND created_by IS NOT NULL GROUP BY created_by", (start, end))
    moves = by_user("SELECT actor_id AS uid, COUNT(*) AS n FROM stage_history WHERE at >= ? AND at < ? "
                    "AND actor_id IS NOT NULL GROUP BY actor_id", (start, end))
    over = by_user("SELECT assignee_id AS uid, COUNT(*) AS n FROM tasks WHERE status = 'open' AND due_at IS NOT NULL "
                   "AND due_at < ? GROUP BY assignee_id", (now_iso(),))
    seen = by_user("SELECT user_id AS uid, MAX(last_seen_at) AS at FROM sessions GROUP BY user_id", ())
    offs = time_off(db)
    rows = []
    for u in users:
        a = acts.get(u["id"]) or {}
        row = {**u, "calls": a.get("calls") or 0, "connected": a.get("connected") or 0, "notes": a.get("notes") or 0,
               "messages": (msgs.get(u["id"]) or {}).get("n") or 0, "tasks_done": (done.get(u["id"]) or {}).get("n") or 0,
               "new_records": (new.get(u["id"]) or {}).get("n") or 0, "moves": (moves.get(u["id"]) or {}).get("n") or 0,
               "overdue": (over.get(u["id"]) or {}).get("n") or 0, "first_at": a.get("first_at"),
               "last_at": a.get("last_at"), "seen_at": (seen.get(u["id"]) or {}).get("at"),
               "now": state(db, u, offs=offs)}
        row["score"] = row["calls"] + row["messages"] + row["tasks_done"] * 2 + row["new_records"] * 2 + row["moves"]
        rows.append(row)
    totals = {k: sum(r[k] for r in rows) for k in ("calls", "connected", "messages", "tasks_done", "new_records",
                                                    "moves", "overdue")}
    return {"period": period, "rows": rows, "totals": totals}
