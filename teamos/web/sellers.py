"""Seller management screens."""
from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from ..db import get_db
from ..domain import sellers as S
from ..domain import tasks as T
from ..domain.errors import ValidationError
from ..domain.seller_fields import LISTING_CHECKLIST, SECTIONS
from ..permissions import user_can
from ..security import can, deny, login_required, require
from ..timeutil import parse_local_input

bp = Blueprint("sellers", __name__, url_prefix="/sellers")


def _active_users(db):
    return db.all("SELECT id, full_name, team FROM users WHERE is_active = 1 AND role <> 'finance' ORDER BY full_name")


def _opp_or_404(db, opp_id):
    if not can("crm.view"):
        deny("crm.view", "opportunity", opp_id)
    opp = S.load(db, opp_id)
    if not opp:
        abort(404)
    return opp


@bp.route("/")
@require("crm.view")
def index():
    db = get_db()
    where, params = ["o.kind = 'seller'"], []
    q = (request.args.get("q") or "").strip()
    stage = request.args.get("stage") or ""
    owner = request.args.get("owner") or ""
    status = request.args.get("status") or "active"
    if q:
        like = f"%{q.lower()}%"
        digits = "".join(ch for ch in q if ch.isdigit())
        clause = "(lower(c.full_name) LIKE ? OR lower(b.name) LIKE ? OR lower(b.locality) LIKE ? OR lower(o.ref_code) LIKE ?"
        params += [like, like, like, like]
        if len(digits) >= 4:
            clause += " OR c.phone_normalized LIKE ?"
            params.append(f"%{digits[-10:]}%")
        where.append(clause + ")")
    if stage:
        where.append("o.stage = ?")
        params.append(stage)
    if owner == "me":
        where.append("o.primary_owner_id = ?")
        params.append(g.user["id"])
    elif owner.isdigit():
        where.append("o.primary_owner_id = ?")
        params.append(int(owner))
    if status in ("active", "holding", "closed"):
        where.append("o.status = ?")
        params.append(status)
    rows = db.all(
        "SELECT o.*, c.full_name AS contact_name, c.phone_normalized, b.name AS salon_name, b.locality, b.city, "
        "u.full_name AS owner_name, l.asking_price, l.approval_state, l.chairs, l.gender_format "
        "FROM opportunities o JOIN contacts c ON c.id = o.contact_id LEFT JOIN salon_businesses b ON b.id = o.business_id "
        "LEFT JOIN users u ON u.id = o.primary_owner_id LEFT JOIN seller_listings l ON l.opportunity_id = o.id "
        "WHERE " + " AND ".join(where) + " ORDER BY CASE WHEN o.next_action_due IS NULL THEN 1 ELSE 0 END, "
        "o.next_action_due LIMIT 300", params)
    return render_template("sellers/index.html", rows=rows, users=_active_users(db), q=q, stage=stage,
                           owner=owner, status=status, STAGES=S.STAGES + S.HOLDING, STAGE_LABELS=S.STAGE_LABELS)


@bp.route("/new", methods=["GET", "POST"])
@require("crm.create")
def new():
    db = get_db()
    form, errors, duplicates = {}, {}, None
    if request.method == "POST":
        form = request.form.to_dict()
        try:
            result = S.create_lead(db, g.user, form, dedupe_decision=form.get("dedupe_decision") or None)
            if "duplicates" in result:
                duplicates = result["duplicates"]
            else:
                flash("Seller lead created. A first-contact task is on the owner's My Day.", "ok")
                return redirect(url_for("sellers.detail", opp_id=result["opportunity_id"]))
        except ValidationError as e:
            errors = e.errors
    return render_template("sellers/new.html", form=form, errors=errors, duplicates=duplicates,
                           users=_active_users(db), SOURCES=S.SOURCES, STAGE_LABELS=S.STAGE_LABELS)


@bp.route("/<int:opp_id>")
@login_required
def detail(opp_id):
    db = get_db()
    opp = _opp_or_404(db, opp_id)
    raw_listing = S.load_listing(db, opp["listing_id"])
    listing, hidden = S.masked_listing(db, g.user, opp, raw_listing)
    full_prov = S.provenance(db, opp["listing_id"])
    prov = {k: v for k, v in full_prov.items() if k not in hidden}
    tab = request.args.get("tab", "overview")
    ctx = dict(
        opp=opp, listing=listing, prov=prov, hidden=hidden, tab=tab,
        cost=S.all_in_cost(raw_listing, full_prov),
        comp=S.completeness(raw_listing, full_prov),
        readiness=S.readiness_score(db, opp, raw_listing, full_prov),
        fin=S.financial_summary(db, opp["listing_id"]),
        tasks=T.open_tasks_for(db, "opportunity", opp_id),
        timeline=S.timeline(db, opp_id),
        transitions=S.transition_options(db, g.user, opp, raw_listing, full_prov),
        checklist=db.all("SELECT * FROM listing_checklist WHERE listing_id = ?", (opp["listing_id"],)),
        approvals=db.all("SELECT a.*, u.full_name AS actor_name FROM listing_approvals a LEFT JOIN users u "
                         "ON u.id = a.actor_id WHERE listing_id = ? ORDER BY a.at DESC", (opp["listing_id"],)),
        swot=db.all("SELECT * FROM swot_items WHERE listing_id = ? ORDER BY kind, id", (opp["listing_id"],)),
        history=db.all("SELECT h.*, u.full_name AS actor_name FROM stage_history h LEFT JOIN users u "
                       "ON u.id = h.actor_id WHERE opportunity_id = ? ORDER BY h.at DESC, h.id DESC", (opp_id,)),
        changes=db.all("SELECT f.*, u.full_name AS actor_name FROM field_change_log f LEFT JOIN users u "
                       "ON u.id = f.actor_id WHERE entity_type = 'seller_listing' AND entity_id = ? "
                       "ORDER BY f.id DESC LIMIT 60", (opp["listing_id"],)),
        consents=S.contact_consent_state(db, opp["contact_id"]),
        other_opps=db.all("SELECT o.id, o.ref_code, o.kind, o.stage, b.name AS salon_name FROM opportunities o "
                          "LEFT JOIN salon_businesses b ON b.id = o.business_id WHERE o.contact_id = ? AND o.id <> ?",
                          (opp["contact_id"], opp_id)),
        users=_active_users(db), blockers=S.approval_blockers(db, opp["listing_id"]),
        SECTIONS=SECTIONS, LISTING_CHECKLIST=dict(LISTING_CHECKLIST), STAGE_LABELS=S.STAGE_LABELS,
        SOURCE_LABELS=S.SOURCE_LABELS, CALL_OUTCOMES=T.CALL_OUTCOMES, CALL_OUTCOME_LABELS=T.CALL_OUTCOME_LABELS,
        can_restricted=not hidden, can_approve=user_can(db, g.user, "listing.approve"),
        can_review=user_can(db, g.user, "evidence.review"),
    )
    return render_template("sellers/detail.html", **ctx)


def _back(opp_id, tab=None):
    return redirect(url_for("sellers.detail", opp_id=opp_id, tab=tab) + ("#" + tab if tab else ""))


def _run(opp_id, fn, ok_msg, tab=None):
    try:
        fn()
        flash(ok_msg, "ok")
    except ValidationError as e:
        flash(" ".join(e.errors.values()), "error")
    return _back(opp_id, tab)


@bp.route("/<int:opp_id>/section/<section>", methods=["POST"])
@login_required
def save_section(opp_id, section):
    db = get_db()
    opp = _opp_or_404(db, opp_id)
    if section not in dict(SECTIONS):
        abort(404)
    try:
        changed = S.update_fields(db, g.user, opp, request.form.to_dict(), section)
        flash(f"Saved {len(changed)} change(s)." if changed else "No changes.", "ok")
    except ValidationError as e:
        flash("Not saved: " + "; ".join(f"{k.replace('_', ' ')} — {v}" for k, v in e.errors.items()), "error")
    return _back(opp_id, "intake")


@bp.route("/<int:opp_id>/call", methods=["POST"])
@login_required
def call(opp_id):
    db = get_db()
    opp = _opp_or_404(db, opp_id)
    f = request.form

    def go():
        S.log_call(db, g.user, opp, f.get("outcome"), f.get("note"),
                   int(f["complete_task_id"]) if f.get("complete_task_id") else None,
                   f.get("next_title"), parse_local_input(f.get("next_due")))
    return _run(opp_id, go, "Call logged.")


@bp.route("/<int:opp_id>/note", methods=["POST"])
@login_required
def note(opp_id):
    db = get_db()
    opp = _opp_or_404(db, opp_id)
    return _run(opp_id, lambda: S.add_note(db, g.user, opp, request.form.get("body")), "Note added.")


@bp.route("/<int:opp_id>/stage", methods=["POST"])
@login_required
def stage(opp_id):
    db = get_db()
    opp = _opp_or_404(db, opp_id)
    f = request.form
    return _run(opp_id, lambda: S.change_stage(db, g.user, opp, f.get("to"), f.get("reason"),
                                               parse_local_input(f.get("follow_up_due"))), "Stage updated.")


@bp.route("/<int:opp_id>/assign", methods=["POST"])
@login_required
def assign(opp_id):
    db = get_db()
    opp = _opp_or_404(db, opp_id)
    return _run(opp_id, lambda: S.assign_owner(db, g.user, opp, int(request.form.get("owner_id") or 0),
                                               request.form.get("reason")), "Owner changed.")


@bp.route("/<int:opp_id>/task", methods=["POST"])
@login_required
def add_task(opp_id):
    db = get_db()
    opp = _opp_or_404(db, opp_id)
    f = request.form

    def go():
        with db:
            T.create_task(db, g.user, title=f.get("title"), assignee_id=int(f.get("assignee_id") or 0),
                          due_at=parse_local_input(f.get("due_at")), task_type=f.get("task_type") or "follow_up",
                          priority=f.get("priority") or "normal", entity_type="opportunity", entity_id=opp["id"])
    return _run(opp_id, go, "Task created.")


@bp.route("/<int:opp_id>/evidence", methods=["POST"])
@login_required
def evidence(opp_id):
    db = get_db()
    opp = _opp_or_404(db, opp_id)
    return _run(opp_id, lambda: S.set_evidence(db, g.user, opp, request.form.get("field_key"),
                                               request.form.get("status"), request.form.get("note")),
                "Evidence status updated.", "intake")


@bp.route("/<int:opp_id>/financials", methods=["POST"])
@login_required
def financials(opp_id):
    db = get_db()
    opp = _opp_or_404(db, opp_id)
    f = request.form
    return _run(opp_id, lambda: S.save_month(db, g.user, opp, f.get("month", ""), f.get("revenue"),
                                             f.get("expenses"), f.get("drawings"), f.get("source")),
                "Month saved.", "financials")


@bp.route("/<int:opp_id>/swot", methods=["POST"])
@login_required
def swot(opp_id):
    db = get_db()
    opp = _opp_or_404(db, opp_id)
    f = request.form
    return _run(opp_id, lambda: S.add_swot(db, g.user, opp, f.get("kind"), f.get("statement"), f.get("source"),
                                           f.get("confidence"), f.get("open_question")), "SWOT item added.",
                "assessment")


@bp.route("/<int:opp_id>/checklist", methods=["POST"])
@login_required
def checklist(opp_id):
    db = get_db()
    opp = _opp_or_404(db, opp_id)
    f = request.form
    return _run(opp_id, lambda: S.set_checklist(db, g.user, opp, f.get("item_key"), f.get("status"), f.get("note")),
                "Checklist updated.", "approval")


@bp.route("/<int:opp_id>/request-review", methods=["POST"])
@login_required
def request_review(opp_id):
    db = get_db()
    opp = _opp_or_404(db, opp_id)
    return _run(opp_id, lambda: S.request_review(db, g.user, opp), "Submitted for approval.", "approval")


@bp.route("/<int:opp_id>/decision", methods=["POST"])
@login_required
def decision(opp_id):
    db = get_db()
    opp = _opp_or_404(db, opp_id)
    return _run(opp_id, lambda: S.decide_listing(db, g.user, opp, request.form.get("decision"),
                                                 request.form.get("conditions")), "Decision recorded.", "approval")
