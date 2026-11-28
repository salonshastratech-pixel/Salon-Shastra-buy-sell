"""Match Desk (PRD §8.5, §11.3; Milestone 5): queues, candidate grid, 360° comparison and one-click actions."""
from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, url_for

from .. import messaging
from ..db import get_db
from ..domain import forms as F
from ..domain import matching as M
from ..domain import tasks as T
from ..domain.errors import ValidationError
from ..permissions import user_can
from ..security import login_required, require, safe_next
from ..timeutil import parse_local_input
from .sellers import _active_users

bp = Blueprint("matches", __name__, url_prefix="/matches")


def _msg(e: ValidationError) -> str:
    return " ".join(str(v) for v in e.errors.values())


@bp.route("/")
@require("crm.view")
def desk():
    db = get_db()
    stale = _stale_check_in_background(db)
    queue = request.args.get("queue") or request.args.get("q") or "new"
    if queue not in M.QUEUES:
        queue = "new"
    buyer = request.args.get("buyer") or ""
    seller = request.args.get("seller") or ""
    mine = request.args.get("mine") == "1"
    min_fit = request.args.get("min_fit") or ""
    where, params = [M.ACTIVE_WHERE, M.QUEUES[queue][1]], []
    if buyer.isdigit():
        where.append("mc.buyer_opp_id = ?")
        params.append(int(buyer))
    if seller.isdigit():
        where.append("mc.seller_opp_id = ?")
        params.append(int(seller))
    if mine:
        where.append("(bo.primary_owner_id = ? OR so.primary_owner_id = ? OR mc.match_owner_id = ?)")
        params += [g.user["id"]] * 3
    if min_fit.isdigit():
        where.append("mc.fit_score >= ?")
        params.append(int(min_fit))
    rows = db.all(M.CAND_SELECT_LIGHT + "WHERE " + " AND ".join(f"({w})" for w in where) +
                  " ORDER BY mc.rank_score DESC LIMIT 200", params)
    M.attach_open_tasks(db, rows)
    counts = M.queue_counts(db, " AND (bo.primary_owner_id = ? OR so.primary_owner_id = ? OR mc.match_owner_id = ?)"
                            if mine else "", [g.user["id"]] * 3 if mine else [])
    buyers = db.all("SELECT o.id, o.ref_code, c.full_name FROM opportunities o JOIN contacts c ON c.id = o.contact_id "
                    "WHERE o.kind = 'buyer' AND o.status = 'active' ORDER BY o.ref_code")
    sellers = db.all("SELECT o.id, o.ref_code, b.name FROM opportunities o JOIN salon_businesses b ON b.id = o.business_id "
                     "WHERE o.kind = 'seller' AND o.status = 'active' ORDER BY o.ref_code")
    last = db.scalar("SELECT MAX(computed_at) FROM match_candidates")
    return render_template("matches/desk.html", rows=rows, queue=queue, counts=counts, buyers=buyers, sellers=sellers,
                           buyer=buyer, seller=seller, mine=mine, min_fit=min_fit, stale=stale, last=last, M=M)


@bp.route("/recompute", methods=["POST"])
@require("crm.view")
def recompute():
    db = get_db()
    f = request.form
    st = M.refresh(db, buyer_id=int(f["buyer"]) if f.get("buyer", "").isdigit() else None,
                   seller_id=int(f["seller"]) if f.get("seller", "").isdigit() else None,
                   force=f.get("force") == "1", actor_id=g.user["id"])
    flash(f"Matches refreshed: {st['evaluated']} evaluated ({st['new']} new), {st['unchanged']} unchanged.", "ok")
    return redirect(safe_next(request.referrer, url_for("matches.desk")))


@bp.route("/<int:cid>")
@require("crm.view")
def detail(cid):
    db = get_db()
    c = db.one(M.CAND_SELECT + "WHERE mc.id = ?", (cid,))
    if not c:
        abort(404)
    M.mark_stale(db, only=(c["buyer_opp_id"], c["seller_opp_id"]))   # just this pair: fast
    c = db.one(M.CAND_SELECT + "WHERE mc.id = ?", (cid,))
    r = M.latest_result(db, cid)
    families = []
    for fam, (label, weight) in M.FAMILIES.items():
        fs = [f for f in r.get("factors", []) if f["family"] == fam]
        app = [f for f in fs if f["result"] != "na"]
        families.append({"key": fam, "label": label, "weight": weight, "factors": fs,
                         "earned": round(sum(f["earned"] for f in app if f["result"] != "unknown"), 1),
                         "possible": round(sum(f["weight"] for f in app if f["result"] != "unknown"), 1)})
    events = db.all("SELECT e.*, u.full_name AS actor_name FROM match_events e LEFT JOIN users u ON u.id = e.actor_id "
                    "WHERE candidate_id = ? ORDER BY e.at DESC, e.id DESC", (cid,))
    snaps = db.all("SELECT id, trigger, rules_version, buyer_version, seller_version, computed_at FROM match_snapshots "
                   "WHERE candidate_id = ? ORDER BY id DESC LIMIT 10", (cid,))
    questions = r.get("questions", [])
    from ..domain import teasers as TZ
    intro_ctx = {
        "teaser": TZ.approved_teaser(db, c["seller_opp_id"]),
        "shares": db.all("SELECT s.*, u.full_name AS by_name FROM teaser_shares s LEFT JOIN users u ON u.id = s.shared_by "
                         "WHERE match_id = ? ORDER BY s.id DESC", (cid,)),
        "intro": db.one("SELECT i.*, u.full_name AS requested_name, d.full_name AS decided_name FROM introductions i "
                        "LEFT JOIN users u ON u.id = i.requested_by LEFT JOIN users d ON d.id = i.decided_by "
                        "WHERE match_id = ? ORDER BY i.id DESC LIMIT 1", (cid,)),
        "intro_blockers": TZ.intro_blockers(db, cid) if c["stage"] in ("approved_to_approach", "interest_confirmed") else [],
        "identity": {side: TZ._latest_consent(db, db.scalar("SELECT contact_id FROM opportunities WHERE id = ?", (oid,)),
                                              "share_identity")
                     for side, oid in (("buyer", c["buyer_opp_id"]), ("seller", c["seller_opp_id"]))},
        "deal": db.one("SELECT id, ref_code, stage FROM deals WHERE match_id = ?", (cid,)),
        "can_intro": user_can(db, g.user, "listing.approve"),
    }
    return render_template("matches/detail.html", **intro_ctx, c=c, r=r, families=families, events=events, snaps=snaps,
                           buyer_q=[q for q in questions if q["side"] == "buyer"],
                           seller_q=[q for q in questions if q["side"] == "seller"],
                           tasks=M.subtask_progress(db, cid), users=_active_users(db), M=M,
                           can_approve=user_can(db, g.user, "evidence.review"),
                           MOCK_LABEL=messaging.PROVIDER_LABEL)


def _back(cid):
    return redirect(url_for("matches.detail", cid=cid))


@bp.route("/<int:cid>/stage", methods=["POST"])
@login_required
def stage(cid):
    db = get_db()
    f = request.form
    try:
        M.change_stage(db, g.user, cid, f.get("to"), f.get("reason"), f.get("note"),
                       parse_local_input(f.get("revisit_on")))
        flash(f"Match moved to {M.STAGES[f.get('to')]}.", "ok")
    except ValidationError as e:
        flash(_msg(e), "error")
    back = request.form.get("return_to") or ""
    return redirect(safe_next(back, url_for("matches.detail", cid=cid)))


@bp.route("/<int:cid>/tasks", methods=["POST"])
@login_required
def tasks(cid):
    db = get_db()
    f = request.form
    try:
        if (f.get("buyer_task") or "").strip() or (f.get("seller_task") or "").strip():
            T.need_when(parse_local_input(f.get("due_at")))
        M.create_subtasks(db, g.user, cid, f.get("buyer_task"), f.get("seller_task"), parse_local_input(f.get("due_at")))
        flash("Tasks created — each side's owner sees theirs on My Day; the shared match task closes when both are done.",
              "ok")
    except ValidationError as e:
        flash(_msg(e), "error")
    return _back(cid)


@bp.route("/<int:cid>/ask/<side>", methods=["POST"])
@login_required
def ask(cid, side):
    """Request missing answers from one side with a targeted form (reuses Milestone 4)."""
    db = get_db()
    if side not in ("buyer", "seller"):
        abort(404)
    c = db.one(M.CAND_SELECT + "WHERE mc.id = ?", (cid,))
    if not c:
        abort(404)
    questions = [q for q in M.latest_result(db, cid).get("questions", []) if q["side"] == side]
    fields = list(dict.fromkeys(k for q in questions for k in q["fields"]))
    if request.form.getlist("fields"):
        fields = [k for k in request.form.getlist("fields") if k in fields]
    opp_id = c["buyer_opp_id"] if side == "buyer" else c["seller_opp_id"]
    if not fields:
        talk = "; ".join(q["text"] for q in questions[:4]) or "nothing is open on this side"
        flash(f"Nothing for the {side} can go in a form here — these need a quick call: {talk}.", "warn")
        return _back(cid)
    base = __import__("teamos.messaging", fromlist=["public_base"]).public_base(request)
    try:
        res = F.create_invitation(db, g.user, opp_id, "targeted", request.form.get("channel") or "copy_link", fields, [],
                                  request.form.get("purpose") or "A few details so we can check a possible match.",
                                  link_base=base)
    except ValidationError as e:
        flash(_msg(e), "error")
        return _back(cid)
    M._event(db, cid, "form_sent", g.user["id"], body=f"Asked the {side} for: " + ", ".join(fields))
    opp = F.load_opportunity(db, opp_id)
    inv = db.one("SELECT * FROM form_invitations WHERE id = ?", (res["invitation_id"],))
    comm = db.one("SELECT * FROM messages WHERE id = ?", (res["communication"]["id"],)) \
        if res["communication"] and res["communication"].get("id") else None
    return render_template("forms/sent.html", opp=opp, inv=inv, link=res["link"], comm=comm,
                           back=url_for("matches.detail", cid=cid), MOCK_LABEL=messaging.PROVIDER_LABEL)


@bp.route("/<int:cid>/note", methods=["POST"])
@login_required
def note(cid):
    db = get_db()
    try:
        M.add_note(db, g.user, cid, request.form.get("body"))
        flash("Note added.", "ok")
    except ValidationError as e:
        flash(_msg(e), "error")
    return _back(cid)


@bp.route("/<int:cid>/owner", methods=["POST"])
@login_required
def owner(cid):
    db = get_db()
    try:
        M.set_owner(db, g.user, cid, int(request.form.get("owner_id") or 0) or None)
        flash("Match co-ordinator updated.", "ok")
    except ValidationError as e:
        flash(_msg(e), "error")
    return _back(cid)


@bp.route("/<int:cid>/recompute", methods=["POST"])
@require("crm.view")
def recompute_one(cid):
    db = get_db()
    c = db.one("SELECT * FROM match_candidates WHERE id = ?", (cid,))
    if not c:
        abort(404)
    M.refresh(db, buyer_id=c["buyer_opp_id"], seller_id=c["seller_opp_id"], force=True, actor_id=g.user["id"])
    flash("Recomputed with the latest data. The previous result is kept in the history.", "ok")
    return _back(cid)


def _stale_check_in_background(db) -> int:
    """The Match Desk shows the stored count at once; the full staleness check runs off the request (at most once a
    10 minutes per process; the timer's jobs run it too)."""
    import time
    from .. import background
    if time.time() - M._LAST_STALE_CHECK["at"] >= 600:   # heavy (loads every matched record): every 10 minutes
        M._LAST_STALE_CHECK["at"] = time.time()
        background.run(current_app._get_current_object(), "mark_stale", M.mark_stale)
    return db.scalar("SELECT COUNT(*) FROM match_candidates WHERE stale = 1") or 0


def _rescore(db, kind, opp_id):
    M.refresh(db, **({"buyer_id": opp_id} if kind == "buyer" else {"seller_id": opp_id}))


def record_matches(db, opp: dict) -> dict:
    """Context for the Matches tab on buyer and seller pages (UAT-37 / UAT-38). Shows the stored matches at once; when
    this record's facts changed since they were scored, re-scoring runs in the background (re-scoring one record
    against every counterpart takes many queries — far too slow to make the person wait)."""
    from .. import background
    side_col = "buyer_opp_id" if opp["kind"] == "buyer" else "seller_opp_id"
    fp_col = "buyer_fp" if opp["kind"] == "buyer" else "seller_fp"
    rec = M.load_buyer(db, opp["id"]) if opp["kind"] == "buyer" else M.load_seller(db, opp["id"])
    stored = {r[fp_col] for r in db.all(f"SELECT DISTINCT {fp_col} FROM match_candidates WHERE {side_col} = ?",
                                        (opp["id"],))}
    updating = False
    if rec and (not stored or stored != {rec["fp"]}) and not M.already_scored(db, opp["id"], rec["fp"]):
        updating = background.run(current_app._get_current_object(), "rescore", _rescore, opp["kind"],  # one at a time
                                  opp["id"]) and not current_app.config.get("TESTING")
    rows = db.all(M.CAND_SELECT + f"WHERE mc.{side_col} = ? AND " + M.ACTIVE_WHERE +
                  "ORDER BY mc.suppressed, mc.rank_score DESC", (opp["id"],))
    return {"match_rows": rows, "M": M, "matches_updating": updating}
