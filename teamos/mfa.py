"""Time-based one-time passwords (RFC 6238) for privileged users — standard library only.

Works with Google Authenticator, Microsoft Authenticator, Authy etc. (SHA-1, 6 digits, 30 s).
Replay of the same code is refused; ±1 step clock drift is accepted; recovery codes are single-use.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

from werkzeug.security import check_password_hash, generate_password_hash

from .timeutil import now_iso

STEP = 30
DIGITS = 6


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _code(secret: str, step: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10 ** DIGITS).zfill(DIGITS)


def current_code(secret: str, at: float | None = None) -> str:
    return _code(secret, int((at or time.time()) // STEP))


def verify(secret: str, code: str, last_step: int | None, at: float | None = None) -> int | None:
    """Return the matched time step (to store as last_step) or None."""
    code = "".join(ch for ch in (code or "") if ch.isdigit())
    if len(code) != DIGITS:
        return None
    now_step = int((at or time.time()) // STEP)
    for step in (now_step - 1, now_step, now_step + 1):
        if last_step is not None and step <= last_step:
            continue
        if hmac.compare_digest(_code(secret, step), code):
            return step
    return None


def provisioning_uri(secret: str, email: str) -> str:
    return (f"otpauth://totp/{quote('Salon Shastra TeamOS')}:{quote(email)}?secret={secret}"
            f"&issuer={quote('Salon Shastra TeamOS')}&digits={DIGITS}&period={STEP}")


def grouped(secret: str) -> str:
    return " ".join(secret[i:i + 4] for i in range(0, len(secret), 4))


def issue_recovery_codes(db, user_id: int, n: int = 8) -> list[str]:
    db.execute("UPDATE mfa_recovery_codes SET used_at = ? WHERE user_id = ? AND used_at IS NULL", (now_iso(), user_id))
    codes = [f"{secrets.token_hex(2)}-{secrets.token_hex(2)}-{secrets.token_hex(2)}" for _ in range(n)]
    for c in codes:
        db.insert("mfa_recovery_codes", {"user_id": user_id, "code_hash": generate_password_hash(c, method="scrypt"),
                                         "created_at": now_iso()})
    return codes


def use_recovery_code(db, user_id: int, code: str) -> bool:
    code = (code or "").strip().lower()
    for row in db.all("SELECT id, code_hash FROM mfa_recovery_codes WHERE user_id = ? AND used_at IS NULL", (user_id,)):
        if check_password_hash(row["code_hash"], code):
            db.execute("UPDATE mfa_recovery_codes SET used_at = ? WHERE id = ?", (now_iso(), row["id"]))
            return True
    return False
