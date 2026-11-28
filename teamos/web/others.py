"""Others: job and academy inquiries (people who call for a job, or for training courses)."""
from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from ..db import get_db
from ..domain import inquiries as Q
from ..domain.errors import PermissionDenied, ValidationError
from ..security import require

bp = Blueprint("others", __name__, url_prefix="/others")


def _links():
    """The two forms anyone can fill: shared in ads, WhatsApp groups, the website, or sent to one person."""
    from .. import messaging
    base = messaging.public_base(request)
    return {"academy": f"{base}/f/academy", "job": f"{base}/f/job"}


def _team(db):
    return db.all("SELECT id, full_name FROM users WHERE is_active = 1 AND role <> 'finance' ORDER BY full_name")


@bp.route("/")
@require("crm.view")
def index():
    db = get_db()
    rows, total = Q.search(db, request.args)
    return render_template("others/index.html", rows=rows, total=total, f=request.args, Q=Q, counts=Q.counts(db),
                           users=_team(db), links=_links())


@bp.route("/new", methods=["GET", "POST"])
@require("crm.create")
def new():
    db = get_db()
    kind = request.values.get("kind") if request.values.get("kind") in Q.KINDS else "job"
    errors, form = {}, request.form
    if request.method == "POST":
        try:
            iid = Q.create(db, g.user, kind, request.form)
        except ValidationError as e:
            errors = e.errors if isinstance(e.errors, dict) else {"form": str(e.errors)}
        except PermissionDenied:
            abort(403)
        else:
            flash(f"{Q.KINDS[kind]} saved.", "ok")
            if request.form.get("another"):
                return redirect(url_for("others.new", kind=kind))
            return redirect(url_for("others.detail", iid=iid))
    answers = Q._answers_from(kind, request.form) if request.method == "POST" else {}
    return render_template("others/new.html", kind=kind, errors=errors, form=form, answers=answers, Q=Q,
                           users=_team(db))


@bp.route("/<int:iid>", methods=["GET", "POST"])
@require("crm.view")
def detail(iid):
    db = get_db()
    inq = Q.load(db, iid)
    if not inq:
        abort(404)
    errors = {}
    if request.method == "POST":
        try:
            if request.form.get("action") == "edit":
                Q.update(db, g.user, inq, request.form)
                flash("Details saved.", "ok")
            else:
                Q.log(db, g.user, inq, request.form)
                flash("Saved.", "ok")
            return redirect(url_for("others.detail", iid=iid))
        except ValidationError as e:
            errors = e.errors if isinstance(e.errors, dict) else {"form": str(e.errors)}
            flash(" ".join(str(v) for v in errors.values()), "error")
        except PermissionDenied:
            abort(403)
    return render_template("others/detail.html", inq=inq, Q=Q, errors=errors, history=Q.history(db, iid),
                           tasks=Q.open_tasks(db, iid), users=_team(db), edit=request.args.get("edit"), links=_links())


@bp.route("/<int:iid>/send-form", methods=["POST"])
@require("crm.log_activity")
def send_form(iid):
    """'Send the form' on an inquiry: WhatsApp opens to this person with the form link, ready to send. What they fill
    comes straight into this inquiry (same mobile number)."""
    from urllib.parse import quote

    from ..domain import tasks as T
    db = get_db()
    inq = Q.load(db, iid)
    if not inq:
        abort(404)
    url = _links()[inq["kind"]]
    text = Q.form_message(inq, url, g.user["full_name"].split()[0])
    T.add_activity(db, g.user["id"], "inquiry", iid, "note",
                   f"Sent the {Q.PUBLIC_META[inq['kind']]['label']} form on WhatsApp")
    digits = "".join(ch for ch in (inq.get("alt_phone") or inq.get("phone_normalized") or "") if ch.isdigit())
    return redirect(f"https://wa.me/{digits}?text={quote(text)}" if digits else f"https://wa.me/?text={quote(text)}")
