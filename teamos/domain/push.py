"""Push notifications to phones and computers (standard Web Push — works with the app closed).

* Every in-app alert (tasks.notify) can also become a push, by category, as each person chooses.
* The push itself carries no customer data: it only wakes the phone, which then asks TeamOS (signed in) what is new
  and shows it. So nothing private passes through Google's / Apple's / Mozilla's push servers.
* Bursts are calmed: one push per person per minute; the notification then says how many updates are waiting.
* Keys (VAPID) are made once by TeamOS itself and kept in the database — nobody has to handle them.
"""
from __future__ import annotations

import base64
import json
import logging
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

from ..timeutil import now_iso

log = logging.getLogger("teamos.push")
CATEGORIES = [("new_lead", "New leads given to me"), ("hot", "Hot leads"),
              ("followup", "Overdue follow-ups and reminders"), ("vikas", "Calls and meetings with Vikas"),
              ("messages", "WhatsApp replies"), ("team", "Mentions, approvals and forms"),
              ("reminders", "Hourly status check and the evening screenshot reminder"),
              ("team_work", "Team screenshots uploaded (admin)")]
KIND_CATEGORY = {"assignment": "new_lead", "task_assigned": "new_lead", "sheet_new_rows": "new_lead",
                 "hot_lead": "hot", "overdue": "followup", "stuck": "followup", "stuck_team": "followup",
                 "escalation": "followup", "due_soon": "followup", "vikas_booking": "vikas", "message": "messages",
                 "status_check": "reminders", "proof_reminder": "reminders", "work_proof": "team_work"}
CALM_SECONDS = 60
SUBJECT = "mailto:support@salonshastra.com"


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


# -- Keys -------------------------------------------------------------------------------------------------------------
def keys(db) -> tuple[str, str]:
    """(private key PEM, public key base64url) — made once, kept in integration_settings (encrypted when the
    server has its FILES_KEY; never shown on any page)."""
    pem = _load_private(db)
    if not pem:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        k = ec.generate_private_key(ec.SECP256R1())
        pem = k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                              serialization.NoEncryption()).decode()
        from ..integrations import _fernet
        fer = _fernet()
        stored, secret = (fer.encrypt(pem.encode()).decode(), 1) if fer else (pem, 0)
        if not db.scalar("SELECT 1 FROM integration_settings WHERE key = 'PUSH_VAPID_PRIVATE'"):
            db.insert("integration_settings", {"key": "PUSH_VAPID_PRIVATE", "value": stored, "is_secret": secret,
                                               "updated_at": now_iso()})
        pem = _load_private(db)
    return pem, _public_from(pem)


def _load_private(db) -> str | None:
    r = db.one("SELECT value, is_secret FROM integration_settings WHERE key = 'PUSH_VAPID_PRIVATE'")
    if not r or not r["value"]:
        return None
    if not r["is_secret"]:
        return r["value"]
    from ..integrations import _fernet
    fer = _fernet()
    try:
        return fer.decrypt(r["value"].encode()).decode() if fer else None
    except Exception:  # noqa: BLE001 — key rotated: make a fresh pair (old devices re-subscribe)
        db.execute("DELETE FROM integration_settings WHERE key = 'PUSH_VAPID_PRIVATE'")
        db.execute("DELETE FROM push_subscriptions")
        return None


def _public_from(pem: str) -> str:
    from cryptography.hazmat.primitives import serialization
    k = serialization.load_pem_private_key(pem.encode(), password=None)
    raw = k.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return _b64(raw)


def _vapid_header(pem: str, endpoint: str) -> str:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
    u = urlparse(endpoint)
    header = _b64(json.dumps({"typ": "JWT", "alg": "ES256"}).encode())
    claims = _b64(json.dumps({"aud": f"{u.scheme}://{u.netloc}", "exp": int(time.time()) + 12 * 3600,
                              "sub": SUBJECT}).encode())
    k = serialization.load_pem_private_key(pem.encode(), password=None)
    r, s = decode_dss_signature(k.sign(f"{header}.{claims}".encode(), ec.ECDSA(hashes.SHA256())))
    sig = _b64(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
    return f"vapid t={header}.{claims}.{sig}, k={_public_from(pem)}"


# -- Preferences & subscriptions ---------------------------------------------------------------------------------------
def prefs(db, user_id: int) -> dict:
    raw = db.scalar("SELECT notify_prefs FROM users WHERE id = ?", (user_id,))
    try:
        saved = json.loads(raw) if raw else {}
    except ValueError:
        saved = {}
    return {k: bool(saved.get(k, True)) for k, _l in CATEGORIES}


def save_prefs(db, user_id: int, on: list[str]) -> None:
    db.execute("UPDATE users SET notify_prefs = ? WHERE id = ?",
               (json.dumps({k: (k in on) for k, _l in CATEGORIES}), user_id))


def subscribe(db, user_id: int, sub: dict, device: str = "") -> None:
    endpoint = (sub or {}).get("endpoint") or ""
    keys_ = (sub or {}).get("keys") or {}
    if not endpoint.startswith("https://") or len(endpoint) > 800:
        raise ValueError("bad subscription")
    with db:
        db.execute("DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,))
        db.insert("push_subscriptions", {"user_id": user_id, "endpoint": endpoint, "p256dh": keys_.get("p256dh"),
                                         "auth": keys_.get("auth"), "device": (device or "")[:120],
                                         "created_at": now_iso(), "failures": 0})


def unsubscribe(db, user_id: int, endpoint: str | None = None) -> None:
    if endpoint:
        db.execute("DELETE FROM push_subscriptions WHERE user_id = ? AND endpoint = ?", (user_id, endpoint))
    else:
        db.execute("DELETE FROM push_subscriptions WHERE user_id = ?", (user_id,))


def devices(db, user_id: int) -> list[dict]:
    return db.all("SELECT id, device, created_at, last_ok_at, failures FROM push_subscriptions WHERE user_id = ? "
                  "ORDER BY id DESC", (user_id,))


# -- Sending ------------------------------------------------------------------------------------------------------------
def wanted(db, user_id: int, kind: str) -> bool:
    return prefs(db, user_id).get(KIND_CATEGORY.get(kind, "team"), True)


def on_notification(db, user_id: int, kind: str) -> None:
    """Called after an in-app alert is saved: push it if the person wants this kind and has a device."""
    if not user_id or not wanted(db, user_id, kind):
        return
    if not db.scalar("SELECT 1 FROM push_subscriptions WHERE user_id = ?", (user_id,)):
        return
    db.after_commit(lambda: _send_later(user_id))


_sender = None   # tests replace this with a fake


def _send_later(user_id: int) -> None:
    from flask import current_app, has_app_context

    from .. import background
    if not has_app_context():
        return
    background.run(current_app._get_current_object(), f"push:{user_id}", lambda bdb: send_now(bdb, user_id))


def send_now(db, user_id: int, force: bool = False) -> int:
    """Wake every device of this person (at most once a minute unless forced). Returns how many were woken."""
    pem, _pub = keys(db)
    sent = 0
    for s in db.all("SELECT * FROM push_subscriptions WHERE user_id = ?", (user_id,)):
        if not force and s.get("last_push_at") and time.time() - _epoch(s["last_push_at"]) < CALM_SECONDS:
            continue
        status = (_sender or _post)(s["endpoint"], _vapid_header(pem, s["endpoint"]))
        if status in (404, 410):              # the browser dropped this subscription
            db.execute("DELETE FROM push_subscriptions WHERE id = ?", (s["id"],))
        elif 200 <= status < 300:
            db.execute("UPDATE push_subscriptions SET last_push_at = ?, last_ok_at = ?, failures = 0 WHERE id = ?",
                       (now_iso(), now_iso(), s["id"]))
            sent += 1
        else:
            db.execute("UPDATE push_subscriptions SET last_push_at = ?, failures = failures + 1 WHERE id = ?",
                       (now_iso(), s["id"]))
    return sent


def _epoch(iso: str) -> float:
    from ..timeutil import parse_iso
    return parse_iso(iso).timestamp()


def _post(endpoint: str, auth: str) -> int:
    req = urllib.request.Request(endpoint, data=b"", method="POST",
                                 headers={"Authorization": auth, "TTL": "86400", "Urgency": "high",
                                          "Content-Length": "0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except (urllib.error.URLError, TimeoutError) as e:
        log.warning("push failed: %s", e)
        return 0


def latest(db, user_id: int) -> dict:
    """What the phone shows when woken: the newest unread alert, and how many are waiting."""
    rows = db.all("SELECT title, link, kind FROM notifications WHERE user_id = ? AND read_at IS NULL "
                  "ORDER BY created_at DESC, id DESC LIMIT 20", (user_id,))
    if not rows:
        return {"title": "Salon Shastra TeamOS", "body": "You're all caught up.", "url": "/", "count": 0}
    top = rows[0]
    n = len(rows)
    head = {"hot": "🔥 Hot lead", "new_lead": "New lead", "followup": "Follow-up due", "vikas": "Vikas",
            "messages": "WhatsApp", "reminders": "Reminder", "team_work": "Team screenshots"}.get(
        KIND_CATEGORY.get(top["kind"], "team"), "TeamOS")
    return {"title": head if n == 1 else f"{head} · {n} updates waiting", "body": top["title"][:180],
            "url": top["link"] or "/", "count": n, "tag": "teamos"}
