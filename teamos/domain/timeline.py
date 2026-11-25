"""CRM timeline for a record: when the lead came in, when it reached TeamOS, who owns it, how fast the team first
reached them, and every milestone since (calls, WhatsApp, forms, reports, Vikas, stage changes)."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from ..timeutil import IST, now_iso, parse_iso, to_iso

SOURCE_NAMES = {"meta_lead": "Facebook / Instagram ad", "import": "Sheet import", "landing_page": "Website form",
                "manual_call": "Phone call", "referral": "Referral", "whatsapp": "WhatsApp", "walk_in": "Walk-in"}


def lead_time(v) -> str | None:
    """The lead's own time from a sheet / ad, as UTC ISO: '2026-11-22T01:15:43-05:00' (Meta), '2026-11-22 10:30:00',
    '22/11/2026 10:30:00' (Google Forms, Indian time), '2026-11-22' (date only → 10:00 IST)."""
    s = str(v or "").strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=IST)
        return to_iso(dt.astimezone(timezone.utc))
    except ValueError:
        pass
    for fmt in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%m/%d/%Y %H:%M:%S", "%d-%m-%Y %H:%M:%S", "%d/%m/%Y",
                "%d-%m-%Y", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(s[:19], fmt)
            if "%H" not in fmt:
                dt = dt.replace(hour=10)
            return to_iso(dt.replace(tzinfo=IST).astimezone(timezone.utc))
        except ValueError:
            continue
    return None


def mark_contacted(db, opp_id: int, when: str | None = None) -> None:
    """A call / WhatsApp reached the person: last contact now; first contact only the first time."""
    when = when or now_iso()
    db.execute("UPDATE opportunities SET last_contact_at = ?, first_contact_at = COALESCE(first_contact_at, ?), "
               "updated_at = ? WHERE id = ?", (when, when, when, opp_id))


def span(a: str | None, b: str | None) -> str | None:
    """'3 h 20 min', '2 days', '15 min' between two ISO times."""
    if not a or not b:
        return None
    secs = (parse_iso(b) - parse_iso(a)).total_seconds()
    if secs < 0:
        return None
    mins = int(secs // 60)
    if mins < 60:
        return f"{mins} min"
    hours = mins // 60
    if hours < 48:
        return f"{hours} h {mins % 60} min" if hours < 6 else f"{hours} h"
    return f"{hours // 24} days"


def speed(opp: dict) -> dict:
    """Speed to first contact: {'label', 'tone'} — good within 1 hour, ok same day, slow after that."""
    lead = opp.get("lead_created_at") or opp.get("created_at")
    first = opp.get("first_contact_at")
    if first:
        secs = (parse_iso(first) - parse_iso(lead)).total_seconds() if lead else 0
        tone = "ok" if secs <= 3600 else "info" if secs <= 86400 else "warn"
        return {"label": f"First contact after {span(lead, first) or '0 min'}", "tone": tone}
    waited = span(lead, now_iso())
    return {"label": f"Not contacted yet — waiting {waited}" if waited else "Not contacted yet", "tone": "bad"}


def build(db, opp: dict, limit: int = 14) -> list[dict]:
    """Milestones, newest first: [{at, icon, title, note}]."""
    oid = opp["id"]
    ev = []
    lead = opp.get("lead_created_at") or opp.get("created_at")
    src = SOURCE_NAMES.get(opp.get("source"), (opp.get("source") or "").replace("_", " ").capitalize())
    detail = opp.get("source_detail") or ""
    ev.append({"at": lead, "icon": "🎯", "title": "Lead came in", "note": " · ".join(x for x in (src, detail) if x)})
    if opp.get("created_at") and lead and abs((parse_iso(opp["created_at"]) - parse_iso(lead)).total_seconds()) > 120:
        ev.append({"at": opp["created_at"], "icon": "📥", "title": "Added to TeamOS",
                   "note": f"{span(lead, opp['created_at'])} after the lead came in"})
    if opp.get("first_contact_at"):
        ev.append({"at": opp["first_contact_at"], "icon": "⚡", "title": "First contact",
                   "note": f"{span(lead, opp['first_contact_at']) or '0 min'} after the lead came in"})
    labels = {"connected": "spoke", "no_answer": "no answer", "busy": "busy", "switched_off": "switched off",
              "callback_requested": "call back requested", "wrong_number": "wrong number",
              "not_interested": "not interested", "do_not_contact": "do not contact"}
    for a in db.all("SELECT a.at, a.kind, a.body, a.call_outcome, u.full_name FROM activities a LEFT JOIN users u "
                    "ON u.id = a.actor_id WHERE a.entity_type = 'opportunity' AND a.entity_id = ? AND a.kind IN "
                    "('call', 'assignment', 'approval', 'system', 'note') ORDER BY a.at DESC, a.id DESC LIMIT 60",
                    (oid,)):
        body = (a["body"] or "").strip()
        who = a["full_name"] or "System"
        if a["kind"] == "call":
            ev.append({"at": a["at"], "icon": "📞", "title": f"Call — {labels.get(a['call_outcome'], a['call_outcome'] or '')}",
                       "note": f"{who}" + (f": {body[:140]}" if body else "")})
        elif a["kind"] == "note" and body.startswith("WhatsApp"):
            ev.append({"at": a["at"], "icon": "💬", "title": "WhatsApp", "note": who})
        elif a["kind"] == "assignment":
            ev.append({"at": a["at"], "icon": "👤", "title": "Owner", "note": body[:160]})
        elif a["kind"] in ("approval", "system") and re.search(r"form|report|Vikas|consult|ChairPe|link", body, re.I):
            ev.append({"at": a["at"], "icon": "📝", "title": body[:90] + ("…" if len(body) > 90 else ""),
                       "note": who if a["kind"] == "approval" else ""})
    for h in db.all("SELECT h.at, h.from_stage, h.to_stage, u.full_name FROM stage_history h LEFT JOIN users u "
                    "ON u.id = h.actor_id WHERE h.opportunity_id = ? AND h.from_stage IS NOT NULL ORDER BY h.at DESC "
                    "LIMIT 20", (oid,)):
        ev.append({"at": h["at"], "icon": "➡️", "title": f"Stage: {(h['to_stage'] or '').replace('_', ' ')}",
                   "note": h["full_name"] or ""})
    ev = [e for e in ev if e["at"]]
    ev.sort(key=lambda e: e["at"], reverse=True)
    return ev[:limit]


def age(opp: dict) -> str | None:
    """How long ago the lead came in: '3 h', '2 days'."""
    return span(opp.get("lead_created_at") or opp.get("created_at"), now_iso())


def backfill_lead_times(db, limit: int = 2000) -> int:
    """Records imported from sheets before lead times were kept: take the time from the sheet row (once)."""
    import json
    n = 0
    for r in db.all("SELECT r.opportunity_id, r.data FROM sheet_import_rows r JOIN opportunities o ON "
                    "o.id = r.opportunity_id WHERE r.outcome = 'created' AND o.lead_created_at = o.created_at "
                    "LIMIT ?", (limit,)):
        try:
            t = lead_time(json.loads(r["data"]).get("canon", {}).get("timestamp"))
        except ValueError:
            t = None
        if t:
            db.execute("UPDATE opportunities SET lead_created_at = ? WHERE id = ?", (t, r["opportunity_id"]))
            n += 1
    return n


_ = timedelta  # (kept for callers computing windows)
