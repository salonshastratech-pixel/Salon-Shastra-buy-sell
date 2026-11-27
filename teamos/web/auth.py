from flask import Blueprint, current_app, g, redirect, render_template, request, url_for

from ..db import get_db
from ..security import COOKIE, authenticate, end_session, safe_next, start_session

bp = Blueprint("auth", __name__)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if g.get("user"):
        return redirect(url_for("main.my_day"))
    error = None
    if request.method == "POST":
        db = get_db()
        user, error = authenticate(db, request.form.get("email", ""), request.form.get("password", ""))
        if user:
            token = start_session(db, user["id"])
            if user["role"] in ("team_member", "team_lead", "super_admin"):
                from ..domain import worklog
                worklog.on_login(db, user)
            nxt = request.args.get("next") or ""
            dest = safe_next(nxt, url_for("main.my_day"))
            if user.get("must_change_password"):
                dest = url_for("auth.change_password", first=1)
            resp = redirect(dest)
            resp.set_cookie(COOKIE, token, httponly=True, samesite="Strict",
                            secure=current_app.config["COOKIE_SECURE"],
                            max_age=current_app.config["SESSION_ABSOLUTE_HOURS"] * 3600)
            return resp
    return render_template("login.html", error=error), (401 if error else 200)


@bp.route("/logout", methods=["POST"])
def logout():
    if g.get("user") and g.user["role"] in ("team_member", "team_lead", "super_admin"):
        from ..domain import worklog
        worklog.on_logout(get_db(), g.user)
    end_session(get_db(), request.cookies.get(COOKIE))
    resp = redirect(url_for("auth.login"))
    resp.delete_cookie(COOKIE)
    return resp


# -- Second factor ---------------------------------------------------------------------
from flask import abort, flash, session  # noqa: E402

from .. import audit, mfa  # noqa: E402
from ..security import end_session as _end  # noqa: E402
from ..timeutil import now_iso as _now  # noqa: E402


def _mfa_fail(db):
    email = "mfa:" + g.user["email"]
    db.insert("login_attempts", {"email": email, "ip": request.remote_addr, "success": 0, "at": _now()})
    audit.record(db, g.user["id"], "auth.mfa", outcome="denied")
    from ..security import is_locked_out
    if is_locked_out(db, email):
        _end(db, request.cookies.get(COOKIE))
        return True
    return False


def _mfa_pass(db, step=None):
    if step is not None:
        db.execute("UPDATE users SET mfa_last_step = ? WHERE id = ?", (step, g.user["id"]))
    db.execute("UPDATE sessions SET mfa_passed = 1 WHERE id = ?", (g.session["id"],))
    audit.record(db, g.user["id"], "auth.mfa")


@bp.route("/mfa", methods=["GET", "POST"], endpoint="mfa")
def mfa_verify():
    if not g.get("user"):
        return redirect(url_for("auth.login"))
    if not g.get("mfa_pending"):
        return redirect(url_for("main.my_day"))
    if not g.user.get("mfa_enabled_at"):
        return redirect(url_for("auth.mfa_setup"))
    db = get_db()
    error = None
    if request.method == "POST":
        from ..security import is_locked_out
        if is_locked_out(db, "mfa:" + g.user["email"]):
            _end(db, request.cookies.get(COOKIE))
            return redirect(url_for("auth.login"))
        code = request.form.get("code", "")
        step = mfa.verify(g.user["mfa_secret"], code, g.user.get("mfa_last_step"))
        if step is not None:
            _mfa_pass(db, step)
            return redirect(url_for("main.my_day"))
        if mfa.use_recovery_code(db, g.user["id"], code):
            _mfa_pass(db)
            audit.record(db, g.user["id"], "auth.mfa_recovery_code_used")
            flash("Recovery code used. Each code works once — generate new ones soon from your profile.", "ok")
            return redirect(url_for("main.my_day"))
        if _mfa_fail(db):
            return redirect(url_for("auth.login"))
        error = "That code didn't match. Use the current 6-digit code or a recovery code."
    return render_template("mfa.html", mode="verify", error=error), (401 if error else 200)




@bp.route("/mfa/setup", methods=["GET", "POST"])
def mfa_setup():
    if not g.get("user"):
        return redirect(url_for("auth.login"))
    db = get_db()
    # Already enrolled: only re-enrol through an admin reset.
    if g.user.get("mfa_enabled_at") and g.get("mfa_pending"):
        return redirect(url_for("auth.mfa"))
    if g.user.get("mfa_enabled_at"):
        return redirect(url_for("main.my_day"))
    secret = g.user.get("mfa_secret")
    if not secret:
        secret = mfa.new_secret()
        db.execute("UPDATE users SET mfa_secret = ? WHERE id = ?", (secret, g.user["id"]))
    error = None
    if request.method == "POST":
        step = mfa.verify(secret, request.form.get("code", ""), None)
        if step is not None:
            with db:
                db.execute("UPDATE users SET mfa_enabled_at = ?, mfa_last_step = ? WHERE id = ?",
                           (_now(), step, g.user["id"]))
                codes = mfa.issue_recovery_codes(db, g.user["id"])
                db.execute("UPDATE sessions SET mfa_passed = 1 WHERE id = ?", (g.session["id"],))
                audit.record(db, g.user["id"], "auth.mfa_enrolled")
            return render_template("mfa.html", mode="codes", codes=codes)
        error = "That code didn't match — check the time on your phone is automatic and try the newest code."
    return render_template("mfa.html", mode="setup", secret=mfa.grouped(secret),
                           uri=mfa.provisioning_uri(secret, g.user["email"]), error=error)



# -- Change my password ------------------------------------------------------------------
@bp.route("/me/password", methods=["GET", "POST"])
def change_password():
    from werkzeug.security import check_password_hash

    from ..security import hash_password, login_required, revoke_user_sessions, validate_password_strength

    @login_required
    def view():
        db = get_db()
        error = None
        if request.method == "POST":
            f = request.form
            current, new, again = f.get("current") or "", f.get("new") or "", f.get("again") or ""
            if not check_password_hash(g.user["password_hash"], current):
                error = "Your current password is not correct."
            elif new != again:
                error = "The two new passwords are different."
            elif new == current:
                error = "Choose a password different from the current one."
            else:
                error = validate_password_strength(new)
            if error:
                audit.record(db, g.user["id"], "auth.password_change", outcome="denied")
            else:
                with db:
                    db.update("users", g.user["id"], {"password_hash": hash_password(new), "must_change_password": 0,
                                                      "password_changed_at": _now()})
                    # sign out every other device; keep this one
                    db.execute("DELETE FROM sessions WHERE user_id = ? AND id <> ?", (g.user["id"], g.session["id"]))
                    audit.record(db, g.user["id"], "auth.password_change")
                flash("Password changed. Other devices have been signed out.", "ok")
                return redirect(url_for("main.my_day"))
        return render_template("change_password.html", error=error, first=bool(request.args.get("first"))), \
            (400 if error else 200)
    return view()
