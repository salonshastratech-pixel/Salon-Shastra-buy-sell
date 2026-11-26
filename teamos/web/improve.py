"""Milestone 8 screens: workload & hand-over, record summaries, learning from declined matches, Tools & admin hub."""
from flask import Blueprint, abort, flash, g, jsonify, redirect, render_template, request, url_for

from ..db import get_db
from ..domain import learning as L
from ..domain import progress as P
from ..domain import summaries as S
from ..domain.errors import PermissionDenied, ValidationError
from ..permissions import user_can
from ..security import login_required, require

bp = Blueprint("improve", __name__)


def _err(e: ValidationError) -> str:
    return " ".join(str(v) for v in (e.errors.values() if isinstance(e.errors, dict) else [e.errors]))


def _opp(opp_id: int) -> dict:
    o = get_db().one("SELECT o.*, b.city, b.locality FROM opportunities o LEFT JOIN salon_businesses b "
                     "ON b.id = o.business_id WHERE o.id = ?", (opp_id,))
    if not o:
        abort(404)
    return o


# -- Summaries -------------------------------------------------------------------------------------------------
@bp.route("/records/<int:opp_id>/summary/draft", methods=["POST"])
@require("crm.view")
def summary_draft(opp_id):
    """Returns a draft as JSON for the record page to put in the text box. Nothing is saved."""
    return jsonify(S.draft(get_db(), g.user, _opp(opp_id)))


@bp.route("/records/<int:opp_id>/summary", methods=["POST"])
@require("crm.edit")
def summary_save(opp_id):
    opp = _opp(opp_id)
    try:
        S.save(get_db(), g.user, opp, request.form.get("summary", ""), request.form.get("source") or "manual")
        flash("Summary saved.", "ok")
    except ValidationError as e:
        flash(_err(e), "error")
    view = "buyers.detail" if opp["kind"] == "buyer" else "sellers.detail"
    return redirect(url_for(view, opp_id=opp_id) + "#summary")


# -- Workload --------------------------------------------------------------------------------------------------
@bp.route("/team/away", methods=["POST"])
@login_required
def set_away():
    uid = int(request.form.get("user_id") or g.user["id"])
    try:
        P.set_away(get_db(), g.user, uid, request.form.get("until") or None)
        flash("Marked as away — no new leads will be assigned until then." if request.form.get("until")
              else "Marked as available again.", "ok")
    except (ValidationError, PermissionDenied) as e:
        flash(_err(e) if isinstance(e, ValidationError) else str(e), "error")
    return redirect(request.form.get("next") or url_for("main.team_board") + "#workload")


@bp.route("/team/hand-over", methods=["POST"])
@require("crm.assign")
def hand_over():
    db = get_db()
    from_id = int(request.form["from_id"])
    to_ids = request.form.getlist("to_ids")
    kind = request.form.get("kind") or "all"
    limit = int(request.form["limit"]) if (request.form.get("limit") or "").isdigit() else None
    names = {u["id"]: u["full_name"] for u in db.all("SELECT id, full_name FROM users")}
    try:
        if request.form.get("go") != "1":
            plan = P.hand_over(db, g.user, from_id, to_ids, kind, limit, preview=True)
            return render_template("team_handover.html", plan=plan, names=names, from_id=from_id, to_ids=to_ids,
                                   kind=kind, limit=limit or "")
        res = P.hand_over(db, g.user, from_id, to_ids, kind, limit)
        flash(f"Handed over {res['records']} records and {res['tasks']} open tasks from {names.get(from_id)}.", "ok")
    except ValidationError as e:
        flash(_err(e), "error")
    return redirect(url_for("main.team_board") + "#workload")


# -- Learning from declined matches ---------------------------------------------------------------------------
@bp.route("/reports/learning")
@require("crm.view")
def learning():
    db = get_db()
    return render_template("reports/learning.html", stats=L.decline_stats(db), weights=L.current_weights(db),
                           open_=L.proposals(db, "proposed"), history=[p for p in L.proposals(db) if
                                                                     p["status"] != "proposed"][:15],
                           can_decide=user_can(db, g.user, "settings.manage"), L=L)


@bp.route("/reports/learning/check", methods=["POST"])
@require("settings.manage")
def learning_check():
    made = L.propose(get_db())
    flash(f"{len(made)} new suggestion(s)." if made else "No new suggestions — the declines so far don't point to "
          "a clear pattern.", "ok")
    return redirect(url_for("improve.learning"))


@bp.route("/reports/learning/<int:change_id>", methods=["POST"])
@require("settings.manage")
def learning_decide(change_id):
    try:
        L.decide(get_db(), g.user, change_id, request.form.get("decision") == "approve", request.form.get("note"))
        flash("Approved — matches are being re-scored with the new weight." if request.form.get("decision") ==
              "approve" else "Suggestion declined. It won't be proposed again for 30 days.", "ok")
    except ValidationError as e:
        flash(_err(e), "error")
    return redirect(url_for("improve.learning"))


@bp.route("/reports/learning/reset/<family>", methods=["POST"])
@require("settings.manage")
def learning_reset(family):
    try:
        L.reset(get_db(), g.user, family)
        flash("Reset to the starting weight — matches are being re-scored.", "ok")
    except ValidationError as e:
        flash(_err(e), "error")
    return redirect(url_for("improve.learning"))


# -- Tools & admin hub -----------------------------------------------------------------------------------------
TOOLS = [  # (endpoint, title, what, capability or None for everyone with crm.view)
    ("records.manage", "Leads & data", "Who gets new leads (salon owners / investors, in turn), and removing all leads to start fresh.",
     "users.manage"),
    ("imports.index", "Spreadsheet import", "Bring sellers and buyers in from Excel or a Google Sheets link.",
     "crm.import"),
    ("main.dictionary", "Field dictionary", "Every field we collect, what it means and who can see it.",
     "users.manage"),
    ("improve.learning", "Match learning", "Why matches are declined and suggested scoring changes.", "users.manage"),
    ("workflow.automations", "Automations", "Everything TeamOS does by itself — pause any of it, quiet hours.",
     "users.manage"),
    ("workflow.sources", "Lead sources", "Which channels bring leads that become deals.", "users.manage"),
    ("workflow.install", "Install on your phone", "Add TeamOS to your home screen; tap-to-call works from there.",
     "crm.view"),
    ("admin.users", "Users & access", "Add staff, WhatsApp numbers, reset passwords, mark people away.",
     "users.manage"),
    ("admin.integrations", "Integrations", "WhatsApp, e-mail, AI assistant, customer links — and what's next.",
     "settings.manage"),
    ("channels.lead_ads", "Lead ads", "Facebook / Instagram ad forms become leads. Try a test lead.",
     "settings.manage"),
    ("admin.templates", "Message templates", "WhatsApp wording, checked against Meta before use.",
     "settings.manage"),
    ("admin.duplicates", "Duplicate review", "People who may have been entered twice.", "duplicates.resolve"),
    ("admin.finance", "Admin finance", "Fees, invoices, receipts and company expenses.", "finance.view"),
    ("admin.outbox", "E-mail outbox", "Every e-mail the system has sent or tried to send.", "settings.manage"),
    ("admin.jobs", "Background jobs", "Scheduled checks: overdue alerts, form chasing, matching, stuck records.",
     "settings.manage"),
    ("admin.audit_log", "Audit log", "Who did what, when.", "users.manage"),
]


def visible_tools(db, user) -> list[dict]:
    return [{"endpoint": e, "title": t, "what": w} for e, t, w, cap in TOOLS if user_can(db, user, cap)]


@bp.route("/tools")
@login_required
def tools():
    items = visible_tools(get_db(), g.user)
    if not items:
        abort(403)
    return render_template("tools.html", items=items)


@bp.app_context_processor
def _record_extras():
    """On a seller / buyer / deal page: the 'not moving' banner (computed for that one record only)."""
    ep = request.endpoint or ""
    if ep not in ("sellers.detail", "buyers.detail", "deals.detail") or not g.get("user"):
        return {}
    args = request.view_args or {}
    rec_id = args.get("opp_id") or args.get("deal_id")
    if not rec_id:
        return {}
    kind = {"sellers.detail": "seller", "buyers.detail": "buyer", "deals.detail": "deal"}[ep]
    try:
        return {"stuck_info": P.stuck_one(get_db(), kind, int(rec_id))}
    except Exception:  # a banner must never break the page
        return {}
