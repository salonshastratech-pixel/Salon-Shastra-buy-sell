"""Backups for SQLite deployments and restore verification (FR-024, UAT-26).

Production on PostgreSQL uses deploy/backup.sh + deploy/restore_check.sh (pg_dump/pg_restore).
This module covers the SQLite path and the shared verification logic: integrity check, foreign keys,
schema version and per-table row counts compared with the manifest written at backup time.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def _tables(conn) -> list[str]:
    return [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]


def _counts(conn) -> dict:
    return {t: conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in _tables(conn)}


def _fernet():
    key = os.environ.get("BACKUP_KEY")
    if not key:
        return None
    from cryptography.fernet import Fernet
    return Fernet(key.encode())


def backup_sqlite(db_path: str, out_dir: str) -> dict:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    target = out / f"teamos-{stamp}.db"
    src = sqlite3.connect(db_path)
    dst = sqlite3.connect(target)
    src.backup(dst)  # consistent online snapshot, safe while the app is running
    manifest = {"created_at": stamp, "schema": dst.execute("SELECT max(version) FROM schema_migrations").fetchone()[0],
                "counts": _counts(dst)}
    dst.close()
    src.close()
    data = target.read_bytes()
    f = _fernet()
    if f:
        enc = target.with_suffix(".db.enc")
        enc.write_bytes(f.encrypt(data))
        target.unlink()
        target = enc
    manifest["file"] = target.name
    manifest["sha256"] = hashlib.sha256(target.read_bytes()).hexdigest()
    manifest["encrypted"] = bool(f)
    Path(str(target) + ".json").write_text(json.dumps(manifest, indent=2))
    return manifest


def verify_sqlite_backup(path: str) -> tuple[bool, list[str]]:
    """Restore the backup into a scratch file and prove it is usable."""
    p = Path(path)
    report, ok = [], True
    manifest_path = Path(str(p) + ".json")
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else None
    blob = p.read_bytes()
    if manifest:
        if hashlib.sha256(blob).hexdigest() != manifest["sha256"]:
            return False, ["Checksum mismatch — file corrupted or altered."]
        report.append("Checksum matches manifest.")
    if p.suffix == ".enc":
        f = _fernet()
        if not f:
            return False, ["Backup is encrypted; set BACKUP_KEY to verify."]
        blob = f.decrypt(blob)
        report.append("Decrypted with BACKUP_KEY.")
    with tempfile.TemporaryDirectory() as tmp:
        restored = Path(tmp) / "restored.db"
        restored.write_bytes(blob)
        conn = sqlite3.connect(restored)
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        report.append(f"Integrity check: {integrity}")
        ok &= integrity == "ok"
        fk = conn.execute("PRAGMA foreign_key_check").fetchall()
        report.append(f"Foreign-key violations: {len(fk)}")
        ok &= not fk
        schema = conn.execute("SELECT max(version) FROM schema_migrations").fetchone()[0]
        report.append(f"Schema version: {schema}")
        counts = _counts(conn)
        for key in ("users", "opportunities", "seller_listings", "tasks", "audit_events"):
            report.append(f"  {key}: {counts.get(key, 0)} rows")
        if manifest:
            diff = {t: (manifest['counts'].get(t), counts.get(t)) for t in set(manifest["counts"]) | set(counts)
                    if manifest["counts"].get(t) != counts.get(t)}
            report.append("Row counts match manifest." if not diff else f"Row count differences: {diff}")
            ok &= not diff
            ok &= schema == manifest["schema"]
        users = conn.execute("SELECT COUNT(*) FROM users WHERE is_active = 1").fetchone()[0]
        report.append(f"Active users able to sign in: {users}")
        ok &= users > 0
        conn.close()
    report.append("RESULT: " + ("PASS — backup restores and is consistent." if ok else "FAIL"))
    return ok, report
