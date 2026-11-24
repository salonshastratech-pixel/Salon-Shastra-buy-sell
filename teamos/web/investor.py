"""Buyers & investors screens: screening, investor form, call with Vikas, office visit & registration, report."""
from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from .. import messaging
from ..db import get_db
from ..domain import buyers as B
from ..domain import investor as I
from ..domain import pilot as P
from ..domain.errors import PermissionDenied, ValidationError
from ..permissions import user_can
from ..security import can, deny, login_required, require
from ..timeutil import fmt_ist, parse_local_input
from .pilot import _wa

bp = Blueprint("investor", __name__)


def _err(e):
    return " ".join(str(v) for v in (e.errors.values() if isinstance(e.errors, dict) else [e.errors]))


def _opp(db, opp_id):
    if not can("crm.view"):
        deny("crm.view", "opportunity", opp_id)
    opp = B.load(db, opp_id)
    if not opp or opp["kind"] != "buyer":
        abort(404)
    return opp


def _back(opp_id, anchor=""):
    return redirect(url_for("buyers.detail", opp_id=opp_id, tab="investor") + (("#" + anchor) if anchor else ""))


def _do(opp_id, fn, ok, anchor=""):
    try:
        fn()
        if ok:
            flash(ok, "ok")
    except PermissionDenied:
        abort(403)
    except ValidationError as e:
        flash(_err(e), "error")
    return _back(opp_id, anchor)


@bp.route("/investors")
@require("crm.view")
def index():
    db = get_db()
    v = request.args.get("verdict") or ""
    return render_template("investor/index.html", o=I.overview(db, v), verdict=v, I=I, P=P)


@bp.route("/buyers/<int:opp_id>/investor/screen", methods=["POST"])
@login_required
def screen(opp_id):
    db = get_db()
    opp = _opp(db, opp_id)
    try:
        res = I.save_screening(db, g.user, opp, request.form)
        flash(f"Screening saved: {I.VERDICTS[res['verdict']][0]} ({res['score']}/100). {I.VERDICTS[res['verdict']][2]}",
              "ok")
    except PermissionDenied:
        abort(403)
    except ValidationError as e:
        flash(_err(e), "error")
    return _back(opp_id, "screen")


@bp.route("/buyers/<int:opp_id>/investor/form-status", methods=["POST"])
@login_required
def form_status(opp_id):
    db = get_db()
    opp = _opp(db, opp_id)
    st = request.form.get("status") or ""
    return _do(opp_id, lambda: I.set_form_status(db, g.user, opp, st),
               f"Investor form marked: {P.FORM_STATUS_LABELS.get(st, st)}.", "form")


@bp.route("/buyers/<int:opp_id>/investor/link", methods=["POST"])
@login_required
def link(opp_id):
    db = get_db()
    opp = _opp(db, opp_id)
    purpose = request.form.get("purpose") or "form"
    if purpose == "report" and not I.latest_report(db, opp_id, approved_only=True):
        flash("Approve the readiness report before sending it.", "error")
        return _back(opp_id, "report")
    try:
        token = P.create_link(db, g.user, opp)
    except PermissionDenied:
        abort(403)
    except ValidationError as e:
        flash(_err(e), "error")
        return _back(opp_id)
    base = messaging.public_base(request)
    if purpose == "report":
        url = f"{base}/s/{token}/report"
        text = I.report_message(opp, url)
    else:
        url = f"{base}/s/{token}"
        text = I.form_message(opp, url)
        if I.profile(db, opp_id)["form_status"] in ("not_sent", "not_filled"):
            I.set_form_status(db, g.user, opp, "sent")
    return render_template("investor/link_ready.html", opp=opp, url=url, text=text, purpose=purpose,
                           wa=_wa(opp.get("phone_normalized"), text))


@bp.route("/buyers/<int:opp_id>/investor/fill", methods=["GET", "POST"])
@login_required
def fill(opp_id):
    db = get_db()
    opp = _opp(db, opp_id)
    if not user_can(db, g.user, "crm.edit"):
        abort(403)
    answers, errors = I.prefill(db, opp), {}
    if request.method == "POST":
        answers, errors = I.parse_answers(request.form)
        if not errors:
            I.save_response(db, opp, answers, "staff", g.user["id"])
            flash("Investor form saved — a readiness report draft is ready. Next: the call with Vikas.", "ok")
            return _back(opp_id, "vikas")
    return render_template("investor/fill.html", opp=opp, answers=answers, errors=errors, I=I)


@bp.route("/buyers/<int:opp_id>/investor/answers")
@login_required
def answers(opp_id):
    db = get_db()
    opp = _opp(db, opp_id)
    return render_template("investor/answers.html", opp=opp, resp=I.latest_response(db, opp_id), I=I)


@bp.route("/buyers/<int:opp_id>/investor/book", methods=["POST"])
@login_required
def book(opp_id):
    db = get_db()
    opp = _opp(db, opp_id)
    if not user_can(db, g.user, "crm.log_activity"):
        abort(403)
    slot = request.form.get("slot_id")
    return _do(opp_id, lambda: P.book(db, opp, int(slot) if slot and slot.isdigit() else None,
                                      request.form.get("preferred"), "staff", g.user["id"], request.form.get("note")),
               f"Call with {P.CONSULTANT_NAME} booked — he has been alerted.", "vikas")


@bp.route("/buyers/<int:opp_id>/investor/booking/<int:bid>", methods=["POST"])
@login_required
def booking(opp_id, bid):
    db = get_db()
    opp = _opp(db, opp_id)
    return _do(opp_id, lambda: P.update_booking(db, g.user, opp, bid, request.form.get("status") or "",
                                                request.form.get("outcome"),
                                                parse_local_input(request.form.get("starts_at"))),
               "Call updated.", "vikas")


@bp.route("/buyers/<int:opp_id>/investor/office", methods=["POST"])
@login_required
def office(opp_id):
    db = get_db()
    opp = _opp(db, opp_id)
    return _do(opp_id, lambda: I.save_office(db, g.user, opp, request.form), "Saved.", "office")


@bp.route("/buyers/<int:opp_id>/investor/report/<int:rid>", methods=["POST"])
@login_required
def save_report(opp_id, rid):
    db = get_db()
    opp = _opp(db, opp_id)
    approve = request.form.get("action") == "approve"
    return _do(opp_id, lambda: I.save_report(db, g.user, opp, rid, request.form, approve),
               "Report approved — download it or send it." if approve else "Report saved.", "report")


@bp.route("/buyers/<int:opp_id>/investor/redraft", methods=["POST"])
@login_required
def redraft(opp_id):
    db = get_db()
    opp = _opp(db, opp_id)
    return _do(opp_id, lambda: I.redraft(db, g.user, opp), "A fresh draft was made from the latest answers.", "report")


@bp.route("/buyers/<int:opp_id>/investor/report.pdf")
@login_required
def report_pdf(opp_id):
    db = get_db()
    _opp(db, opp_id)
    rep = I.latest_report(db, opp_id)
    if not rep:
        abort(404)
    return investor_pdf(rep, draft=rep["status"] != "approved")


@bp.route("/buyers/<int:opp_id>/investor/sent", methods=["POST"])
@login_required
def sent(opp_id):
    db = get_db()
    opp = _opp(db, opp_id)
    return _do(opp_id, lambda: I.mark_sent(db, g.user, opp), "Marked as sent.", "report")


def investor_pdf(rep, draft=False):
    from flask import Response

    from ..domain import swot_pdf
    data = swot_pdf.build_investor({"c": rep["c"], "date": fmt_ist(rep["approved_at"] or rep["created_at"],
                                                                     "%d %b %Y")})
    name = "".join(ch if ch.isalnum() else "-" for ch in (rep["c"].get("name") or "investor"))[:40].strip("-")
    return Response(data, mimetype="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="Investor-readiness-{name}'
                                                    f'{"-DRAFT" if draft else ""}.pdf"', "Cache-Control": "no-store"})


@bp.app_context_processor
def _investor_ctx():
    if (request.endpoint or "") != "buyers.detail" or not g.get("user"):
        return {}
    try:
        opp_id = int((request.view_args or {}).get("opp_id"))
        ctx = I.record_context(get_db(), opp_id)
        ctx.update(I=I, P=P)
        return ctx
    except Exception:  # noqa: BLE001 — a side card must never break the record page
        return {}
