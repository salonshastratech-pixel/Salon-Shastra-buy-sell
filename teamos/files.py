"""Encrypted document storage (PRD §13, §14.1, FR-013).

Files are written outside the web root under a random name, encrypted with Fernet (AES-128-CBC + HMAC)
using FILES_KEY from the environment. There is no static URL: the only way to read a file is the
authorised download route, which checks permissions on every request.

If FILES_KEY is not configured, uploads are refused rather than stored in plain text.
"""
from __future__ import annotations

import hashlib
import os
import secrets
from pathlib import Path

from flask import current_app

MAX_BYTES = 5 * 1024 * 1024
# Only formats we can recognise by their first bytes; the extension and browser-supplied type are not trusted.
SIGNATURES = {
    "application/pdf": (b"%PDF-",),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/jpeg": (b"\xff\xd8\xff",),
}
EXTENSIONS = {"application/pdf": ".pdf", "image/png": ".png", "image/jpeg": ".jpg"}


class UploadRejected(Exception):
    pass


def _fernet():
    key = current_app.config.get("FILES_KEY") or os.environ.get("FILES_KEY")
    if not key:
        return None
    from cryptography.fernet import Fernet
    return Fernet(key.encode() if isinstance(key, str) else key)


def uploads_enabled() -> bool:
    return _fernet() is not None


def storage_dir() -> Path:
    d = Path(current_app.config.get("UPLOAD_DIR") or os.path.join(current_app.instance_path, "uploads"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def sniff(data: bytes) -> str | None:
    for ctype, sigs in SIGNATURES.items():
        if any(data.startswith(s) for s in sigs):
            return ctype
    return None


def store(data: bytes, original_name: str) -> dict:
    """Validate and encrypt. Returns metadata for the documents table."""
    f = _fernet()
    if f is None:
        raise UploadRejected("Secure uploads are not configured on this server (FILES_KEY missing).")
    if not data:
        raise UploadRejected("The file is empty.")
    if len(data) > MAX_BYTES:
        raise UploadRejected("Files must be 5 MB or smaller.")
    ctype = sniff(data)
    if not ctype:
        raise UploadRejected("Only PDF, PNG or JPG files can be uploaded.")
    stored = secrets.token_hex(16) + ".bin"
    (storage_dir() / stored).write_bytes(f.encrypt(data))
    name = os.path.basename(original_name or "document")[:120] or "document"
    return {"stored_name": stored, "content_type": ctype, "size_bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(), "original_name": name, "encrypted": 1}


def read(stored_name: str) -> bytes:
    f = _fernet()
    if f is None:
        raise UploadRejected("FILES_KEY missing — cannot decrypt.")
    if "/" in stored_name or "\\" in stored_name or ".." in stored_name:
        raise UploadRejected("Invalid file reference.")
    return f.decrypt((storage_dir() / stored_name).read_bytes())
