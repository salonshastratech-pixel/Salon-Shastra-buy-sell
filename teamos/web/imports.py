import json

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from ..db import get_db
from ..domain import imports as I
from ..domain.errors import ValidationError
from ..security import require
from ..timeutil import parse_local_input

bp = Blueprint("imports", __name__, url_prefix="/imports")


@bp.route("/")
@require("crm.import")
def index():
    db = get_db()
    batches = db.all("SELECT b.*, u.full_name AS uploader FROM import_batches b JOIN users u ON u.id = b.uploaded_by "
                     "ORDER BY b.id DESC LIMIT 50")
    smart = db.all("SELECT i.*, u.full_name AS by_name FROM sheet_imports i JOIN users u ON u.id = i.created_by "
                   "ORDER BY i.id DESC LIMIT 30")
    for s_ in smart:
        s_["counts_d"] = json.loads(s_["counts"]) if s_["counts"] else None
    return render_template("imports/index.html", batches=batches, smart=smart, TARGETS=I.TARGETS, REQUIRED=I.REQUIRED)


@bp.route("/", methods=["POST"])
@require("crm.import")
def upload():
    f = request.files.get("file")
    if not f or not f.filename:
        flash("Choose a .csv or .xlsx file.", "error")
        return redirect(url_for("imports.index"))
    try:
        bid = I.create_batch(get_db(), g.user, f.filename, f.read())
    except ValidationError as e:
        flash(" ".join(e.errors.values()), "error")
        return redirect(url_for("imports.index"))
    return redirect(url_for("imports.preview", batch_id=bid))


@bp.route("/<int:batch_id>")
@require("crm.import")
def preview(batch_id):
    db = get_db()
    batch = db.one("SELECT * FROM import_batches WHERE id = ?", (batch_id,))
    if not batch:
        abort(404)
    headers = json.loads(batch["headers"])
    mapping = json.loads(batch["mapping"])
    show = request.args.get("show", "all")
    rows = db.all("SELECT r.*, c.full_name AS dup_name, o.ref_code FROM import_rows r "
                  "LEFT JOIN contacts c ON c.id = r.duplicate_contact_id "
                  "LEFT JOIN opportunities o ON o.id = r.opportunity_id WHERE batch_id = ? "
                  + ("" if show == "all" else "AND r.status = ? ") + "ORDER BY row_no LIMIT 500",
                  (batch_id,) if show == "all" else (batch_id, show))
    for r in rows:
        r["raw_d"] = json.loads(r["raw"])
        r["mapped"] = I.map_row(r["raw_d"], mapping)
        r["errors_d"] = json.loads(r["errors"]) if r["errors"] else {}
    users = db.all("SELECT id, full_name FROM users WHERE is_active = 1 AND role <> 'finance' ORDER BY full_name")
    return render_template("imports/preview.html", batch=batch, headers=headers, mapping=mapping, rows=rows,
                           summary=I.summary(db, batch_id), users=users, show=show, TARGETS=I.TARGETS,
                           REQUIRED=I.REQUIRED, ALLOWED=I.ALLOWED_DECISIONS)


@bp.route("/<int:batch_id>/mapping", methods=["POST"])
@require("crm.import")
def mapping(batch_id):
    db = get_db()
    batch = db.one("SELECT headers FROM import_batches WHERE id = ?", (batch_id,))
    if not batch:
        abort(404)
    headers = json.loads(batch["headers"])
    new = {h: request.form.get(f"map_{i}") for i, h in enumerate(headers) if request.form.get(f"map_{i}")}
    try:
        I.set_mapping(db, g.user, batch_id, new, int(request.form.get("owner_id") or 0) or None)
        flash("Mapping updated and every row re-checked.", "ok")
    except ValidationError as e:
        flash(" ".join(e.errors.values()), "error")
    return redirect(url_for("imports.preview", batch_id=batch_id))


@bp.route("/<int:batch_id>/commit", methods=["POST"])
@require("crm.import")
def commit(batch_id):
    db = get_db()
    decisions = {int(k.split("_", 1)[1]): v for k, v in request.form.items() if k.startswith("decision_")}
    try:
        counts = I.commit(db, g.user, batch_id, decisions, parse_local_input(request.form.get("first_task_due")))
        flash(f"Import committed: {counts['created']} created, {counts['skipped']} skipped"
              + (f", {counts['changed_since_preview']} not created because they became duplicates since preview"
                 if counts["changed_since_preview"] else "") + ".", "ok")
    except ValidationError as e:
        flash(" ".join(e.errors.values()), "error")
    return redirect(url_for("imports.preview", batch_id=batch_id))


@bp.route("/<int:batch_id>/discard", methods=["POST"])
@require("crm.import")
def discard(batch_id):
    try:
        I.discard(get_db(), g.user, batch_id)
        flash("Import discarded. Nothing was written to the CRM.", "ok")
    except ValidationError as e:
        flash(" ".join(e.errors.values()), "error")
    return redirect(url_for("imports.index"))


# -- Smart import: any workbook, sellers and buyers, synced ------------------------------------------------------
GSHEET = __import__("re").compile(r"docs\.google\.com/spreadsheets/d/([A-Za-z0-9_\-]{20,})")


def _fetch_google_sheet(url: str) -> tuple[str, bytes]:
    """Download a Google Sheet shared as 'Anyone with the link can view' as Excel (all tabs)."""
    import urllib.error
    import urllib.request
    m = GSHEET.search(url or "")
    if not m:
        raise ValidationError({"link": "Paste a Google Sheets link (docs.google.com/spreadsheets/…)."})
    export = f"https://docs.google.com/spreadsheets/d/{m.group(1)}/export?format=xlsx"
    try:
        with urllib.request.urlopen(urllib.request.Request(export, headers={"User-Agent": "TeamOS-import"}),
                                    timeout=30) as r:
            data, ctype = r.read(8 * 1024 * 1024 + 1), r.headers.get("Content-Type", "")
    except (urllib.error.URLError, TimeoutError) as e:
        raise ValidationError({"link": f"Could not download the sheet ({getattr(e, 'code', '') or e}). "
                                       "Share it as 'Anyone with the link can view', or download it as Excel "
                                       "(File → Download → .xlsx) and upload the file."})
    if "spreadsheetml" not in ctype and not data.startswith(b"PK"):
        raise ValidationError({"link": "Google asked for a sign-in, so the sheet is private. Share it as 'Anyone with "
                                       "the link can view', or download it as Excel and upload the file."})
    return f"google-sheet-{m.group(1)[:8]}.xlsx", data


@bp.route("/smart", methods=["POST"])
@require("crm.import")
def smart_upload():
    from ..domain import sheet_sync as SS
    db = get_db()
    try:
        f = request.files.get("file")
        if f and f.filename:
            name, data = f.filename, f.read()
        elif (request.form.get("link") or "").strip():
            name, data = _fetch_google_sheet(request.form["link"].strip())
        else:
            raise ValidationError({"file": "Choose an Excel/CSV file or paste a Google Sheets link."})
        iid = SS.create_import(db, g.user, name, data)
    except ValidationError as e:
        flash(" ".join(str(v) for v in e.errors.values()), "error")
        return redirect(url_for("imports.index"))
    return redirect(url_for("imports.smart_preview", import_id=iid))


@bp.route("/smart/<int:import_id>")
@require("crm.import")
def smart_preview(import_id):
    from ..domain import sheet_sync as SS
    db = get_db()
    imp = db.one("SELECT i.*, u.full_name AS by_name FROM sheet_imports i JOIN users u ON u.id = i.created_by "
                 "WHERE i.id = ?", (import_id,))
    if not imp:
        abort(404)
    show = request.args.get("show", "create")
    q = "SELECT r.*, o.ref_code FROM sheet_import_rows r LEFT JOIN opportunities o ON o.id = r.opportunity_id " \
        "WHERE r.import_id = ?"
    args = [import_id]
    if show in ("create", "update", "merged", "skip"):
        q += " AND r.action = ?"
        args.append(show)
    rows = db.all(q + " ORDER BY r.kind, r.id LIMIT 400", args)
    for r in rows:
        d = json.loads(r["data"] or "{}")
        r["u"] = d.get("understood", {})
        r["canon"] = d.get("canon", {})
        r["notes_l"] = json.loads(r["notes"] or "[]")
    return render_template("imports/smart_preview.html", imp=imp, tabs=json.loads(imp["tabs"]), rows=rows, show=show,
                           s=SS.summary(db, import_id), counts=json.loads(imp["counts"]) if imp["counts"] else None)


@bp.route("/smart/<int:import_id>/commit", methods=["POST"])
@require("crm.import")
def smart_commit(import_id):
    from ..domain import sheet_sync as SS
    try:
        c = SS.commit(get_db(), g.user, import_id)
        flash(f"Imported: {c['created_seller']} new sellers, {c['created_buyer']} new buyers, {c['updated']} existing "
              f"records updated, {c['merged']} repeats merged, {c['skipped']} rows left out"
              + (f", {c['failed']} could not be saved (see the list)" if c["failed"] else "") + ".", "ok")
    except ValidationError as e:
        flash(" ".join(str(v) for v in e.errors.values()), "error")
    return redirect(url_for("imports.smart_preview", import_id=import_id))


@bp.route("/smart/<int:import_id>/discard", methods=["POST"])
@require("crm.import")
def smart_discard(import_id):
    from ..domain import sheet_sync as SS
    SS.discard(get_db(), g.user, import_id)
    flash("Import discarded. Nothing was written to sellers or buyers.", "ok")
    return redirect(url_for("imports.index"))
