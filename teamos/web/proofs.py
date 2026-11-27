"""Daily work proof (screenshots of calls & WhatsApp) and the quick status page the hourly reminder opens."""
from datetime import datetime, timedelta

from flask import Blueprint, Response, abort, flash, g, redirect, render_template, request, url_for

from ..db import get_db
from ..domain import availability as AV
from ..domain import work_proof as W
from ..domain.errors import PermissionDenied, ValidationError
from ..files import UploadRejected
from ..security import login_required, require

bp = Blueprint("work", __name__, url_prefix="/work")


def _err(e) -> str:
    return " ".join(str(v) for v in e.errors.values()) if isinstance(e.errors, dict) else str(e.errors)


@bp.route("/", methods=["GET", "POST"])
@require("crm.log_activity")
def mine():
    db = get_db()
    if request.method == "POST":
        files = [(f.read(), f.filename or "") for f in request.files.getlist("files")]
        try:
            n = W.save(db, g.user, request.form.get("kind") or "", files, request.form.get("note"))
            flash(f"Uploaded {n} screenshot{'s' if n != 1 else ''}. Thank you!", "ok")
        except ValidationError as e:
            flash(_err(e), "error")
        except PermissionDenied:
            abort(403)
        return redirect(url_for("work.mine"))
    day = W.today_ist()
    return render_template("work/mine.html", items=W.mine(db, g.user["id"], day), counts=W.day_counts(db, g.user["id"], day),
                           week=W.week_summary(db, g.user["id"], day), W=W, day=day)


@bp.route("/<int:pid>/delete", methods=["POST"])
@require("crm.log_activity")
def remove(pid):
    try:
        W.delete(get_db(), g.user, pid)
        flash("Screenshot removed.", "ok")
    except PermissionDenied:
        flash("You can remove only your own screenshots, on the same day.", "error")
    return redirect(url_for("work.mine"))


@bp.route("/team")
@require("users.manage")
def team():
    db = get_db()
    day = request.args.get("day") or W.today_ist()
    try:
        d = datetime.strptime(day, "%Y-%m-%d")
    except ValueError:
        day, d = W.today_ist(), datetime.strptime(W.today_ist(), "%Y-%m-%d")
    return render_template("work/team.html", rows=W.team_day(db, day), day=day, W=W,
                           prev=(d - timedelta(days=1)).strftime("%Y-%m-%d"),
                           next=(d + timedelta(days=1)).strftime("%Y-%m-%d") if day < W.today_ist() else None,
                           label=d.strftime("%a %d %b %Y"))


@bp.route("/img/<int:pid>")
@login_required
def img(pid):
    db = get_db()
    try:
        _p, data = W.image(db, g.user, pid)
    except ValidationError:
        abort(404)
    except PermissionDenied:
        abort(403)
    except UploadRejected:
        abort(410)
    resp = Response(data, mimetype="image/jpeg")
    resp.headers["Cache-Control"] = "private, max-age=86400"
    return resp


@bp.route("/status")
@login_required
def status():
    """Opened from the hourly reminder: what are you doing now? (one tap)"""
    db = get_db()
    return render_template("work/status.html", AV=AV, state=AV.state(db, g.user))
