"""Buyer / investor management screens (Milestone 3)."""
from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from ..db import get_db
from ..domain import buyer_fields as BFD
from ..domain import buyers as B
from ..domain import tasks as T
from ..domain.errors import ValidationError
from ..permissions import user_can
from ..security import can, deny, login_required, require
from ..timeutil import parse_local_input
from .sellers import _active_users, _link_chat

bp = Blueprint("buyers", __name__, url_prefix="/buyers")

LIST_SQL = (
    "SELECT o.*, c.full_name AS contact_name, c.phone_normalized, c.city AS contact_city, u.full_name AS owner_name, "
    "m.need_type, m.all_in_max, m.preferred_city, m.funding_status, m.purchase_window, m.data_version, "
    "m.criteria_reviewed_version, m.id AS mandate_id "
    "FROM opportunities o JOIN contacts c ON c.id = o.contact_id LEFT JOIN users u ON u.id = o.primary_owner_id "
    "LEFT JOIN buyer_mandates m ON m.opportunity_id = o.id ")


def _all_stage_labels():
    from ..domain import sellers as S
    return {**S.STAGE_LABELS, **B.STAGE_LABELS}


def _opp_or_404(db, opp_id):
    if not can("crm.view"):
        deny("crm.view", "opportunity", opp_id)
    opp = B.load(db, opp_id)
    if not opp:
        abort(404)
    return opp


@bp.route("/")
@require("crm.view")
def index():
    db = get_db()
    where, params = ["o.kind = 'buyer'"], []
    q = (request.args.get("q") or "").strip()
    stage = request.args.get("stage") or ""
    owner = request.args.get("owner") or ""
    need = request.args.get("need") or ""
    status = request.args.get("status") or "active"
    queue = request.args.get("queue") or ""
    if q:
        like = f"%{q.lower()}%"
        digits = "".join(ch for ch in q if ch.isdigit())
        clause = ("(lower(c.full_name) LIKE ? OR lower(o.ref_code) LIKE ? OR lower(m.preferred_city) LIKE ? OR "
                  "EXISTS (SELECT 1 FROM buyer_localities l WHERE l.mandate_id = m.id AND lower(l.locality) LIKE ?)")
        params += [like, like, like, like]
        if len(digits) >= 4:
            clause += " OR c.phone_normalized LIKE ?"
            params.append(f"%{digits[-10:]}%")
        where.append(clause + ")")
    if stage:
        where.append("o.stage = ?")
        params.append(stage)
    if need:
        where.append("m.need_type = ?")
        params.append(need)
    if owner == "me":
        where.append("o.primary_owner_id = ?")
        params.append(g.user["id"])
    elif owner.isdigit():
        where.append("o.primary_owner_id = ?")
        params.append(int(owner))
    if queue == "criteria":
        where.append("(m.criteria_reviewed_version IS NULL OR m.criteria_reviewed_version <> m.data_version)")
    elif queue == "funding":
        where.append("m.funding_status IN ('not_reviewed','evidence_requested')")
    if status in ("active", "holding", "closed"):
        where.append("o.status = ?")
        params.append(status)
    rows = db.all(LIST_SQL + "WHERE " + " AND ".join(where) + " ORDER BY CASE WHEN o.next_action_due IS NULL "
                  "THEN 1 ELSE 0 END, o.next_action_due LIMIT 300", params)
    locs: dict[int, list[str]] = {}
    ids = [r["mandate_id"] for r in rows if r["mandate_id"]]
    if ids:  # portable (no group_concat/string_agg): one query, grouped in Python
        for lr in db.all("SELECT mandate_id, locality FROM buyer_localities WHERE kind = 'preferred' AND mandate_id IN ("
                         + ",".join("?" * len(ids)) + ") ORDER BY rank_order", ids):
            locs.setdefault(lr["mandate_id"], []).append(lr["locality"])
    for r in rows:
        r["top_localities"] = ", ".join(locs.get(r["mandate_id"], []))
        r["criteria_state"] = ("not_reviewed" if r["criteria_reviewed_version"] is None else
                               "current" if r["criteria_reviewed_version"] == r["data_version"] else "stale")
    return render_template("buyers/index.html", rows=rows, users=_active_users(db), q=q, stage=stage, owner=owner,
                           need=need, status=status, queue=queue, STAGES=B.STAGES + B.HOLDING,
                           STAGE_LABELS=B.STAGE_LABELS)


@bp.route("/new", methods=["GET", "POST"])
@require("crm.create")
def new():
    db = get_db()
    form, errors, duplicates = {}, {}, None
    if request.method == "GET":  # prefill, e.g. "+ New lead" from a WhatsApp chat with an unknown number
        form = {k: request.args[k] for k in ("phone", "source", "from_conv") if request.args.get(k)}
    if request.method == "POST":
        form = request.form.to_dict()
        try:
            result = B.create_lead(db, g.user, form, dedupe_decision=form.get("dedupe_decision") or None)
            if "duplicates" in result:
                duplicates = result["duplicates"]
            else:
                _link_chat(db, form.get("from_conv"), result["opportunity_id"])
                flash("Buyer lead created. A first-contact task is on the owner's My Day.", "ok")
                return redirect(url_for("buyers.detail", opp_id=result["opportunity_id"]))
        except ValidationError as e:
            errors = e.errors
    return render_template("buyers/new.html", form=form, errors=errors, duplicates=duplicates,
                           users=_active_users(db), SOURCES=B.SOURCES, STAGE_LABELS=_all_stage_labels())


@bp.route("/<int:opp_id>")
@login_required
def detail(opp_id):
    db = get_db()
    opp = _opp_or_404(db, opp_id)
    raw = B.load_mandate(db, opp["mandate_id"])
    mandate = B.masked_mandate(db, g.user, opp, raw)
    prov = B.provenance(db, raw["id"])
    locs = B.localities(db, raw["id"])
    prefs = B.preferences(db, raw["id"])
    tab = request.args.get("tab", "overview")
    from ..domain import sellers as S
    reviewer = (db.scalar("SELECT full_name FROM users WHERE id = ?", (raw["funding_reviewed_by"],))
                if raw["funding_reviewed_by"] else None)
    ctx = dict(
        opp=opp, mandate=mandate, prov=prov, locs=locs, prefs=prefs, tab=tab,
        choice_prefs=sorted((d, c, i) for (d, c), i in prefs.items() if c),
        comp=B.completeness(raw, prov, locs),
        score=B.seriousness_score(db, opp, raw, prov, locs, prefs),
        criteria_state=B.criteria_review_state(raw), criteria_blockers=B.criteria_review_blockers(db, raw),
        funding_reviewer=reviewer,
        tasks=T.open_tasks_for(db, "opportunity", opp_id),
        timeline=B.timeline(db, opp_id),
        transitions=B.transition_options(db, g.user, opp, raw, prov),
        history=db.all("SELECT h.*, u.full_name AS actor_name FROM stage_history h LEFT JOIN users u "
                       "ON u.id = h.actor_id WHERE opportunity_id = ? ORDER BY h.at DESC, h.id DESC", (opp_id,)),
        changes=db.all("SELECT f.*, u.full_name AS actor_name FROM field_change_log f LEFT JOIN users u "
                       "ON u.id = f.actor_id WHERE entity_type = 'buyer_mandate' AND entity_id = ? "
                       "ORDER BY f.id DESC LIMIT 80", (raw["id"],)),
        consents=S.contact_consent_state(db, opp["contact_id"]),
        other_opps=db.all("SELECT o.id, o.ref_code, o.kind, o.stage, b.name AS salon_name FROM opportunities o "
                          "LEFT JOIN salon_businesses b ON b.id = o.business_id WHERE o.contact_id = ? AND o.id <> ?",
                          (opp["contact_id"], opp_id)),
        users=_active_users(db), SECTIONS=BFD.SECTIONS, STAGE_LABELS=B.STAGE_LABELS,
        ALL_STAGE_LABELS={**S.STAGE_LABELS, **B.STAGE_LABELS},
        SOURCE_LABELS=B.SOURCE_LABELS, CALL_OUTCOMES=T.CALL_OUTCOMES, CALL_OUTCOME_LABELS=T.CALL_OUTCOME_LABELS,
        can_review=user_can(db, g.user, "evidence.review"),
        can_funding_detail=B.can_view_funding_evidence(db, g.user, opp),
    )
    from .forms import record_context
    ctx.update(record_context(db, g.user, opp))
    if tab in ("overview", "matches"):
        from ..domain import matching as MM
        from .matches import record_matches
        ctx.update(record_matches(db, opp))  # incremental: only re-scores pairs whose facts changed
        ctx["unblock"] = MM.unblock_suggestions(db, opp)
    return render_template("buyers/detail.html", **ctx)


def _back(opp_id, tab=None):
    return redirect(url_for("buyers.detail", opp_id=opp_id, tab=tab) + ("#" + tab if tab else ""))


def _run(opp_id, fn, ok_msg, tab=None):
    try:
        fn()
        flash(ok_msg, "ok")
    except ValidationError as e:
        flash(" ".join(str(v) for v in e.errors.values()), "error")
    return _back(opp_id, tab)


@bp.route("/<int:opp_id>/section/<section>", methods=["POST"])
@login_required
def save_section(opp_id, section):
    db = get_db()
    opp = _opp_or_404(db, opp_id)
    if section not in dict(BFD.SECTIONS):
        abort(404)
    try:
        changed = B.update_fields(db, g.user, opp, request.form.to_dict(), section)
        flash(f"Saved {len(changed)} change(s)." if changed else "No changes.", "ok")
    except ValidationError as e:
        flash("Not saved: " + "; ".join(f"{k.replace('_', ' ')} — {v}" for k, v in e.errors.items()), "error")
    return _back(opp_id, "requirements")


@bp.route("/<int:opp_id>/preferences", methods=["POST"])
@login_required
def save_preferences(opp_id):
    db = get_db()
    opp = _opp_or_404(db, opp_id)
    return _run(opp_id, lambda: B.set_choice_preferences(db, g.user, opp, request.form.to_dict()),
                "Preferences saved.", "requirements")


@bp.route("/<int:opp_id>/locality", methods=["POST"])
@login_required
def locality(opp_id):
    db = get_db()
    opp = _opp_or_404(db, opp_id)
    f = request.form
    action = f.get("action", "add")
    if action == "add":
        fn = lambda: B.add_locality(db, g.user, opp, f.get("kind"), f.get("city"), f.get("locality"), f.get("note"))  # noqa: E731
    elif action == "remove":
        fn = lambda: B.remove_locality(db, g.user, opp, int(f.get("locality_id") or 0))  # noqa: E731
    elif action in ("up", "down"):
        fn = lambda: B.move_locality(db, g.user, opp, int(f.get("locality_id") or 0), action)  # noqa: E731
    else:
        abort(400)
    return _run(opp_id, fn, "Localities updated.", "requirements")


@bp.route("/<int:opp_id>/funding", methods=["POST"])
@login_required
def funding(opp_id):
    db = get_db()
    opp = _opp_or_404(db, opp_id)
    return _run(opp_id, lambda: B.set_funding_review(db, g.user, opp, request.form.get("status"),
                                                     request.form.get("note")), "Funding review recorded.",
                "qualification")


@bp.route("/<int:opp_id>/confirm-criteria", methods=["POST"])
@login_required
def confirm_criteria(opp_id):
    db = get_db()
    opp = _opp_or_404(db, opp_id)
    return _run(opp_id, lambda: B.confirm_criteria(db, g.user, opp), "Requirements confirmed as effective criteria.",
                "qualification")


@bp.route("/<int:opp_id>/call", methods=["POST"])
@login_required
def call(opp_id):
    db = get_db()
    opp = _opp_or_404(db, opp_id)
    f = request.form

    try:
        B.log_call(db, g.user, opp, f.get("outcome"), f.get("note"),
                   int(f["complete_task_id"]) if f.get("complete_task_id") else None,
                   f.get("next_title"), parse_local_input(f.get("next_due")))
    except ValidationError as e:
        flash(" ".join(e.errors.values()), "error")
        return _back(opp_id)
    flash("Call logged.", "ok")
    # straight to the ready-to-send WhatsApp follow-ups for this stage and outcome
    return redirect(url_for("buyers.detail", opp_id=opp_id, after_call=f.get("outcome")) + "#quick")


@bp.route("/<int:opp_id>/note", methods=["POST"])
@login_required
def note(opp_id):
    db = get_db()
    opp = _opp_or_404(db, opp_id)
    return _run(opp_id, lambda: B.add_note(db, g.user, opp, request.form.get("body")), "Note added.")


@bp.route("/<int:opp_id>/stage", methods=["POST"])
@login_required
def stage(opp_id):
    db = get_db()
    opp = _opp_or_404(db, opp_id)
    f = request.form
    return _run(opp_id, lambda: B.change_stage(db, g.user, opp, f.get("to"), f.get("reason"),
                                               parse_local_input(f.get("follow_up_due"))), "Stage updated.")


@bp.route("/<int:opp_id>/assign", methods=["POST"])
@login_required
def assign(opp_id):
    db = get_db()
    opp = _opp_or_404(db, opp_id)
    return _run(opp_id, lambda: B.assign_owner(db, g.user, opp, int(request.form.get("owner_id") or 0),
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
