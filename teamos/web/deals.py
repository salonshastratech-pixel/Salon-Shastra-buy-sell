"""Deals, teasers and introductions (Milestone 6)."""
import json

from flask import (Blueprint, abort, current_app, flash, g, make_response, redirect, render_template, request,
                   url_for)

from .. import messaging
from ..db import get_db
from ..domain import deals as D
from ..domain import teasers as TZ
from ..domain.errors import ValidationError
from ..permissions import user_can
from ..security import login_required, require
from ..timeutil import parse_local_input
from .sellers import _active_users

bp = Blueprint("deals", __name__, url_prefix="/deals")
teaser_public = Blueprint("public_teaser", __name__, url_prefix="/t")


def _msg(e):
    return " ".join(str(v) for v in e.errors.values())


def _run(fn, ok, back):
    try:
        fn()
        flash(ok, "ok")
    except ValidationError as e:
        flash(_msg(e), "error")
    return redirect(back)


# -- Deal room ---------------------------------------------------------------------------
@bp.route("/")
@require("crm.view")
def index():
    db = get_db()
    view = request.args.get("view", "open")
    cond = {"open": "d.stage NOT IN ('closed_won','closed_lost','withdrawn')", "won": "d.stage = 'closed_won'",
            "lost": "d.stage IN ('closed_lost','withdrawn')", "all": "1 = 1"}.get(view, "1 = 1")
    rows = db.all(D.DEAL_SELECT + f"WHERE {cond} ORDER BY d.updated_at DESC")
    for r in rows:
        r["next"] = db.one("SELECT title, due_at FROM tasks WHERE entity_type = 'deal' AND entity_id = ? AND status = "
                           "'open' ORDER BY due_at LIMIT 1", (r["id"],))
    pending = db.all("SELECT i.*, mc.buyer_opp_id, mc.seller_opp_id, bo.ref_code AS buyer_ref, so.ref_code AS seller_ref "
                     "FROM introductions i JOIN match_candidates mc ON mc.id = i.match_id "
                     "JOIN opportunities bo ON bo.id = mc.buyer_opp_id JOIN opportunities so ON so.id = mc.seller_opp_id "
                     "WHERE i.status = 'requested' ORDER BY i.requested_at")
    return render_template("deals/index.html", rows=rows, view=view, D=D, pending=pending)


@bp.route("/<int:deal_id>")
@require("crm.view")
def detail(deal_id):
    db = get_db()
    d = D.load(db, deal_id)
    if not d:
        abort(404)
    events = db.all("SELECT e.*, u.full_name AS actor_name FROM deal_events e LEFT JOIN users u ON u.id = e.actor_id "
                    "WHERE deal_id = ? ORDER BY e.at DESC, e.id DESC", (deal_id,))
    tasks = db.all("SELECT t.*, u.full_name AS assignee_name FROM tasks t LEFT JOIN users u ON u.id = t.assignee_id "
                   "WHERE entity_type = 'deal' AND entity_id = ? AND status = 'open' ORDER BY due_at", (deal_id,))
    intro = db.one("SELECT * FROM introductions WHERE match_id = ? AND status = 'approved'", (d["match_id"],))
    return render_template("deals/detail.html", d=d, D=D, events=events, tasks=tasks, visits=D.visits(db, deal_id),
                           offers=D.offers(db, deal_id), checklist=D.checklist(db, deal_id),
                           commercial=D.commercial_status(db, deal_id),
                           can_finance=user_can(db, g.user, "finance.view"),
                           can_close=user_can(db, g.user, "listing.approve"), users=_active_users(db),
                           shared=json.loads(intro["details_shared"]) if intro and intro["details_shared"] else None,
                           blockers={k: D.transition_blockers(db, g.user, d, k) for k, _ in D.STAGES + D.OTHER})


def _deal_back(deal_id):
    return url_for("deals.detail", deal_id=deal_id)


@bp.route("/<int:deal_id>/stage", methods=["POST"])
@login_required
def stage(deal_id):
    db = get_db()
    f = request.form
    return _run(lambda: D.change_stage(db, g.user, deal_id, f.get("to"), f.get("note"), f.get("reason"), f.get("lessons")),
                "Deal updated.", _deal_back(deal_id))


@bp.route("/<int:deal_id>/visit", methods=["POST"])
@login_required
def visit(deal_id):
    db = get_db()
    f = request.form
    return _run(lambda: D.schedule_visit(db, g.user, deal_id, parse_local_input(f.get("when")), f.get("attendees")),
                "Visit scheduled — it is on the owner's My Day.", _deal_back(deal_id))


@bp.route("/visits/<int:visit_id>", methods=["POST"])
@login_required
def visit_outcome(visit_id):
    db = get_db()
    v = db.one("SELECT deal_id FROM deal_visits WHERE id = ?", (visit_id,))
    if not v:
        abort(404)
    f = request.form
    return _run(lambda: D.record_visit(db, g.user, visit_id, f.get("status"), f.get("outcome"), f.get("feedback")),
                "Visit updated.", _deal_back(v["deal_id"]))


@bp.route("/<int:deal_id>/offer", methods=["POST"])
@login_required
def offer(deal_id):
    db = get_db()
    return _run(lambda: D.add_offer(db, g.user, deal_id, request.form.to_dict()),
                "Offer recorded as a new version.", _deal_back(deal_id))


@bp.route("/offers/<int:offer_id>", methods=["POST"])
@login_required
def offer_status(offer_id):
    db = get_db()
    o = db.one("SELECT deal_id FROM deal_offers WHERE id = ?", (offer_id,))
    if not o:
        abort(404)
    return _run(lambda: D.set_offer_status(db, g.user, offer_id, request.form.get("status")), "Offer updated.",
                _deal_back(o["deal_id"]))


@bp.route("/<int:deal_id>/check", methods=["POST"])
@login_required
def check(deal_id):
    db = get_db()
    f = request.form
    return _run(lambda: D.set_check(db, g.user, deal_id, f.get("item_key"), f.get("status"), f.get("note")),
                "Checklist updated.", _deal_back(deal_id) + "#diligence")


@bp.route("/<int:deal_id>/dates", methods=["POST"])
@login_required
def dates(deal_id):
    db = get_db()
    f = request.form
    return _run(lambda: D.set_dates(db, g.user, deal_id, f.get("agreement_date"), f.get("handover_date")),
                "Dates saved.", _deal_back(deal_id))


@bp.route("/<int:deal_id>/note", methods=["POST"])
@login_required
def note(deal_id):
    db = get_db()
    return _run(lambda: D.add_note(db, g.user, deal_id, request.form.get("body")), "Note added.", _deal_back(deal_id))


# -- Teasers (seller side) -------------------------------------------------------------------
@bp.route("/teasers/seller/<int:opp_id>", methods=["POST"])
@login_required
def teaser_save(opp_id):
    db = get_db()
    f = request.form
    return _run(lambda: TZ.save_draft(db, g.user, opp_id, f.getlist("fields"), f.get("headline"), f.get("highlights")),
                "Teaser draft saved. Next: get the seller's approval of this content.",
                url_for("sellers.detail", opp_id=opp_id, tab="teaser"))


@bp.route("/teasers/<int:teaser_id>/<action>", methods=["POST"])
@login_required
def teaser_action(teaser_id, action):
    db = get_db()
    t = db.one("SELECT seller_opp_id FROM teasers WHERE id = ?", (teaser_id,))
    if not t:
        abort(404)
    back = url_for("sellers.detail", opp_id=t["seller_opp_id"], tab="teaser")
    if action == "seller-approval":
        return _run(lambda: TZ.record_seller_approval(db, g.user, teaser_id, request.form.get("method"),
                                                      request.form.get("note")),
                    "Seller approval recorded.", back)
    if action == "approve":
        return _run(lambda: TZ.approve(db, g.user, teaser_id), "Teaser approved — it can now be shared with buyers "
                                                                "approved to approach.", back)
    abort(404)


# -- Match → teaser share, identity consent, introduction ----------------------------------
@bp.route("/match/<int:cid>/share-teaser", methods=["POST"])
@login_required
def share_teaser(cid):
    db = get_db()
    base = current_app.config.get("PUBLIC_BASE_URL") or request.host_url.rstrip("/")
    try:
        r = TZ.share_with_buyer(db, g.user, cid, request.form.get("channel") or "copy_link", link_base=base)
    except ValidationError as e:
        flash(_msg(e), "error")
        return redirect(url_for("matches.detail", cid=cid))
    comm = db.one("SELECT * FROM messages WHERE id = ?", (r["communication"],)) if r["communication"] else None
    return render_template("deals/teaser_sent.html", link=r["link"], comm=comm, cid=cid,
                           MOCK_LABEL=messaging.PROVIDER_LABEL)


@bp.route("/match/<int:cid>/consent", methods=["POST"])
@login_required
def identity_consent(cid):
    db = get_db()
    f = request.form
    return _run(lambda: TZ.record_identity_consent(db, g.user, cid, f.get("side"), f.get("method"), f.get("note")),
                "Consent recorded.", url_for("matches.detail", cid=cid) + "#intro")


@bp.route("/match/<int:cid>/introduce", methods=["POST"])
@login_required
def request_intro(cid):
    db = get_db()
    return _run(lambda: TZ.request_introduction(db, g.user, cid, request.form.get("note")),
                "Introduction requested — an approver has been notified.", url_for("matches.detail", cid=cid) + "#intro")


@bp.route("/introductions/<int:intro_id>", methods=["POST"])
@login_required
def decide_intro(intro_id):
    db = get_db()
    intro = db.one("SELECT match_id FROM introductions WHERE id = ?", (intro_id,))
    if not intro:
        abort(404)
    try:
        deal_id = TZ.decide_introduction(db, g.user, intro_id, request.form.get("decision"), request.form.get("note"))
    except ValidationError as e:
        flash(_msg(e), "error")
        return redirect(url_for("matches.detail", cid=intro["match_id"]) + "#intro")
    if deal_id:
        flash("Introduction approved. Contact details are exchanged and the deal room is open.", "ok")
        return redirect(url_for("deals.detail", deal_id=deal_id))
    flash("Introduction rejected.", "ok")
    return redirect(url_for("matches.detail", cid=intro["match_id"]))


# -- Public teaser link (no login; only frozen, approved, anonymised content) ---------------
@teaser_public.route("/<token>")
def view(token):
    db = get_db()
    s = TZ.open_share(db, token)
    if not s:
        resp = make_response(render_template("public/message.html", title="Link not available",
                                             message="This summary link has expired or is not valid. Please ask your "
                                                     "Salon Shastra contact."), 404)
    else:
        resp = make_response(render_template("public/teaser.html", c=s["content_d"], version=s["version"],
                                             expires=s["expires_at"]))
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    return resp
