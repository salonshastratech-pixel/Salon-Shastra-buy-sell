"""Authentication, server-side sessions, CSRF and route guards.

- Passwords: Werkzeug scrypt hashes.
- Sessions: random token in an HttpOnly cookie; only its SHA-256 is stored. Every request
  re-loads the user, so deactivation or revocation takes effect immediately (UAT-27).
- Idle timeout + absolute lifetime; login lockout after repeated failures.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta
from functools import wraps

from flask import abort, current_app, g, redirect, request, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from . import audit
from .db import get_db
from .permissions import user_can
from .timeutil import now_iso, now_utc, to_iso

COOKIE = "teamos_session"
LOCKOUT_FAILS = 5
LOCKOUT_WINDOW_MIN = 15


def hash_password(pw: str) -> str:
    return generate_password_hash(pw, method="scrypt")


def validate_password_strength(pw: str) -> str | None:
    if len(pw) < 10:
        return "Password must be at least 10 characters."
    if pw.lower() == pw or pw.upper() == pw or not any(c.isdigit() for c in pw):
        return "Use upper and lower case letters and at least one number."
    return None


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def is_locked_out(db, email: str) -> bool:
    since = to_iso(now_utc() - timedelta(minutes=LOCKOUT_WINDOW_MIN))
    fails = db.scalar(
        "SELECT COUNT(*) FROM login_attempts WHERE email = ? AND success = 0 AND at > ?", (email, since)
    )
    return (fails or 0) >= LOCKOUT_FAILS


def authenticate(db, email: str, password: str) -> tuple[dict | None, str | None]:
    email = (email or "").strip().lower()
    if is_locked_out(db, email):
        audit.record(db, None, "auth.login", detail={"email": email, "reason": "locked_out"}, outcome="denied")
        return None, "Too many failed attempts. Try again in 15 minutes."
    user = db.one("SELECT * FROM users WHERE email = ?", (email,))
    ok = bool(user and user["is_active"] and check_password_hash(user["password_hash"], password or ""))
    db.insert("login_attempts", {"email": email, "ip": request.remote_addr, "success": int(ok), "at": now_iso()})
    if not ok:
        audit.record(db, user["id"] if user else None, "auth.login", detail={"email": email}, outcome="denied")
        return None, "Email or password is incorrect, or the account is disabled."
    db.update("users", user["id"], {"last_login_at": now_iso()})
    audit.record(db, user["id"], "auth.login")
    return user, None


def start_session(db, user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    cfg = current_app.config
    now = now_utc()
    db.insert("sessions", {
        "token_hash": _token_hash(token),
        "user_id": user_id,
        "csrf_token": secrets.token_urlsafe(24),
        "created_at": to_iso(now),
        "last_seen_at": to_iso(now),
        "expires_at": to_iso(now + timedelta(hours=cfg["SESSION_ABSOLUTE_HOURS"])),
        "ip": request.remote_addr,
        "user_agent": (request.user_agent.string or "")[:200],
    })
    return token


def end_session(db, token: str | None) -> None:
    if token:
        db.execute("UPDATE sessions SET revoked_at = ? WHERE token_hash = ?", (now_iso(), _token_hash(token)))


def revoke_user_sessions(db, user_id: int) -> None:
    db.execute("UPDATE sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL", (now_iso(), user_id))


def load_current_user() -> None:
    g.user = None
    g.session = None
    token = request.cookies.get(COOKIE)
    if not token:
        return
    db = get_db()
    sess = db.one("SELECT * FROM sessions WHERE token_hash = ?", (_token_hash(token),))
    if not sess or sess["revoked_at"]:
        return
    now = now_utc()
    idle_limit = to_iso(now - timedelta(minutes=current_app.config["SESSION_IDLE_MINUTES"]))
    if sess["expires_at"] <= to_iso(now) or sess["last_seen_at"] < idle_limit:
        db.execute("UPDATE sessions SET revoked_at = ? WHERE id = ?", (to_iso(now), sess["id"]))
        return
    user = db.one("SELECT * FROM users WHERE id = ?", (sess["user_id"],))
    if not user or not user["is_active"]:
        db.execute("UPDATE sessions SET revoked_at = ? WHERE id = ?", (to_iso(now), sess["id"]))
        return
    db.execute("UPDATE sessions SET last_seen_at = ? WHERE id = ?", (to_iso(now), sess["id"]))
    g.user = user
    g.session = sess
    g.mfa_pending = mfa_required(user) and not sess["mfa_passed"]


def mfa_required(user: dict) -> bool:
    roles = {r.strip() for r in current_app.config.get("MFA_REQUIRED_ROLES", "").split(",") if r.strip()}
    return user["role"] in roles or bool(user.get("mfa_enabled_at"))


MFA_EXEMPT = {"auth.mfa", "auth.mfa_setup", "auth.logout", "static", "main.healthz"}


def enforce_mfa():
    """Until the second factor is passed, the session can do nothing except complete MFA or sign out."""
    if g.get("mfa_pending") and request.endpoint not in MFA_EXEMPT and not (request.endpoint or "").startswith("public."):
        target = "auth.mfa" if g.user.get("mfa_enabled_at") else "auth.mfa_setup"
        return redirect(url_for(target))
    return None


def can(capability: str, scope_type: str | None = None, scope_id: int | None = None) -> bool:
    if g.get("mfa_pending"):
        return False
    return user_can(get_db(), g.get("user"), capability, scope_type, scope_id)


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.get("user") is None:
            return redirect(url_for("auth.login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def log_denied(capability: str, entity_type: str | None = None, entity_id: int | None = None) -> None:
    db = get_db()
    audit.record(db, g.user["id"] if g.get("user") else None, "access.denied", entity_type, entity_id,
                 {"capability": capability, "path": request.path, "method": request.method}, outcome="denied")


def deny(capability: str, entity_type: str | None = None, entity_id: int | None = None):
    """Log the denied attempt then abort 403 (UAT-08, UAT-16, UAT-33)."""
    log_denied(capability, entity_type, entity_id)
    abort(403)


def require(capability: str):
    def decorator(view):
        @wraps(view)
        @login_required
        def wrapped(*args, **kwargs):
            if not can(capability):
                deny(capability)
            return view(*args, **kwargs)
        return wrapped
    return decorator


def check_csrf() -> None:
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    if request.endpoint in ("auth.login", "static", "main.healthz"):
        return  # login has no session yet; protected by SameSite=Strict cookie + lockout
    if (request.endpoint or "").startswith("public."):
        return  # customer forms: signed form token (quick forms) or the secret link token (personal links)
    sess = g.get("session")
    sent = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
    if not sess or not sent or not secrets.compare_digest(sent, sess["csrf_token"]):
        abort(400, description="Security token missing or expired. Reload the page and try again.")
