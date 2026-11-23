"""Customer-facing forms (no staff login). Public quick forms + personal secure links (PRD §5.6).

Protection: quick forms carry a signed, time-limited form token, a honeypot field and per-IP rate limiting;
personal links are protected by their unguessable token (only its hash is stored). Neither can read or
write anything except the respondent's own submission.
"""
import json

from flask import Blueprint, abort, current_app, make_response, redirect, render_template, request, url_for
from itsdangerous import BadData, URLSafeTimedSerializer

from .. import i18n
from ..db import get_db
from ..domain import buyer_fields as BFD
from ..domain import form_templates as FT
from ..domain import forms as F
from ..domain.errors import ValidationError

bp = Blueprint("public", __name__, url_prefix="/f")

FORM_TOKEN_MAX_AGE = 2 * 60 * 60


def _signer():
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="public-form")


def _ip_hash():
    return F.hash_ip(request.remote_addr, current_app.config["SECRET_KEY"])


def _page(template, status=200, **ctx):
    lang = i18n.pick(request)
    _ = lambda s: i18n.tr(s, lang)  # noqa: E731
    if "title" in ctx:
        ctx["title"], ctx["message"] = _(ctx["title"]), _(ctx.get("message"))
    resp = make_response(render_template(template, lang=lang, LANGS=i18n.LANGS, _=_, **ctx), status)
    if request.args.get("lang") in i18n.LANGS:
        resp.set_cookie("form_lang", lang, max_age=180 * 86400, samesite="Lax", httponly=True)
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    return resp


def _uploads():
    out = {}
    for key, fs in request.files.items():
        if key.startswith("doc__") and fs and fs.filename:
            out[key[5:]] = (fs.read(), fs.filename)
    return out


def _remember_lang(db, sid):
    """Record which language the customer answered in (answers themselves are stored the same either way)."""
    if isinstance(sid, int):
        db.execute("UPDATE form_submissions SET lang = ? WHERE id = ?", (i18n.pick(request), sid))


@bp.route("/<kind>", methods=["GET", "POST"])
def quick(kind):
    form_key = {"seller": "seller_quick", "buyer": "buyer_quick"}.get(kind)
    if not form_key:
        abort(404)
    t = FT.build(form_key)
    db = get_db()
    errors, answers, contact = {}, {}, {}
    if request.method == "POST":
        ip = _ip_hash()
        try:
            if _signer().loads(request.form.get("form_token", ""), max_age=FORM_TOKEN_MAX_AGE) != form_key:
                raise BadData("wrong form")
        except BadData:
            F.record_public_hit(db, ip, form_key, "bad_token")
            return _page("public/message.html", 400, title="Please try again",
                         message="This form expired or was not loaded correctly. Please reload the page and resubmit.")
        if request.form.get("website"):  # honeypot: humans never see this field
            F.record_public_hit(db, ip, form_key, "honeypot")
            return redirect(url_for("public.thanks", kind=kind))
        if F.public_rate_limited(db, ip):
            F.record_public_hit(db, ip, form_key, "rate_limited")
            return _page("public/message.html", 429, title="Too many submissions",
                         message="We have already received several forms from this connection. Please try again "
                                 "later or call us.")
        try:
            opp_id = F.submit_public(db, form_key, request.form, ip, request.headers.get("User-Agent"))
            _remember_lang(db, db.scalar("SELECT MAX(id) FROM form_submissions WHERE opportunity_id = ? AND "
                                         "invitation_id IS NULL", (opp_id,)))
            return redirect(url_for("public.thanks", kind=kind))
        except ValidationError as e:
            errors = e.errors
            answers, _e, _i = F.collect_answers(t, request.form)
            contact = {k: request.form.get(k, "") for k in t["contact"]}
    return _page("public/form.html", t=t, kind=t["kind"], answers=answers, contact=contact, errors=errors,
                 form_token=_signer().dumps(form_key), personal=False, inv=None, docs=[], purposes={})


@bp.route("/<kind>/thanks")
def thanks(kind):
    if kind not in ("seller", "buyer"):
        abort(404)
    return _page("public/message.html", title="Thank you",
                 message="We have received your details. A member of the Salon Shastra team will call you. "
                         "Nothing is shared with anyone outside Salon Shastra without your permission.")


@bp.route("/i/<token>", methods=["GET", "POST"])
def personal(token):
    db = get_db()
    inv = F.resolve(db, token)
    if not inv:
        return _page("public/message.html", 404, title="Link not recognised",
                     message="This link is not valid. Please check you copied the whole link, or ask your Salon "
                             "Shastra contact for a new one.")
    if inv["status"] not in F.OPEN_STATES:
        msg = {"submitted": "Thank you — we have received your answers and our team is reviewing them.",
               "accepted": "Thank you — your answers have been reviewed.",
               "more_info_required": "Thank you. Our team will contact you about a few more details.",
               "expired": "This link has expired. Please ask your Salon Shastra contact for a new one.",
               "revoked": "This link is no longer active. Please ask your Salon Shastra contact for a new one."}
        return _page("public/message.html", 410 if inv["status"] in ("expired", "revoked") else 200,
                     title="Form closed" if inv["status"] in ("expired", "revoked") else "Thank you",
                     message=msg.get(inv["status"], "This link is no longer active."))
    t = F.template_for(inv)
    errors, saved = {}, False
    if request.method == "POST":
        final = request.form.get("action") == "submit"
        try:
            res = F.save_response(db, inv, request.form, _uploads(), _ip_hash(), request.headers.get("User-Agent"),
                                  final)
            _remember_lang(db, (res or {}).get("submission_id"))
            if final:
                return _page("public/message.html", title="Thank you",
                             message="We have received your answers. Our team will review them and get back to you. "
                                     "Nothing is shared with buyers or sellers without your permission.")
            saved = True
            inv = F.resolve(db, token)
        except ValidationError as e:
            errors = e.errors
    else:
        F.mark_opened(db, inv)
    draft = F.draft_for(db, inv)
    answers = json.loads(draft["answers"]) if draft else F.prefill(db, inv)
    if errors and request.method == "POST":
        answers, _e, _i = F.collect_answers(t, request.form)
    docs = db.all("SELECT doc_type, original_name, created_at FROM documents WHERE invitation_id = ? ORDER BY id",
                  (inv["id"],))
    opp = F.load_opportunity(db, inv["opportunity_id"])
    return _page("public/form.html", t=t, kind=t["kind"], answers=answers, contact={}, errors=errors,
                 personal=True, inv=inv, saved=saved, docs=docs, first_name=opp["contact_name"].split()[0],
                 purposes=FT.PURPOSES if inv["template_key"] == "targeted" else {})
