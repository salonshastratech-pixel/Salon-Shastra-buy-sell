"""Connected Google Sheets — and Excel / CSV files kept in Google Drive: checked every 10 minutes. New people arrive as an import preview the team approves —
or, for sheets marked "Auto-import" (Meta ad leads), straight in: shared between the team in turn with a first call due
within 10 minutes.

Works with sheets shared as "Anyone with the link can view" (Google's own export link: no Google account or API key
is stored in TeamOS). Without Auto-import, nothing is written to sellers/buyers until someone presses Import now on the preview.
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
# Excel / CSV files kept in Google Drive (uploaded as .xlsx, not converted to Google Sheets)
DRIVE = re.compile(r"drive\.google\.com/(?:file/d/|open\?(?:[^#\s]*&)?id=|uc\?(?:[^#\s]*&)?id=)([A-Za-z0-9_\-]{20,})")
LINK_HELP = ("Paste a Google Sheets link (docs.google.com/spreadsheets/…) or a Google Drive file link "
             "(drive.google.com/file/d/…) of an Excel or CSV file.")
MAX_BYTES = 8 * 1024 * 1024


def sheet_key(url: str | None) -> str | None:
    """The Google file id inside a Sheets or Drive link (the same file always gives the same key)."""
    m = GSHEET.search(url or "") or DRIVE.search(url or "")
    return m.group(1) if m else None


def _download(url: str) -> tuple[bytes, str]:
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "TeamOS-import"}),
                                timeout=75) as r:   # Google often takes 10-30 s to build the file
        return r.read(MAX_BYTES + 1), r.headers.get("Content-Type", "")


def fetch(url: str) -> tuple[str, bytes]:
    """Download a shared sheet: a Google Sheet (exported as Excel, all tabs), or an Excel / CSV file kept in Google
    Drive. The file must be shared as 'Anyone with the link can view'."""
    key = sheet_key(url)
    if not key:
        raise ValidationError({"link": LINK_HELP})
    drive = [f"https://drive.usercontent.google.com/download?id={key}&export=download&confirm=t",
             f"https://drive.google.com/uc?export=download&id={key}"]
    # a docs.google.com link may also be an Excel file opened in Google's editor: then the plain download works
    tries = ([f"https://docs.google.com/spreadsheets/d/{key}/export?format=xlsx"] if GSHEET.search(url or "") else []) + drive
    last = None
    for u in tries:
        try:
            data, ctype = _download(u)
        except (urllib.error.URLError, TimeoutError) as e:
            last = f"could not download ({getattr(e, 'code', '') or e})"
            continue
        if len(data) > MAX_BYTES:
            raise ValidationError({"link": "The file is larger than 8 MB. Split it, or remove old tabs, and try again."})
        if data.startswith(b"PK"):                                  # .xlsx (a zip)
            return f"google-sheet-{key[:8]}.xlsx", data
        if data.startswith(b"\xd0\xcf\x11\xe0"):                    # old .xls
            raise ValidationError({"link": "This is an old Excel file (.xls). In Google Drive open it, then "
                                           "File > Save as Google Sheets (or save it as .xlsx) and use that link."})
        head = data[:600].lstrip().lower()
        if "text/csv" in ctype or ("text/plain" in ctype and b"<html" not in head):
            return f"google-sheet-{key[:8]}.csv", data
        last = "Google asked for a sign-in, so the file is private"
    raise ValidationError({"link": f"{last or 'Could not download the file'}. Share it as 'Anyone with the link can "
                                   "view' (Share > General access), or download it and upload the file here."})


def _need(db, actor):
    if not user_can(db, actor, "crm.import"):
        raise PermissionDenied("crm.import")


def add(db, actor, name: str, url: str, auto: bool = False) -> int:
    _need(db, actor)
    key = sheet_key(url)
    if not key:
        raise ValidationError({"link": LINK_HELP})
    name = (name or "").strip()[:80] or "Google Sheet"
    if db.scalar("SELECT id FROM connected_sheets WHERE sheet_key = ? AND active = 1", (key,)):
        raise ValidationError({"link": "This sheet is already connected."})
    with db:
        old = db.scalar("SELECT id FROM connected_sheets WHERE sheet_key = ?", (key,))
        if old:  # reconnecting a sheet that was removed earlier
            db.update("connected_sheets", old, {"active": 1, "name": name, "url": url.strip()[:500], "last_sha": None,
                                                "auto_import": 1 if auto else 0})
            sid = old
        else:
            sid = db.insert("connected_sheets", {"name": name, "url": url.strip()[:500], "sheet_key": key,
                                                 "active": 1, "auto_import": 1 if auto else 0,
                                                 "created_by": actor["id"], "created_at": now_iso()})
        audit.record(db, actor["id"], "sheet.connect", "connected_sheet", sid, {"name": name})
    return sid


def set_auto(db, actor, sheet_id: int, on: bool) -> None:
    _need(db, actor)
    with db:
        db.execute("UPDATE connected_sheets SET auto_import = ? WHERE id = ?", (1 if on else 0, sheet_id))
        audit.record(db, actor["id"], "sheet.auto_import", "connected_sheet", sheet_id, {"on": on})


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
            p = SS.plan_many(db, [(name, data, chosen(sheet))])
            new = sum(1 for r in p["rows"] if r["action"] == "create")
            if not new:
                result = "Checked: no new people (everyone is already in TeamOS)."
            elif sheet.get("auto_import"):
                import_id = SS.create_import(db, actor, f"{sheet['name']} - {name}", data, chosen(sheet))
                counts = SS.commit(db, actor, import_id, live=new <= SS.LIVE_BATCH_MAX)
                made = counts["created_seller"] + counts["created_buyer"]
                result = (f"{made} new lead(s) added and shared out ({counts['created_seller']} salon owner, "
                          f"{counts['created_buyer']} investor)" + (f", {counts['failed']} could not be added — see "
                                                                    f"import #{import_id}" if counts["failed"] else "")
                          + ("." if new <= SS.LIVE_BATCH_MAX else " — first calls spread over the coming days."))
            elif waiting:
                result = (f"{new} new people. The earlier preview #{sheet['last_import_id']} is still waiting: "
                          "import or discard it, and the next check prepares a fresh one.")
                sha = sheet["last_sha"]  # look again next time
            else:
                import_id = SS.create_import(db, actor, f"{sheet['name']} - {name}", data, chosen(sheet))
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



def chosen(sheet: dict) -> list[str] | None:
    """The tabs picked for this sheet (None = every lead tab)."""
    import json
    try:
        tabs = json.loads(sheet.get("tabs") or "null")
    except ValueError:
        return None
    return tabs or None


def tab_list(url: str, fetcher=None) -> list[dict]:
    """Every tab inside the sheet: name, rows, and what it looks like (salon owners / investors / helper …)."""
    name, data = (fetcher or fetch)(url)
    _book, info, raw = SS._tab_pass(name, data)
    SS._resolve_unsure_tabs(info, raw)
    return [{"name": t["name"], "rows": t["rows"], "kind": t["kind"], "why": t["why"]} for t in info]


def set_tabs(db, actor, sheet_id: int, tabs: list[str]) -> None:
    import json
    _need(db, actor)
    with db:
        db.execute("UPDATE connected_sheets SET tabs = ?, last_sha = NULL WHERE id = ?",   # re-check with the new choice
                   (json.dumps(tabs) if tabs else None, sheet_id))
        audit.record(db, actor["id"], "sheet.tabs", "connected_sheet", sheet_id, {"tabs": tabs})
