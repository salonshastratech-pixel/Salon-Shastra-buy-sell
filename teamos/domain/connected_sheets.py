"""Connected Google Sheets: checked every hour; new people arrive as an import preview the team approves.

Works with sheets shared as "Anyone with the link can view" (Google's own export link: no Google account or API key
is stored in TeamOS). Nothing is written to sellers/buyers until someone presses Import now on the preview.
"""
from __future__ import annotations

import re
import urllib.error
import urllib.request

from .. import audit
from ..permissions import user_can
from ..timeutil import now_iso
from . import sheet_sync as SS
from . import smart_import as SI
from . import tasks as T
from .errors import PermissionDenied, ValidationError

GSHEET = re.compile(r"docs\.google\.com/spreadsheets/d/([A-Za-z0-9_\-]{20,})")


def fetch(url: str) -> tuple[str, bytes]:
    """Download a Google Sheet shared as 'Anyone with the link can view' as Excel (all tabs)."""
    m = GSHEET.search(url or "")
    if not m:
        raise ValidationError({"link": "Paste a Google Sheets link (docs.google.com/spreadsheets/...)."})
    export = f"https://docs.google.com/spreadsheets/d/{m.group(1)}/export?format=xlsx"
    try:
        with urllib.request.urlopen(urllib.request.Request(export, headers={"User-Agent": "TeamOS-import"}),
                                    timeout=30) as r:
            data, ctype = r.read(8 * 1024 * 1024 + 1), r.headers.get("Content-Type", "")
    except (urllib.error.URLError, TimeoutError) as e:
        raise ValidationError({"link": f"Could not download the sheet ({getattr(e, 'code', '') or e}). "
                                       "Share it as 'Anyone with the link can view', or download it as Excel "
                                       "(File > Download > .xlsx) and upload the file."})
    if "spreadsheetml" not in ctype and not data.startswith(b"PK"):
        raise ValidationError({"link": "Google asked for a sign-in, so the sheet is private. Share it as 'Anyone with "
                                       "the link can view', or download it as Excel and upload the file."})
    return f"google-sheet-{m.group(1)[:8]}.xlsx", data


def _need(db, actor):
    if not user_can(db, actor, "crm.import"):
        raise PermissionDenied("crm.import")


def add(db, actor, name: str, url: str) -> int:
    _need(db, actor)
    m = GSHEET.search(url or "")
    if not m:
        raise ValidationError({"link": "Paste a Google Sheets link (docs.google.com/spreadsheets/...)."})
    name = (name or "").strip()[:80] or "Google Sheet"
    if db.scalar("SELECT id FROM connected_sheets WHERE sheet_key = ? AND active = 1", (m.group(1),)):
        raise ValidationError({"link": "This sheet is already connected."})
    with db:
        old = db.scalar("SELECT id FROM connected_sheets WHERE sheet_key = ?", (m.group(1),))
        if old:  # reconnecting a sheet that was removed earlier
            db.update("connected_sheets", old, {"active": 1, "name": name, "url": url.strip()[:500], "last_sha": None})
            sid = old
        else:
            sid = db.insert("connected_sheets", {"name": name, "url": url.strip()[:500], "sheet_key": m.group(1),
                                                 "active": 1, "created_by": actor["id"], "created_at": now_iso()})
        audit.record(db, actor["id"], "sheet.connect", "connected_sheet", sid, {"name": name})
    return sid


def remove(db, actor, sheet_id: int) -> None:
    _need(db, actor)
    with db:
        db.execute("UPDATE connected_sheets SET active = 0 WHERE id = ?", (sheet_id,))
        audit.record(db, actor["id"], "sheet.disconnect", "connected_sheet", sheet_id)


def listing(db) -> list[dict]:
    return db.all("SELECT s.*, u.full_name AS by_name, i.status AS import_status FROM connected_sheets s "
                  "JOIN users u ON u.id = s.created_by LEFT JOIN sheet_imports i ON i.id = s.last_import_id "
                  "WHERE s.active = 1 ORDER BY s.name")


def check(db, sheet: dict, actor: dict | None = None, fetcher=None) -> str:
    """Look at one sheet now. Returns what happened, in plain words (also saved on the sheet)."""
    actor = actor or db.one("SELECT * FROM users WHERE id = ?", (sheet["created_by"],))
    if not actor or not user_can(db, actor, "crm.import"):
        actor = db.one("SELECT * FROM users WHERE is_active = 1 AND role = 'super_admin' ORDER BY id LIMIT 1")
    import_id = None
    try:
        name, data = (fetcher or fetch)(sheet["url"])
        sha = SI.file_sha(data)
        waiting = sheet["last_import_id"] and db.scalar(
            "SELECT status FROM sheet_imports WHERE id = ?", (sheet["last_import_id"],)) == "preview"
        if sha == sheet["last_sha"]:
            result = "No change since the last check."
        else:
            p = SS.plan(db, name, data)
            new = sum(1 for r in p["rows"] if r["action"] == "create")
            if not new:
                result = "Checked: no new people (everyone is already in TeamOS)."
            elif waiting:
                result = (f"{new} new people. The earlier preview #{sheet['last_import_id']} is still waiting: "
                          "import or discard it, and the next check prepares a fresh one.")
                sha = sheet["last_sha"]  # look again next time
            else:
                import_id = SS.create_import(db, actor, f"{sheet['name']} - {name}", data)
                result = f"{new} new people: preview #{import_id} is ready to review and import."
                admins = [r["id"] for r in db.all("SELECT id FROM users WHERE is_active = 1 AND role = 'super_admin'")]
                for uid in {sheet["created_by"], *admins}:
                    T.notify(db, uid, "sheet_new_rows", f"{sheet['name']}: {new} new people to import",
                             f"/imports/smart/{import_id}")
        values = {"last_checked_at": now_iso(), "last_sha": sha, "last_result": result}
    except ValidationError as e:
        result = " ".join(str(v) for v in e.errors.values())
        values = {"last_checked_at": now_iso(), "last_result": result}
    if import_id:
        values["last_import_id"] = import_id
    with db:
        db.update("connected_sheets", sheet["id"], values)
    return result


def check_all(db, fetcher=None) -> str:
    out = [f"{s['name']}: {check(db, s, fetcher=fetcher)}"
           for s in db.all("SELECT * FROM connected_sheets WHERE active = 1 ORDER BY id")]
    return "; ".join(out) or "no connected sheets"
