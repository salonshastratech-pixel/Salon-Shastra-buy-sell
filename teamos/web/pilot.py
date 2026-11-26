"""Salon Owners pilot program screens: needs, pilot form, SWOT report + PDF, Vikas bookings, consultations."""
from urllib.parse import quote

from flask import Blueprint, Response, abort, flash, g, redirect, render_template, request, url_for

from .. import i18n, messaging
from ..db import get_db
from ..domain import pilot as P
from ..domain import sellers as S
from ..domain import swot_pdf
from ..domain.errors import PermissionDenied, ValidationError
from ..permissions import user_can
from ..security import can, deny, login_required, require
from ..timeutil import fmt_ist, now_iso, parse_local_input

bp = Blueprint("pilot", __name__)
public_bp = Blueprint("public_pilot", __name__)


def _err(e: ValidationError) -> str:
    return " ".join(str(v) for v in (e.errors.values() if isinstance(e.errors, dict) else [e.errors]))


def _opp(db, opp_id):
    if not can("crm.view"):
        deny("crm.view", "opportunity", opp_id)
    opp = S.load(db, opp_id)
    if not opp or opp["kind"] != "seller":
        abort(404)
    return opp


def _back(opp_id, anchor=""):
    return redirect(url_for("sellers.detail", opp_id=opp_id, tab="pilot") + (("#" + anchor) if anchor else ""))


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


def _wa(phone: str | None, text: str) -> str:
    digits = "".join(ch for ch in (phone or "") if ch.isdigit())
    return f"https://wa.me/{digits}?text={quote(text)}" if digits else f"https://wa.me/?text={quote(text)}"


# -- Overview -----------------------------------------------------------------------------------------------------
@bp.route("/pilot")
@require("crm.view")
def index():
    db = get_db()
    area, status = request.args.get("area") or "", request.args.get("status") or ""
    return render_template("pilot/index.html", o=P.overview(db, area, status), area=area, status=status, P=P,
                           consultant=P.consultant(db),
                           users=db.all("SELECT id, full_name FROM users WHERE is_active = 1 ORDER BY full_name"),
                           can_settings=user_can(db, g.user, "settings.manage"))


@bp.route("/pilot/consultant", methods=["POST"])
@login_required
def set_consultant():
    try:
        P.set_consultant(get_db(), g.user, int(request.form.get("user_id") or 0))
        flash("Saved. Booking alerts now go to this person.", "ok")
    except PermissionDenied:
        abort(403)
    except (ValidationError, ValueError) as e:
        flash(_err(e) if isinstance(e, ValidationError) else "Choose a person.", "error")
    return redirect(url_for("pilot.index"))


@bp.route("/pilot/vikas", methods=["GET", "POST"])
@login_required
def vikas():
    db = get_db()
    if not P.can_manage_slots(db, g.user) and not can("crm.view"):
        abort(403)
    if request.method == "POST":
        try:
            if request.form.get("cancel_slot"):
                P.cancel_slot(db, g.user, int(request.form["cancel_slot"]))
                flash("Slot removed.", "ok")
            else:
                times = [t.strip() for t in (request.form.get("times") or "").replace(",", " ").split() if t.strip()]
                n = P.add_slots(db, g.user, request.form.get("day"), times, 0, request.form.get("mode") or "phone")
                skipped = len(times) - n
                flash(f"{n} slot(s) added. Salon owners can now pick them from their booking link."
                      + (f" {skipped} skipped — in the past, already listed, or overlapping another appointment."
                         if skipped > 0 else ""), "ok")
        except PermissionDenied:
            abort(403)
        except (ValidationError, ValueError) as e:
            flash(_err(e) if isinstance(e, ValidationError) else "Check the values.", "error")
        return redirect(url_for("pilot.vikas"))
    slots = db.all("SELECT s.*, vb.opportunity_id, o.ref_code, o.kind, c.full_name AS contact_name, b.name AS salon_name "
                   "FROM vikas_slots s LEFT JOIN vikas_bookings vb ON vb.slot_id = s.id AND vb.status <> 'cancelled' "
                   "LEFT JOIN opportunities o ON o.id = vb.opportunity_id LEFT JOIN contacts c ON c.id = o.contact_id "
                   "LEFT JOIN salon_businesses b ON b.id = o.business_id "
                   "WHERE s.status <> 'cancelled' AND s.starts_at > ? ORDER BY s.starts_at LIMIT 200", (now_iso(),))
    requested = db.all("SELECT vb.*, o.ref_code, o.kind, c.full_name AS contact_name, b.name AS salon_name FROM vikas_bookings "
                       "vb JOIN opportunities o ON o.id = vb.opportunity_id JOIN contacts c ON c.id = o.contact_id "
                       "LEFT JOIN salon_businesses b ON b.id = o.business_id WHERE vb.status = 'requested' "
                       "ORDER BY vb.created_at")
    from ..domain import appointments as AP
    return render_template("pilot/vikas.html", slots=slots, requested=requested, consultant=P.consultant(db),
                           appointments=AP.upcoming(db),
                           can_manage=P.can_manage_slots(db, g.user), P=P)


# -- Record actions -------------------------------------------------------------------------------------------------
@bp.route("/sellers/<int:opp_id>/pilot/needs", methods=["POST"])
@login_required
def needs(opp_id):
    db = get_db()
    opp = _opp(db, opp_id)
    return _do(opp_id, lambda: P.save_needs(db, g.user, opp, request.form), "Saved what they need help with.", "needs")


@bp.route("/sellers/<int:opp_id>/pilot/form-status", methods=["POST"])
@login_required
def form_status(opp_id):
    db = get_db()
    opp = _opp(db, opp_id)
    st = request.form.get("status") or ""
    return _do(opp_id, lambda: P.set_form_status(db, g.user, opp, st),
               f"Pilot form marked: {P.FORM_STATUS_LABELS.get(st, st)}.", "form")


@bp.route("/sellers/<int:opp_id>/pilot/link", methods=["POST"])
@login_required
def link(opp_id):
    """Make a private link and show the ready-to-send WhatsApp message (form or report)."""
    db = get_db()
    opp = _opp(db, opp_id)
    purpose = request.form.get("purpose") or "form"
    if purpose == "report" and not P.latest_report(db, opp_id, approved_only=True):
        flash("Approve the SWOT report before sending it.", "error")
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
        text = P.report_message(opp, url)
    else:
        url = f"{base}/s/{token}"
        text = P.form_message(opp, url)
        if P.profile(db, opp_id)["form_status"] in ("not_sent", "not_filled"):
            P.set_form_status(db, g.user, opp, "sent")
    return render_template("pilot/link_ready.html", opp=opp, url=url, text=text, purpose=purpose,
                           wa=_wa(opp.get("phone_normalized"), text), P=P)


@bp.route("/sellers/<int:opp_id>/pilot/links/off", methods=["POST"])
@login_required
def links_off(opp_id):
    db = get_db()
    opp = _opp(db, opp_id)
    return _do(opp_id, lambda: P.revoke_links(db, g.user, opp), "All links for this salon switched off.")


@bp.route("/sellers/<int:opp_id>/pilot/fill", methods=["GET", "POST"])
@login_required
def fill(opp_id):
    """The team fills the pilot form on the owner's behalf (e.g. during the call)."""
    db = get_db()
    opp = _opp(db, opp_id)
    if not user_can(db, g.user, "crm.edit"):
        abort(403)
    answers, errors = P.prefill(db, opp), {}
    if request.method == "POST":
        answers, errors = P.parse_answers(request.form)
        if not errors:
            P.save_response(db, opp, answers, "staff", g.user["id"])
            flash("Pilot form saved and a SWOT report draft is ready below.", "ok")
            return _back(opp_id, "report")
    return render_template("pilot/fill.html", opp=opp, answers=answers, errors=errors, P=P)


@bp.route("/sellers/<int:opp_id>/pilot/answers")
@login_required
def answers(opp_id):
    db = get_db()
    opp = _opp(db, opp_id)
    return render_template("pilot/answers.html", opp=opp, resp=P.latest_response(db, opp_id), P=P)


@bp.route("/sellers/<int:opp_id>/pilot/report/<int:rid>", methods=["POST"])
@login_required
def save_report(opp_id, rid):
    db = get_db()
    opp = _opp(db, opp_id)
    approve = request.form.get("action") == "approve"
    return _do(opp_id, lambda: P.save_report(db, g.user, opp, rid, request.form, approve),
               "Report approved — download the PDF or send it." if approve else "Report saved.", "report")


@bp.route("/sellers/<int:opp_id>/pilot/redraft", methods=["POST"])
@login_required
def redraft(opp_id):
    db = get_db()
    opp = _opp(db, opp_id)
    return _do(opp_id, lambda: P.redraft(db, g.user, opp), "A fresh draft was made from the latest answers.",
               "report")


@bp.route("/sellers/<int:opp_id>/pilot/report.pdf")
@login_required
def report_pdf(opp_id):
    db = get_db()
    opp = _opp(db, opp_id)
    rep = P.latest_report(db, opp_id)
    if not rep:
        abort(404)
    return _pdf(rep, opp, None, draft=rep["status"] != "approved")


@bp.route("/sellers/<int:opp_id>/pilot/sent", methods=["POST"])
@login_required
def sent(opp_id):
    db = get_db()
    opp = _opp(db, opp_id)
    return _do(opp_id, lambda: P.mark_sent(db, g.user, opp), "Marked as sent.", "report")


@bp.route("/sellers/<int:opp_id>/pilot/book", methods=["POST"])
@login_required
def staff_book(opp_id):
    db = get_db()
    opp = _opp(db, opp_id)
    if not user_can(db, g.user, "crm.log_activity"):
        abort(403)
    slot = request.form.get("slot_id")
    return _do(opp_id, lambda: P.book(db, opp, int(slot) if slot and slot.isdigit() else None,
                                      request.form.get("preferred"), "staff", g.user["id"], request.form.get("note")),
               f"Call with {P.CONSULTANT_NAME} booked — he has been alerted.", "vikas")


@bp.route("/sellers/<int:opp_id>/pilot/booking/<int:bid>", methods=["POST"])
@login_required
def booking(opp_id, bid):
    db = get_db()
    opp = _opp(db, opp_id)
    return _do(opp_id, lambda: P.update_booking(db, g.user, opp, bid, request.form.get("status") or "",
                                                request.form.get("outcome"),
                                                parse_local_input(request.form.get("starts_at"))),
               "Call updated.", "vikas")


@bp.route("/sellers/<int:opp_id>/pilot/chairpe", methods=["POST"])
@login_required
def chairpe(opp_id):
    db = get_db()
    opp = _opp(db, opp_id)
    return _do(opp_id, lambda: P.set_chairpe(db, g.user, opp, request.form), "ChairPe status saved.", "chairpe")


@bp.route("/sellers/<int:opp_id>/pilot/consultation", methods=["POST"])
@login_required
def consultation(opp_id):
    db = get_db()
    opp = _opp(db, opp_id)
    return _do(opp_id, lambda: P.book_consultation(db, g.user, opp, request.form), "Consultation booked.", "consult")


@bp.route("/sellers/<int:opp_id>/pilot/consultation/<int:cid>", methods=["POST"])
@login_required
def consultation_update(opp_id, cid):
    db = get_db()
    opp = _opp(db, opp_id)
    return _do(opp_id, lambda: P.update_consultation(db, g.user, opp, cid, request.form), "Consultation updated.",
               "consult")


def _pdf(rep, opp, booking_link, draft=False):
    data = swot_pdf.build({"c": rep["c"], "date": fmt_ist(rep["approved_at"] or rep["created_at"], "%d %b %Y"),
                           "version": rep["version"]}, booking_link)
    name = "".join(ch if ch.isalnum() else "-" for ch in (rep["c"].get("salon_name") or "salon"))[:40].strip("-")
    fname = f"SWOT-report-{name}{'-DRAFT' if draft else ''}.pdf"
    return Response(data, mimetype="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{fname}"', "Cache-Control": "no-store"})


@bp.app_context_processor
def _pilot_ctx():
    if (request.endpoint or "") != "sellers.detail" or not g.get("user"):
        return {}
    try:
        opp_id = int((request.view_args or {}).get("opp_id"))
        db = get_db()
        ctx = P.record_context(db, {"id": opp_id})
        ctx["P"] = P
        ctx["can_manage_slots"] = P.can_manage_slots(db, g.user)
        return ctx
    except Exception:  # noqa: BLE001 — a side card must never break the record page
        return {}


# -- Public pages (salon owner, no login) -------------------------------------------------------------------------
def _public(template, status=200, **ctx):
    lang = "en"  # the pilot form keeps the Google Form's English wording; Telugu can be added later

    def _(s):
        return i18n.tr(s, lang)
    from flask import make_response
    resp = make_response(render_template(template, lang=lang, LANGS=None, _=_, P=P, **ctx), status)
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["Referrer-Policy"] = "no-referrer"
    return resp


@public_bp.route("/s/<token>", methods=["GET", "POST"])
def form(token):
    db = get_db()
    opp = P.resolve(db, token)
    if not opp:
        return _public("public/pilot_gone.html", 404)
    if opp["kind"] == "buyer":
        return _investor_form(db, token, opp)
    answers, errors = P.prefill(db, opp), {}
    if request.method == "POST":
        if request.form.get("website"):  # honeypot
            return _public("public/pilot_done.html", token=token, opp=opp)
        answers, errors = P.parse_answers(request.form, lenient=True)  # answer what you can; the team helps
        if not errors:
            P.save_response(db, opp, answers, "link")
            return redirect(url_for("public_pilot.thanks", token=token))
    return _public("public/pilot_form.html", token=token, opp=opp, answers=answers, errors=errors,
                   filled=P.profile(db, opp["id"])["form_status"] == "filled")


@public_bp.route("/s/<token>/thanks")
def thanks(token):
    """After the open form: what happens next, quick wins from their own answers, book Vikas, their personal page."""
    from urllib.parse import quote
    db = get_db()
    opp = P.resolve(db, token)
    if not opp:
        return _public("public/pilot_gone.html", 404)
    tips, keep_ready = [], []
    if opp["kind"] == "buyer":
        from ..domain import investor as I
        rep = I.latest_report(db, opp["id"])
        if rep:
            tips = [s for s in rep["c"].get("steps") or [] if s.get("title")][:3]
            keep_ready = (rep["c"].get("conditions") or [])[:4]
        keep_ready = keep_ready or ["How much money is ready now, and from where (savings, family, loan)",
                                    "Six months of running costs kept aside (rent, salaries, products)",
                                    "Two or three areas you prefer, and how far from them is OK"]
    else:
        rep = P.latest_report(db, opp["id"])
        if rep:
            tips = [s for s in rep["c"].get("steps") or [] if s.get("title")][:3]
    first = (opp.get("contact_name") or "").split(" ")[0]
    wa_text = (f"Hi Salon Shastra, this is {first}. I just filled the "
               + ("investor form." if opp["kind"] == "buyer" else f"pilot program form for {opp.get('salon_name') or 'my salon'}."))
    return _public("public/thanks.html", token=token, opp=opp, tips=tips, keep_ready=keep_ready,
                   slots=P.open_slots(db), booked=_live_booking(db, opp["id"]),
                   wa_link="https://wa.me/918686878090?text=" + quote(wa_text))


@public_bp.route("/s/<token>/report")
def report_page(token):
    db = get_db()
    opp = P.resolve(db, token)
    if not opp:
        return _public("public/pilot_gone.html", 404)
    if opp["kind"] == "buyer":
        from ..domain import investor as I
        return _public("public/investor_report.html", token=token, opp=opp,
                       rep=I.latest_report(db, opp["id"], approved_only=True))
    rep = P.latest_report(db, opp["id"], approved_only=True)
    return _public("public/pilot_report.html", token=token, opp=opp, rep=rep, slots=P.open_slots(db),
                   booked=_live_booking(db, opp["id"]))


@public_bp.route("/s/<token>/report.pdf")
def public_pdf(token):
    db = get_db()
    opp = P.resolve(db, token)
    if opp and opp["kind"] == "buyer":
        from ..domain import investor as I
        from .investor import investor_pdf
        irep = I.latest_report(db, opp["id"], approved_only=True)
        return investor_pdf(irep) if irep else _public("public/pilot_gone.html", 404)
    rep = P.latest_report(db, opp["id"], approved_only=True) if opp else None
    if not rep:
        return _public("public/pilot_gone.html", 404)
    return _pdf(rep, opp, f"{messaging.public_base(request)}/s/{token}/book")


def _investor_form(db, token, opp):
    from ..domain import investor as I
    answers, errors = I.prefill(db, opp), {}
    if request.method == "POST":
        if request.form.get("website"):  # honeypot
            return _public("public/investor_done.html", token=token, opp=opp)
        answers, errors = I.parse_answers(request.form, lenient=True)
        if not errors:
            I.save_response(db, opp, answers, "link")
            return redirect(url_for("public_pilot.thanks", token=token))
    return _public("public/investor_form.html", token=token, opp=opp, answers=answers, errors=errors, I=I,
                   filled=I.profile(db, opp["id"])["form_status"] == "filled")


def _live_booking(db, opp_id):
    return db.one("SELECT vb.*, s.mode, s.minutes FROM vikas_bookings vb LEFT JOIN vikas_slots s ON s.id = vb.slot_id "
                  "WHERE vb.opportunity_id = ? AND vb.status IN ('requested','booked') AND "
                  "(vb.starts_at IS NULL OR vb.starts_at > ?) ORDER BY vb.id DESC LIMIT 1", (opp_id, now_iso()))


@public_bp.route("/s/<token>/book", methods=["GET", "POST"])
def book(token):
    db = get_db()
    opp = P.resolve(db, token)
    if not opp:
        return _public("public/pilot_gone.html", 404)
    error = None
    if request.method == "POST" and not request.form.get("website"):
        slot = request.form.get("slot_id")
        try:
            P.book(db, opp, int(slot) if slot and slot.isdigit() else None, request.form.get("preferred"), "link",
                   None, request.form.get("note"))
            return redirect(url_for("public_pilot.book", token=token, done=1))
        except ValidationError as e:
            error = _err(e)
    return _public("public/pilot_book.html", token=token, opp=opp, slots=P.open_slots(db), error=error,
                   booked=_live_booking(db, opp["id"]), done=request.args.get("done"))
