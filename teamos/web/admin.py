"""Administration: users, grants, audit log, duplicate review, finance boundary."""
from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from .. import audit
from ..db import get_db
from ..permissions import CAPABILITIES, MATRIX_VERSION, ROLE_CAPABILITIES, ROLE_LABELS
from ..security import hash_password, require, revoke_user_sessions, validate_password_strength
from ..timeutil import now_iso, parse_local_input

bp = Blueprint("admin", __name__, url_prefix="/admin")


@bp.route("/users")
@require("users.manage")
def users():
    db = get_db()
    rows = db.all("SELECT * FROM users ORDER BY is_active DESC, full_name")
    grants = db.all("SELECT gr.*, u.full_name AS granted_by_name FROM user_grants gr JOIN users u ON u.id = gr.granted_by "
                    "WHERE gr.revoked_at IS NULL ORDER BY gr.created_at DESC")
    return render_template("admin/users.html", rows=rows, grants=grants, CAPABILITIES=CAPABILITIES,
                           ROLE_CAPABILITIES=ROLE_CAPABILITIES, ROLE_LABELS=ROLE_LABELS, MATRIX_VERSION=MATRIX_VERSION)


@bp.route("/users", methods=["POST"])
@require("users.manage")
def create_user():
    db = get_db()
    f = request.form
    email = (f.get("email") or "").strip().lower()
    name = (f.get("full_name") or "").strip()
    role, team, pw = f.get("role"), f.get("team") or "general", f.get("password") or ""
    err = None
    if not email or "@" not in email or not name:
        err = "Name and a valid email are required."
    elif role not in ROLE_LABELS:
        err = "Choose a role."
    elif db.one("SELECT id FROM users WHERE email = ?", (email,)):
        err = "A user with that email already exists."
    else:
        err = validate_password_strength(pw)
    if err:
        flash(err, "error")
        return redirect(url_for("admin.users"))
    with db:
        uid = db.insert("users", {"email": email, "full_name": name, "password_hash": hash_password(pw), "role": role,
                                  "team": team, "is_active": 1, "created_at": now_iso()})
        audit.record(db, g.user["id"], "user.create", "user", uid, {"email": email, "role": role})
    flash(f"User {name} created. Share the temporary password securely.", "ok")
    return redirect(url_for("admin.users"))


@bp.route("/users/<int:user_id>/active", methods=["POST"])
@require("users.manage")
def toggle_active(user_id):
    db = get_db()
    u = db.one("SELECT * FROM users WHERE id = ?", (user_id,))
    if not u:
        abort(404)
    if u["id"] == g.user["id"]:
        flash("You cannot disable your own account.", "error")
        return redirect(url_for("admin.users"))
    new = 0 if u["is_active"] else 1
    with db:
        db.update("users", user_id, {"is_active": new})
        if not new:
            revoke_user_sessions(db, user_id)
        audit.record(db, g.user["id"], "user.enable" if new else "user.disable", "user", user_id)
    flash(f"{u['full_name']} {'enabled' if new else 'disabled — all sessions ended immediately'}.", "ok")
    return redirect(url_for("admin.users"))


@bp.route("/users/<int:user_id>/role", methods=["POST"])
@require("users.manage")
def change_role(user_id):
    db = get_db()
    role = request.form.get("role")
    if role not in ROLE_LABELS or user_id == g.user["id"]:
        flash("Invalid role change (you cannot change your own role).", "error")
        return redirect(url_for("admin.users"))
    with db:
        old = db.scalar("SELECT role FROM users WHERE id = ?", (user_id,))
        db.update("users", user_id, {"role": role})
        audit.record(db, g.user["id"], "user.role", "user", user_id, {"from": old, "to": role})
    flash("Role updated.", "ok")
    return redirect(url_for("admin.users"))


@bp.route("/grants", methods=["POST"])
@require("users.manage")
def grant():
    db = get_db()
    f = request.form
    cap = f.get("capability")
    reason = (f.get("reason") or "").strip()
    if cap not in CAPABILITIES or not reason or not (f.get("user_id") or "").isdigit():
        flash("Choose a user, capability and give a reason.", "error")
        return redirect(url_for("admin.users"))
    scope_id = int(f["scope_id"]) if (f.get("scope_id") or "").isdigit() else None
    with db:
        gid = db.insert("user_grants", {
            "user_id": int(f["user_id"]), "capability": cap, "scope_type": "opportunity" if scope_id else None,
            "scope_id": scope_id, "granted_by": g.user["id"], "reason": reason, "created_at": now_iso(),
            "expires_at": parse_local_input(f.get("expires_at"))})
        audit.record(db, g.user["id"], "grant.create", "user", int(f["user_id"]),
                     {"grant_id": gid, "capability": cap, "scope_id": scope_id})
    flash("Capability granted.", "ok")
    return redirect(url_for("admin.users"))


@bp.route("/grants/<int:grant_id>/revoke", methods=["POST"])
@require("users.manage")
def revoke(grant_id):
    db = get_db()
    with db:
        db.execute("UPDATE user_grants SET revoked_at = ? WHERE id = ?", (now_iso(), grant_id))
        audit.record(db, g.user["id"], "grant.revoke", "user_grant", grant_id)
    flash("Grant revoked.", "ok")
    return redirect(url_for("admin.users"))


@bp.route("/audit")
@require("audit.view")
def audit_log():
    db = get_db()
    outcome = request.args.get("outcome") or ""
    where, params = "", []
    if outcome in ("allowed", "denied", "failed"):
        where, params = "WHERE a.outcome = ?", [outcome]
    rows = db.all("SELECT a.*, u.full_name AS actor_name FROM audit_events a LEFT JOIN users u ON u.id = a.actor_id "
                  + where + " ORDER BY a.id DESC LIMIT 300", params)
    return render_template("admin/audit.html", rows=rows, outcome=outcome)


@bp.route("/duplicates")
@require("duplicates.resolve")
def duplicates():
    db = get_db()
    rows = db.all(
        "SELECT d.*, a.full_name AS a_name, a.phone_normalized AS a_phone, b.full_name AS b_name, "
        "b.phone_normalized AS b_phone FROM duplicate_reviews d JOIN contacts a ON a.id = d.contact_id "
        "JOIN contacts b ON b.id = d.candidate_contact_id ORDER BY d.status = 'open' DESC, d.created_at DESC LIMIT 200")
    for r in rows:
        r["a_opps"] = db.all("SELECT id, ref_code, kind FROM opportunities WHERE contact_id = ?", (r["contact_id"],))
        r["b_opps"] = db.all("SELECT id, ref_code, kind FROM opportunities WHERE contact_id = ?",
                             (r["candidate_contact_id"],))
    return render_template("admin/duplicates.html", rows=rows)


@bp.route("/duplicates/<int:dup_id>", methods=["POST"])
@require("duplicates.resolve")
def resolve_duplicate(dup_id):
    db = get_db()
    status = request.form.get("status")
    rationale = (request.form.get("rationale") or "").strip()
    if status not in ("distinct", "same_person_linked") or not rationale:
        flash("Choose a resolution and give a rationale.", "error")
        return redirect(url_for("admin.duplicates"))
    with db:
        db.execute("UPDATE duplicate_reviews SET status = ?, rationale = ?, resolved_by = ?, resolved_at = ? WHERE id = ?",
                   (status, rationale, g.user["id"], now_iso(), dup_id))
        audit.record(db, g.user["id"], "duplicate.resolve", "duplicate_review", dup_id,
                     {"status": status, "rationale": rationale})
    flash("Recorded. No records were merged or deleted; merge tooling arrives with imports (Milestone 3).", "ok")
    return redirect(url_for("admin.duplicates"))


@bp.route("/finance")
@require("finance.view")
def finance():
    return render_template("admin/finance.html")


@bp.route("/users/<int:user_id>/reset-mfa", methods=["POST"])
@require("users.manage")
def reset_mfa(user_id):
    """For a lost phone: clears the authenticator and signs the user out; they enrol again at next login."""
    db = get_db()
    u = db.one("SELECT * FROM users WHERE id = ?", (user_id,))
    if not u:
        abort(404)
    if u["id"] == g.user["id"]:
        flash("Ask another admin to reset your own two-step sign-in.", "error")
        return redirect(url_for("admin.users"))
    with db:
        db.execute("UPDATE users SET mfa_secret = NULL, mfa_enabled_at = NULL, mfa_last_step = NULL WHERE id = ?",
                   (user_id,))
        db.execute("UPDATE mfa_recovery_codes SET used_at = ? WHERE user_id = ? AND used_at IS NULL", (now_iso(), user_id))
        revoke_user_sessions(db, user_id)
        audit.record(db, g.user["id"], "user.mfa_reset", "user", user_id)
    flash(f"Two-step sign-in reset for {u['full_name']}. They'll set it up again at next login.", "ok")
    return redirect(url_for("admin.users"))


@bp.route("/jobs")
@require("settings.manage")
def jobs():
    db = get_db()
    rows = db.all("SELECT * FROM jobs ORDER BY id DESC LIMIT 100")
    counts = {r["status"]: r["n"] for r in db.all("SELECT status, COUNT(*) AS n FROM jobs GROUP BY status")}
    return render_template("admin/jobs.html", rows=rows, counts=counts)
