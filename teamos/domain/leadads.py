"""Milestone 9 — Facebook / Instagram lead ads become seller or buyer leads automatically.

Every lead goes through the same smart import as a Google Sheet (teamos/domain/sheet_sync.py), so it gets exactly
the same treatment: columns understood by meaning, junk and "not interested" rows left out, one person = one record
(an existing person is updated, never duplicated), least-busy available owner, a first-call task, and contact
consent recorded (the person submitted the form).

Two ways in:
* the webhook Meta calls for each new lead (works only once an admin has connected the Facebook page and switched
  it live in Admin → Integrations — off by default);
* "Try a lead ad" in Admin → Integrations, which simulates a lead so the team can see what happens. Marked SIM-….
"""
from __future__ import annotations

import csv
import hashlib
import hmac
import io
import json
import secrets

from ..timeutil import now_iso
from .errors import ValidationError

KIND_HINT = {"seller": "Sellers", "buyer": "Buyers", "new_salon": "New salon"}


def _system_actor(db) -> dict | None:
    return db.one("SELECT * FROM users WHERE is_active = 1 AND role = 'super_admin' ORDER BY id LIMIT 1")


def record(db, leadgen_id: str, form_name: str | None, answers: dict, source: str) -> int | None:
    """Store a delivery once (Meta may retry). Returns the event id, or None if already received."""
    if db.scalar("SELECT 1 FROM lead_ad_events WHERE leadgen_id = ?", (leadgen_id,)):
        return None
    return db.insert("lead_ad_events", {"leadgen_id": leadgen_id, "form_name": (form_name or "")[:200],
                                        "source": source, "payload": json.dumps(answers)[:20000],
                                        "status": "received", "received_at": now_iso()})


def process(db, event_id: int) -> dict:
    """Run one stored lead through the smart import. Returns the event row afterwards."""
    from . import sheet_sync as SS
    ev = db.one("SELECT * FROM lead_ad_events WHERE id = ?", (event_id,))
    if not ev or ev["status"] != "received":
        return ev
    answers = json.loads(ev["payload"])
    if not answers:
        db.update("lead_ad_events", event_id, {"status": "received", "detail": "Waiting for the lead's answers "
                                                                                "(live fetching is off)."})
        return db.one("SELECT * FROM lead_ad_events WHERE id = ?", (event_id,))
    actor = _system_actor(db)
    buf = io.StringIO()
    w = csv.writer(buf)
    headers = ["created_time"] + [k for k in answers if k != "created_time"]
    w.writerow(headers)
    w.writerow([answers.get("created_time") or now_iso()[:19].replace("T", " ")] +
               [answers[k] for k in headers[1:]])
    filename = f"Meta lead ad - {ev['form_name'] or 'form'}.csv".replace("/", "-")
    try:
        iid = SS.create_import(db, actor, filename, buf.getvalue().encode("utf-8"))
        SS.commit(db, actor, iid)
    except ValidationError as e:
        msg = " ".join(str(v) for v in (e.errors.values() if isinstance(e.errors, dict) else [e.errors]))
        db.update("lead_ad_events", event_id, {"status": "skipped", "detail": msg[:500], "processed_at": now_iso()})
        return db.one("SELECT * FROM lead_ad_events WHERE id = ?", (event_id,))
    row = db.one("SELECT action, reason, opportunity_id FROM sheet_import_rows WHERE import_id = ? ORDER BY id LIMIT 1",
                 (iid,)) or {}
    if row.get("action") == "skip":
        status, detail = "skipped", f"Left out: {row.get('reason')}"
    elif row.get("action") == "update":
        status, detail = "imported", "Already known — empty details filled in, nothing overwritten."
    else:
        status, detail = "imported", "New lead created with a first-call task."
    db.update("lead_ad_events", event_id, {"status": status, "detail": detail, "import_id": iid,
                                           "opportunity_id": row.get("opportunity_id"), "processed_at": now_iso()})
    return db.one("SELECT * FROM lead_ad_events WHERE id = ?", (event_id,))


def simulate(db, kind: str, answers: dict) -> dict:
    """Admin test: pretend Meta delivered this lead."""
    answers = {k: (v or "").strip() for k, v in answers.items() if (v or "").strip()}
    if not answers.get("phone_number"):
        raise ValidationError({"phone_number": "A lead ad always has a phone number — enter one."})
    form = f"{KIND_HINT.get(kind, 'Buyers')} form (test)"
    eid = record(db, "SIM-" + secrets.token_hex(6), form, answers, "simulation")
    return process(db, eid)


def recent(db, limit: int = 15) -> list[dict]:
    return db.all("SELECT e.*, o.ref_code, o.kind FROM lead_ad_events e LEFT JOIN opportunities o "
                  "ON o.id = e.opportunity_id ORDER BY e.id DESC LIMIT ?", (limit,))


# -- Webhook ----------------------------------------------------------------------------------------------------
def signature_ok(app_secret: str | None, body: bytes, header: str | None) -> bool:
    if not app_secret or not header or not header.startswith("sha256="):
        return False
    good = hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(good, header[7:])


def from_webhook(db, payload: dict, fetch=None) -> list[int]:
    """Store each lead in a Meta 'leadgen' webhook. `fetch(leadgen_id)` returns (form_name, answers) when live."""
    ids = []
    for entry in payload.get("entry") or []:
        for ch in entry.get("changes") or []:
            if ch.get("field") != "leadgen":
                continue
            v = ch.get("value") or {}
            lid = str(v.get("leadgen_id") or "")
            if not lid:
                continue
            form_name, answers = (None, {})
            if fetch:
                try:
                    form_name, answers = fetch(lid)
                except Exception:  # keep the lead id; it can be fetched again later
                    form_name, answers = None, {}
            eid = record(db, lid, form_name or f"form {v.get('form_id', '')}".strip(), answers, "webhook")
            if eid:
                ids.append(eid)
    return ids


def graph_fetch(token: str):
    """Live only: read the lead's answers from Meta's Graph API."""
    import urllib.request

    def fetch(leadgen_id: str):
        url = f"https://graph.facebook.com/v21.0/{leadgen_id}?fields=created_time,field_data,form_id"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=20) as r:  # noqa: S310 — fixed Meta host
            data = json.loads(r.read().decode())
        answers = {f["name"]: ", ".join(f.get("values") or []) for f in data.get("field_data") or []}
        answers["created_time"] = (data.get("created_time") or "")[:19].replace("T", " ")
        form_name = None
        if data.get("form_id"):
            req = urllib.request.Request(f"https://graph.facebook.com/v21.0/{data['form_id']}?fields=name",
                                         headers={"Authorization": f"Bearer {token}"})
            with urllib.request.urlopen(req, timeout=20) as r:  # noqa: S310
                form_name = json.loads(r.read().decode()).get("name")
        return form_name, answers
    return fetch
