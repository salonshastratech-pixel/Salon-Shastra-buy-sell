"""Administration: users, grants, audit log, duplicate review, finance boundary."""
from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, url_for

from .. import audit
from ..db import get_db
from ..domain.errors import ValidationError
from ..permissions import CAPABILITIES, MATRIX_VERSION, ROLE_CAPABILITIES, ROLE_CHOICES, ROLE_LABELS, user_can

TEAM_LABELS = {"general": "Both", "seller": "Salon owners", "buyer": "Buyers & investors", "finance": "Finance",
               "admin": "Admin"}
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
                           ROLE_CAPABILITIES=ROLE_CAPABILITIES, ROLE_LABELS=ROLE_LABELS, MATRIX_VERSION=MATRIX_VERSION,
                           ROLE_CHOICES=ROLE_CHOICES, TEAM_LABELS=TEAM_LABELS)


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
    if role in ("finance", "super_admin") and team in ("seller", "buyer", "general"):
        team = "finance" if role == "finance" else "admin"
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
    msg = f"User {name} created. Share the temporary password securely."
    if (f.get("wa_number") or "").strip() or (f.get("number_id") or "").strip():
        from ..domain import inbox as I
        try:
            I.set_team_number(db, g.user, uid, f.get("wa_number"), f.get("number_id"))
            msg += " Their WhatsApp number is connected."
        except ValidationError as e:
            msg += " WhatsApp number not saved: " + " ".join(str(v) for v in e.errors.values())
    flash(msg, "ok")
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
    from ..domain import finance as FN
    db = get_db()
    return render_template("admin/finance.html", L=FN.ledger(db, g.user), FN=FN,
                           can_edit=user_can(db, g.user, "finance.edit"), today=now_iso()[:10])


@bp.route("/finance/<kind>", methods=["POST"])
@require("finance.view")
def finance_post(kind):
    from ..domain import finance as FN
    db = get_db()
    fns = {"agreement": FN.add_agreement, "invoice": FN.add_invoice, "receipt": FN.add_receipt,
           "expense": FN.add_expense}
    try:
        if kind == "earned":
            FN.mark_earned(db, g.user, int(request.form.get("agreement_id") or 0))
        elif kind in fns:
            fns[kind](db, g.user, request.form.to_dict())
        else:
            abort(404)
        flash("Saved to the finance ledger.", "ok")
    except ValidationError as e:
        flash(" ".join(str(v) for v in e.errors.values()), "error")
    return redirect(url_for("admin.finance"))


@bp.route("/users/<int:user_id>/reset-password", methods=["POST"])
@require("users.manage")
def reset_password(user_id):
    """Forgotten password: a one-time temporary password, shown once to the admin; it must be changed at sign-in."""
    import secrets
    db = get_db()
    u = db.one("SELECT * FROM users WHERE id = ?", (user_id,))
    if not u:
        abort(404)
    if u["id"] == g.user["id"]:
        flash("Use 'Change password' (click your name at the top) for your own account.", "error")
        return redirect(url_for("admin.users"))
    words = ["Salon", "Mirror", "Comb", "Studio", "Style", "Shine", "Trim", "Glow"]
    temp = secrets.choice(words) + secrets.choice(words).lower() + str(secrets.randbelow(9000) + 1000) + \
        secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ")
    with db:
        db.update("users", user_id, {"password_hash": hash_password(temp), "must_change_password": 1})
        revoke_user_sessions(db, user_id)
        audit.record(db, g.user["id"], "user.password_reset", "user", user_id)  # never the password itself
    flash(f"Temporary password for {u['full_name']}: {temp} — share it privately (e.g. by phone). "
          "They must choose their own when they sign in. It is not shown again.", "ok")
    return redirect(url_for("admin.users"))


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


@bp.route("/integrations", methods=["GET", "POST"])
@require("settings.manage")
def integrations():
    """Honest integration status (UAT-48): never claims 'live' without verified credentials and approval."""
    from flask import current_app
    from .. import messaging
    from ..domain import inbox as I
    db = get_db()
    if request.method == "POST" and request.form.get("action") == "retry":
        flash(I.retry_failed_events(db), "ok")
        return redirect(url_for("admin.integrations"))
    from .. import integrations as IG
    act = request.form.get("action") if request.method == "POST" else None
    if act == "settings":
        try:
            changed = IG.save(db, g.user, {k: v for k, v in request.form.items() if k in IG.SETTINGS})
            flash(f"Saved: {', '.join(IG.SETTINGS[k][0] for k in changed)}." if changed else "Nothing changed.", "ok")
        except ValidationError as e:
            flash(" ".join(str(v) for v in e.errors.values()), "error")
        return redirect(url_for("admin.integrations") + "#" + (request.form.get("connector") or ""))
    if act == "verify_token":
        IG.save(db, g.user, {"WHATSAPP_VERIFY_TOKEN": IG.new_verify_token()})
        flash("A new webhook verify token was generated and saved (encrypted). Show it once below to copy into Meta.", "ok")
        return redirect(url_for("admin.integrations", show_token=1) + "#whatsapp")
    if act in ("go_live", "test_only"):
        try:
            if act == "go_live" and messaging.whatsapp_status(current_app.config)["missing"]:
                raise ValidationError({"live": "Fill in all WhatsApp settings first."})
            IG.set_live(db, g.user, act == "go_live", request.form.get("confirm"))
            flash("WhatsApp is LIVE — real customers will receive messages." if act == "go_live"
                  else "WhatsApp is back in test-only mode.", "ok")
        except ValidationError as e:
            flash(" ".join(str(v) for v in e.errors.values()), "error")
        return redirect(url_for("admin.integrations") + "#whatsapp")
    if act == "test_whatsapp":
        to = db.scalar("SELECT wa_number FROM users WHERE id = ?", (g.user["id"],)) or \
            (request.form.get("to") or "").strip()
        from ..domain.common import normalize_indian_mobile
        norm, _err = normalize_indian_mobile(to) if to else (None, None)
        if not norm:
            flash("Enter your own mobile number to receive the test.", "error")
        else:
            st = messaging.whatsapp_status(current_app.config)
            res = messaging.provider(current_app.config).send(norm, "TeamOS test message", None,
                                                              template_name="hello_world" if st["live"] else None,
                                                              language="en_US")
            audit.record(db, g.user["id"], "integration.whatsapp_test", None, None, {"status": res["status"]})
            flash(f"Test message to {norm}: {res['status'].replace('_', ' ')}"
                  + (f" — {res.get('detail')}" if res.get("detail") else ""),
                  "ok" if res["status"] != "failed" else "error")
        return redirect(url_for("admin.integrations") + "#whatsapp")
    if request.method == "POST" and request.form.get("action") == "team_number":
        f = request.form
        try:
            I.set_team_number(db, g.user, int(f.get("user_id") or 0), f.get("wa_number"), f.get("number_id"))
            flash("WhatsApp number saved.", "ok")
        except ValidationError as e:
            flash(" ".join(str(v) for v in e.errors.values()), "error")
        return redirect(url_for("admin.integrations") + "#numbers")
    cfg = current_app.config
    events = db.all("SELECT * FROM webhook_events ORDER BY id DESC LIMIT 30")
    import os as _os
    from_server = {k for k in IG.SETTINGS if cfg.get(k) not in (None, "") or _os.environ.get(k)}
    shown_token = None
    if request.args.get("show_token") and "WHATSAPP_VERIFY_TOKEN" not in from_server:
        shown_token = IG.overlay(db).get("WHATSAPP_VERIFY_TOKEN")
    return render_template("admin/integrations.html", wa=messaging.whatsapp_status(cfg),
                           email=messaging.email_provider(cfg), files_ok=bool(cfg.get("FILES_KEY")),
                           public_url=messaging.setting("PUBLIC_BASE_URL") or "", events=events,
                           CONNECTORS=IG.CONNECTORS, saved=IG.describe(db), from_server=from_server,
                           shown_token=shown_token, webhook_url=messaging.public_base(request) + "/webhooks/whatsapp",
                           failed=sum(1 for e in events if e["status"] == "failed"), WA_SETTINGS=messaging.WA_SETTINGS,
                           team=db.all("SELECT id, full_name, role, wa_number, wa_phone_number_id FROM users WHERE "
                                       "is_active = 1 AND role <> 'finance' ORDER BY full_name"))


@bp.route("/templates", methods=["GET", "POST"])
@require("settings.manage")
def templates():
    from .. import messaging
    from ..domain import inbox as I
    db = get_db()
    I.ensure_templates(db)
    from .. import meta_templates as MT
    if request.method == "POST" and request.form.get("action") == "meta_check":
        try:
            c = MT.cross_check(db, g.user["id"], MT.fetch_meta_templates(current_app.config))
            flash(f"Checked with Meta: {c['approved']} approved and matching, {c['mismatch']} with different wording, "
                  f"{c['pending']} pending, {c['rejected']} rejected/paused, {c['missing']} not in Meta.", "ok")
        except ValueError as e:
            flash(str(e), "error")
        except Exception as e:  # network / Meta error: nothing changes
            flash(f"Could not reach Meta: {type(e).__name__}. Nothing was changed.", "error")
        return redirect(url_for("admin.templates"))
    if request.method == "POST":
        f = request.form
        try:
            I.save_template(db, g.user, f.get("key"), f.get("name"), f.get("category"), f.get("body"), f.get("status"),
                            f.get("provider_name"))
            flash("Template saved.", "ok")
        except ValidationError as e:
            flash(" ".join(str(v) for v in e.errors.values()), "error")
        return redirect(url_for("admin.templates"))
    rows = db.all("SELECT * FROM message_templates ORDER BY category, name")
    for r in rows:
        r["fields"] = messaging.placeholders(r["body"])
        r["meta_text"], r["meta_vars"] = MT.to_meta(r["body"])
        r["lint"] = MT.lint(r["key"], r["body"], r["meta_category"])
        r["live_ready"] = r["status"] == "provider_approved" and r["meta_status"] == "APPROVED" and not r["meta_issue"]
    replies = sum(1 for r in rows if r["meta_category"] == "REPLY")
    return render_template("admin/templates.html", rows=rows, edit=request.args.get("edit"), SAMPLES=MT.SAMPLES,
                           ready=sum(1 for r in rows if r["live_ready"]), replies=replies,
                           last_check=db.scalar("SELECT MAX(meta_checked_at) FROM message_templates"),
                           can_check=bool(messaging._cfg("WHATSAPP_BUSINESS_ACCOUNT_ID", current_app.config)
                                          and messaging._cfg("WHATSAPP_ACCESS_TOKEN", current_app.config)))


@bp.route("/outbox")
@require("settings.manage")
def outbox():
    from flask import current_app
    from .. import messaging
    db = get_db()
    rows = db.all("SELECT e.*, u.full_name FROM email_outbox e LEFT JOIN users u ON u.id = e.user_id "
                  "ORDER BY e.id DESC LIMIT 50")
    return render_template("admin/outbox.html", rows=rows, provider=messaging.email_provider(current_app.config))
