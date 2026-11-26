import json

from flask import Blueprint, abort, current_app, flash, g, make_response, redirect, render_template, request, url_for

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
    from ..domain import connected_sheets as CS
    return render_template("imports/index.html", batches=batches, smart=smart, TARGETS=I.TARGETS, REQUIRED=I.REQUIRED,
                           sheets=CS.listing(get_db()))


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
        o = request.form.get("owner_id") or ""
        I.set_mapping(db, g.user, batch_id, new, "auto" if o == "auto" else (int(o) if o.isdigit() else None))
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
    from ..domain import connected_sheets as CS
    return CS.fetch(url)


@bp.route("/sheets", methods=["POST"])
@require("crm.import")
def sheet_connect():
    from ..domain import connected_sheets as CS
    db = get_db()
    try:
        sid = CS.add(db, g.user, request.form.get("name"), request.form.get("link"), request.form.get("auto") == "1")
        res = CS.check(db, db.one("SELECT * FROM connected_sheets WHERE id = ?", (sid,)), g.user)
        flash("Sheet connected. First check: " + res, "ok")
    except ValidationError as e:
        flash(" ".join(str(v) for v in e.errors.values()), "error")
    return redirect(url_for("imports.index") + "#sheets")


@bp.route("/sheets/<int:sheet_id>/check", methods=["POST"])
@require("crm.import")
def sheet_check(sheet_id):
    from ..domain import connected_sheets as CS
    db = get_db()
    s = db.one("SELECT * FROM connected_sheets WHERE id = ? AND active = 1", (sheet_id,))
    if not s:
        abort(404)
    flash(f"{s['name']}: " + CS.check(db, s, g.user), "ok")
    return redirect(url_for("imports.index") + "#sheets")


@bp.route("/sheets/<int:sheet_id>/auto", methods=["POST"])
@require("crm.import")
def sheet_auto(sheet_id):
    from ..domain import connected_sheets as CS
    on = request.form.get("on") == "1"
    CS.set_auto(get_db(), g.user, sheet_id, on)
    flash("Auto-import is on: new leads come straight in, shared between the team in turn, first call within "
          "10 minutes." if on else "Auto-import is off: new people wait as a preview for you to approve.", "ok")
    return redirect(url_for("imports.index") + "#sheets")


@bp.route("/sheets/<int:sheet_id>/remove", methods=["POST"])
@require("crm.import")
def sheet_remove(sheet_id):
    from ..domain import connected_sheets as CS
    CS.remove(get_db(), g.user, sheet_id)
    flash("Sheet disconnected. Records already imported stay as they are.", "ok")
    return redirect(url_for("imports.index") + "#sheets")


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
    from ..domain import remarks as RM
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
    cache = RM._learned(db)
    for r in rows:
        d = json.loads(r["data"] or "{}")
        r["u"] = d.get("understood", {})
        r["canon"] = d.get("canon", {})
        r["notes_l"] = json.loads(r["notes"] or "[]")
        rt = SS.remark_text(r["notes_l"])
        r["rm"] = RM.read(rt, db, learned_cache=cache) if rt else None
    counts = json.loads(imp["counts"]) if imp["counts"] else None
    importing = bool(imp["status"] == "preview" and counts and counts.get("importing"))
    failed = counts.get("error") if imp["status"] == "preview" and counts else None
    resp = make_response(render_template("imports/smart_preview.html", imp=imp, tabs=json.loads(imp["tabs"]),
                                         rows=rows, show=show, s=SS.summary(db, import_id),
                                         counts=counts if imp["status"] == "committed" else None, importing=importing,
                                         failed=failed, prog=SS.progress(db, import_id)))
    if importing:
        resp.headers["Refresh"] = "4"
    return resp


@bp.route("/smart/<int:import_id>/commit", methods=["POST"])
@require("crm.import")
def smart_commit(import_id):
    from ..domain import sheet_sync as SS
    from ..permissions import user_can as _can
    db = get_db()
    if not _can(db, g.user, "crm.import"):
        abort(403)
    if not SS.claim(db, import_id):
        flash("This import is already being saved, or was already saved.", "error")
        return redirect(url_for("imports.smart_preview", import_id=import_id))
    SS.commit_in_background(current_app._get_current_object(), import_id, g.user["id"])
    flash("Importing now — this page updates by itself. You can keep working meanwhile.", "ok")
    return redirect(url_for("imports.smart_preview", import_id=import_id))


@bp.route("/smart/<int:import_id>/discard", methods=["POST"])
@require("crm.import")
def smart_discard(import_id):
    from ..domain import sheet_sync as SS
    SS.discard(get_db(), g.user, import_id)
    flash("Import discarded. Nothing was written to sellers or buyers.", "ok")
    return redirect(url_for("imports.index"))


# -- Remark dictionary: what TeamOS understands from the team's remarks, and teaching it new phrases --------------
@bp.route("/remarks", methods=["GET", "POST"])
@require("crm.view")
def remarks():
    from ..domain import remarks as RM
    from ..domain.errors import PermissionDenied
    db = get_db()
    if request.method == "POST":
        try:
            if request.form.get("forget"):
                RM.forget(db, g.user, int(request.form["forget"]))
                flash("Phrase removed.", "ok")
            else:
                RM.teach(db, g.user, request.form.get("phrase"), request.form.get("meaning"))
                flash("Learned. TeamOS will understand this phrase in every import from now on.", "ok")
        except PermissionDenied:
            abort(403)
        except ValidationError as e:
            flash(" ".join(e.errors.values()) if isinstance(e.errors, dict) else str(e.errors), "error")
        return redirect(url_for("imports.remarks", t=request.form.get("t") or None))
    t = (request.args.get("t") or "").strip()[:500]
    from ..permissions import user_can
    return render_template("imports/remarks.html", t=t, res=RM.read(t, db) if t else None, RM=RM,
                           learned=RM.learned(db), unknown=RM.not_understood(db),
                           can_teach=user_can(db, g.user, "crm.import") or user_can(db, g.user, "crm.assign"))
