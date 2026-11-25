"""Team availability and activity screens."""
from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from ..db import get_db
from ..domain import availability as AV
from ..domain.errors import PermissionDenied, ValidationError
from ..permissions import user_can
from ..security import login_required, require

bp = Blueprint("team", __name__)


def _err(e: ValidationError) -> str:
    return " ".join(str(v) for v in (e.errors.values() if isinstance(e.errors, dict) else [e.errors]))


def _target(db):
    """Whose availability is being edited: yourself, or (team leads) someone else via ?user=."""
    uid = request.values.get("user", type=int) or g.user["id"]
    if uid != g.user["id"] and not user_can(db, g.user, "crm.assign"):
        abort(403)
    u = db.one("SELECT * FROM users WHERE id = ? AND is_active = 1", (uid,))
    if not u:
        abort(404)
    return u


@bp.route("/me/availability", methods=["GET", "POST"])
@login_required
def mine():
    db = get_db()
    u = _target(db)
    if request.method == "POST":
        action = request.form.get("action")
        try:
            if action == "hours":
                AV.save_hours(db, g.user, u["id"], request.form)
                flash("Working hours saved.", "ok")
            elif action == "time_off":
                AV.add_time_off(db, g.user, u["id"], request.form.get("start"), request.form.get("end"),
                                request.form.get("kind"), request.form.get("note"))
                flash("Time off added. The team can see it on Who's working.", "ok")
            elif action == "cancel":
                AV.cancel_time_off(db, g.user, int(request.form.get("tid") or 0))
                flash("Time off removed.", "ok")
            elif action == "status":
                AV.set_status(db, g.user, request.form.get("status") or "", request.form.get("note"), u["id"])
                flash("Status updated.", "ok")
        except PermissionDenied:
            abort(403)
        except (ValidationError, ValueError) as e:
            flash(_err(e) if isinstance(e, ValidationError) else "Check the values.", "error")
        return redirect(url_for("team.mine", user=u["id"] if u["id"] != g.user["id"] else None))
    return render_template("team/mine.html", u=u, hours=AV.hours(db, u["id"]), offs=AV.time_off(db, u["id"]),
                           now=AV.state(db, u), AV=AV, self_edit=u["id"] == g.user["id"])


@bp.route("/me/status", methods=["POST"])
@login_required
def status():
    """One-tap status from the top bar."""
    try:
        AV.set_status(get_db(), g.user, request.form.get("status") or "", request.form.get("note"))
    except ValidationError as e:
        flash(_err(e), "error")
    nxt = request.form.get("next") or ""
    return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("main.my_day"))


@bp.route("/team/availability")
@require("crm.view")
def week():
    db = get_db()
    return render_template("team/week.html", w=AV.week(db), offs=AV.time_off(db), AV=AV,
                           can_edit_others=user_can(db, g.user, "crm.assign"))


@bp.route("/team/activity")
@require("crm.view")
def activity():
    db = get_db()
    return render_template("team/activity.html", a=AV.activity(db, request.args.get("period") or "today"), AV=AV)



@bp.route("/team/worklog")
@require("users.manage")
def worklog():
    """Admin: hours (sign-in, sign-out, breaks, field) and output per person per day."""
    from datetime import date, timedelta

    from ..domain import worklog as WL
    from ..timeutil import IST, now_utc
    db = get_db()
    today = now_utc().astimezone(IST).date()
    try:
        day = date.fromisoformat(request.args.get("day") or today.isoformat())
    except ValueError:
        day = today
    day = min(day, today)
    person = None
    uid = request.args.get("user") or ""
    if uid.isdigit():
        person = db.one("SELECT * FROM users WHERE id = ?", (int(uid),))
    return render_template("team/worklog.html", rows=WL.team_day(db, day), day=day, today=today,
                           one_day=timedelta(days=1), person=person,
                           person_week=WL.week(db, person, day) if person else None,
                           person_day=WL.day_summary(db, person, day) if person else None,
                           STATUS_LABELS=AV.STATUS_LABELS)



@bp.route("/desk")
@require("users.manage")
def founder_desk():
    """Everything that needs the founder today, on one page."""
    from ..domain import advisor
    return render_template("desk.html", d=advisor.desk(get_db()), STATUS_LABELS=AV.STATUS_LABELS)
