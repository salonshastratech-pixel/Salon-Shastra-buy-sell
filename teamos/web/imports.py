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
    return render_template("imports/index.html", batches=batches, TARGETS=I.TARGETS, REQUIRED=I.REQUIRED)


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
