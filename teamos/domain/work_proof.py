"""Daily proof of work, and the nudges that keep the team's day visible to the admin.

* Each team member uploads screenshots of the calls and WhatsApp chats they did that day. Images are shrunk (a phone
  screenshot of ~2 MB becomes ~150 KB, still readable), encrypted and kept in the database; after RETENTION_DAYS they
  are deleted so the free database does not fill up.
* The admin gets a separate alert for each upload and sees everyone's day on one page.
* 7:30 PM IST: anyone working today who has not uploaded yet gets a reminder (push + bell), once a day.
* Every hour of their working day: a reminder to update their status (available / busy / out on a visit …), unless
  they changed it in the last hour. Nobody on leave, off duty or outside their working hours is disturbed.
"""
from __future__ import annotations

import io
import secrets
from datetime import datetime, timedelta

from ..permissions import user_can
from ..timeutil import IST, now_iso, now_utc, parse_iso, to_iso
from . import tasks as T
from .errors import PermissionDenied, ValidationError

KINDS = [("calls", "Phone calls"), ("whatsapp", "WhatsApp chats"), ("other", "Other work")]
KIND_LABELS = dict(KINDS)
TEAM_ROLES = ("team_member", "team_lead")          # who uploads; admins (super_admin) review
MAX_FILES = 25
MAX_BYTES = 12 * 1024 * 1024                         # per original image, before shrinking
RETENTION_DAYS = 60
EVENING_REMINDER = "19:30"
STATUS_EVERY_MIN = 60


def today_ist() -> str:
    return now_utc().astimezone(IST).strftime("%Y-%m-%d")


def _shrink(data: bytes) -> bytes:
    """Any phone screenshot (PNG / JPG / WEBP) → a readable JPEG of at most ~1600 px tall and 900 px wide."""
    from PIL import Image, UnidentifiedImageError
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
    except (UnidentifiedImageError, OSError):
        raise ValidationError({"files": "Only photos or screenshots (JPG, PNG or WEBP) can be uploaded."})
    if im.mode not in ("RGB", "L"):
        bg = Image.new("RGB", im.size, (255, 255, 255))
        im = im.convert("RGBA")
        bg.paste(im, mask=im.split()[3])
        im = bg
    im.thumbnail((900, 1600))
    out = io.BytesIO()
    im.convert("RGB").save(out, "JPEG", quality=72, optimize=True)
    return out.getvalue()


def save(db, actor, kind: str, files: list[tuple[bytes, str]], note: str | None = None,
         day: str | None = None) -> int:
    """Store the uploaded screenshots for today. files = [(bytes, filename)]. Returns how many were saved."""
    if actor["role"] not in TEAM_ROLES + ("super_admin",) or not user_can(db, actor, "crm.log_activity"):
        raise PermissionDenied("crm.log_activity")
    if kind not in KIND_LABELS:
        raise ValidationError({"kind": "Choose what the screenshots show."})
    files = [(b, n) for b, n in files if b]
    if not files:
        raise ValidationError({"files": "Choose at least one screenshot."})
    if len(files) > MAX_FILES:
        raise ValidationError({"files": f"Up to {MAX_FILES} screenshots at a time."})
    from ..files import _fernet
    fer = _fernet()
    if not fer:
        raise ValidationError({"files": "Uploads are not set up on this server (FILES_KEY missing)."})
    shrunk = []
    for data, _name in files:
        if len(data) > MAX_BYTES:
            raise ValidationError({"files": "Each screenshot must be under 12 MB."})
        shrunk.append(_shrink(data))
    day = day or today_ist()
    now = now_iso()
    with db:
        for img in shrunk:
            stored = "wp-" + secrets.token_hex(14)
            db.insert("document_blobs", {"stored_name": stored, "data": fer.encrypt(img).decode("ascii"),
                                         "created_at": now})
            db.insert("work_proofs", {"user_id": actor["id"], "day": day, "kind": kind, "stored_name": stored,
                                      "content_type": "image/jpeg", "size_bytes": len(img),
                                      "note": (note or "").strip()[:300] or None, "created_at": now})
        n = len(shrunk)
        counts = day_counts(db, actor["id"], day)
        summary = ", ".join(f"{c} {KIND_LABELS[k].lower()}" for k, c in counts.items() if c)
        for a in db.all("SELECT id FROM users WHERE is_active = 1 AND role = 'super_admin' AND id <> ?",
                        (actor["id"],)):
            T.notify(db, a["id"], "work_proof",
                     f"{actor['full_name']} uploaded {n} screenshot{'s' if n != 1 else ''} of {KIND_LABELS[kind].lower()}"
                     f" · today so far: {summary}", f"/work/team?day={day}#u{actor['id']}")
    return n


def day_counts(db, user_id: int, day: str) -> dict:
    out = {k: 0 for k, _l in KINDS}
    for r in db.all("SELECT kind, COUNT(*) AS n FROM work_proofs WHERE user_id = ? AND day = ? GROUP BY kind",
                    (user_id, day)):
        out[r["kind"]] = r["n"]
    return out


def mine(db, user_id: int, day: str) -> list[dict]:
    return db.all("SELECT * FROM work_proofs WHERE user_id = ? AND day = ? ORDER BY id DESC", (user_id, day))


def team_day(db, day: str) -> list[dict]:
    """Everyone who should upload, with their screenshots for the day (people without any first)."""
    from . import availability as AV
    people = db.all("SELECT * FROM users WHERE is_active = 1 AND role IN ('team_member', 'team_lead') "
                    "ORDER BY full_name")
    proofs = db.all("SELECT * FROM work_proofs WHERE day = ? ORDER BY id", (day,))
    away = AV.away_ids(db, datetime.strptime(day, "%Y-%m-%d").date())
    out = []
    for p in people:
        items = [x for x in proofs if x["user_id"] == p["id"]]
        calls = db.scalar("SELECT COUNT(*) FROM activities WHERE actor_id = ? AND kind = 'call' AND at >= ? AND at < ?",
                          (p["id"], *_day_bounds(day))) or 0
        out.append({"user": p, "shots": items, "away": p["id"] in away, "calls_logged": calls,
                    "counts": {k: sum(1 for x in items if x["kind"] == k) for k, _l in KINDS}})
    out.sort(key=lambda r: (bool(r["shots"]) or r["away"], r["user"]["full_name"]))
    return out


def _day_bounds(day: str) -> tuple[str, str]:
    start = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=IST)
    return to_iso(start), to_iso(start + timedelta(days=1))


def can_see(db, viewer: dict, proof: dict) -> bool:
    return proof["user_id"] == viewer["id"] or user_can(db, viewer, "users.manage")


def image(db, viewer: dict, proof_id: int) -> tuple[dict, bytes]:
    p = db.one("SELECT * FROM work_proofs WHERE id = ?", (proof_id,))
    if not p:
        raise ValidationError({"id": "Not found."})
    if not can_see(db, viewer, p):
        raise PermissionDenied("users.manage")
    from ..files import read
    return p, read(p["stored_name"])


def delete(db, actor, proof_id: int) -> None:
    """A person may remove their own screenshot on the same day (e.g. a wrong picture)."""
    p = db.one("SELECT * FROM work_proofs WHERE id = ?", (proof_id,))
    if not p or not (p["user_id"] == actor["id"] and p["day"] == today_ist()):
        raise PermissionDenied("crm.log_activity")
    with db:
        db.execute("DELETE FROM document_blobs WHERE stored_name = ?", (p["stored_name"],))
        db.execute("DELETE FROM work_proofs WHERE id = ?", (proof_id,))


# -- Reminders (run by the timer every few minutes) -------------------------------------------------------------------
def _working_now(db, user, now) -> bool:
    from . import availability as AV
    return AV.state(db, user, now)["key"] not in ("off", "off_hours")


def send_reminders(db, now: datetime | None = None) -> dict:
    now = now or now_utc()
    local = now.astimezone(IST)
    hm = local.strftime("%H:%M")
    day = local.strftime("%Y-%m-%d")
    day_start, _ = _day_bounds(day)
    sent = {"evening": 0, "status": 0}
    team = db.all("SELECT * FROM users WHERE is_active = 1 AND role IN ('team_member', 'team_lead')")
    from . import availability as AV
    away = AV.away_ids(db, local.date())
    for u in team:
        if u["id"] in away:
            continue
        # evening: screenshots of the day, once
        if EVENING_REMINDER <= hm < "23:00" and not db.scalar(
                "SELECT 1 FROM work_proofs WHERE user_id = ? AND day = ?", (u["id"], day)) and not db.scalar(
                "SELECT 1 FROM notifications WHERE user_id = ? AND kind = 'proof_reminder' AND created_at >= ?",
                (u["id"], day_start)):
            T.notify(db, u["id"], "proof_reminder",
                     "Upload today's screenshots — your call log and WhatsApp chats", "/work/")
            sent["evening"] += 1
        # hourly: status, while they are working
        if not _working_now(db, u, now):
            continue
        hour_ago = to_iso(now - timedelta(minutes=STATUS_EVERY_MIN - 5))
        changed = u.get("work_status_at") and u["work_status_at"] >= hour_ago
        nudged = db.scalar("SELECT 1 FROM notifications WHERE user_id = ? AND kind = 'status_check' AND created_at >= ?",
                           (u["id"], hour_ago))
        if not changed and not nudged:
            db.execute("UPDATE notifications SET read_at = ? WHERE user_id = ? AND kind = 'status_check' AND "
                       "read_at IS NULL", (now_iso(), u["id"]))          # keep only the latest one in the bell
            T.notify(db, u["id"], "status_check", "What are you doing now? Tap to update your status", "/work/status")
            sent["status"] += 1
    return sent


def cleanup(db, days: int = RETENTION_DAYS) -> int:
    cutoff = (now_utc().astimezone(IST) - timedelta(days=days)).strftime("%Y-%m-%d")
    old = db.all("SELECT id, stored_name FROM work_proofs WHERE day < ?", (cutoff,))
    with db:
        for p in old:
            db.execute("DELETE FROM document_blobs WHERE stored_name = ?", (p["stored_name"],))
            db.execute("DELETE FROM work_proofs WHERE id = ?", (p["id"],))
    return len(old)


def week_summary(db, user_id: int, end_day: str, days: int = 7) -> list[dict]:
    end = datetime.strptime(end_day, "%Y-%m-%d")
    out = []
    for i in range(days - 1, -1, -1):
        d = (end - timedelta(days=i)).strftime("%Y-%m-%d")
        out.append({"day": d, "n": db.scalar("SELECT COUNT(*) FROM work_proofs WHERE user_id = ? AND day = ?",
                                             (user_id, d)) or 0})
    return out


_ = parse_iso  # (kept for callers that compare stored times)
