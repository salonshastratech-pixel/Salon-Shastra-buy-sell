"""Customer-facing forms (no staff login). Public quick forms + personal secure links (PRD §5.6).

Protection: quick forms carry a signed, time-limited form token, a honeypot field and per-IP rate limiting;
personal links are protected by their unguessable token (only its hash is stored). Neither can read or
write anything except the respondent's own submission.
"""
import json

from flask import (Blueprint, abort, current_app, jsonify, make_response, redirect, render_template, request,
                   url_for)
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


OPEN_FORMS = {
    "pilot": {"key": "pilot_open", "title": "Salon Shastra Pilot Program",
              "intro": "This form helps us understand your salon’s current position, challenges, and opportunities "
                       "so we can provide personalized solutions to help you grow. Based on your answers we prepare "
                       "a free SWOT report for your salon with clear next steps.",
              "time": "05-10 minutes"},
    "investor": {"key": "investor_open", "title": "Planning a salon investment?",
                 "intro": "These questions help Salon Shastra understand your plan, your money and how much support "
                          "you'll need — so we can tell you honestly whether and how to invest. After you submit, "
                          "you can speak with our founder Vikas Rachakonda.",
                 "time": "about 10 minutes"},
}


@bp.route("/pilot", methods=["GET", "POST"])
@bp.route("/investor", methods=["GET", "POST"])
def open_form():
    """The two open questionnaires anyone can fill (ads, WhatsApp groups, website): the Pilot Program form for salon
    owners and the Investor form. Each submission creates the record, saves every answer and drafts the report."""
    from ..domain import investor as INV
    from ..domain import pilot as PIL
    kind = request.path.rstrip("/").rsplit("/", 1)[-1]
    meta = OPEN_FORMS[kind]
    mod = PIL if kind == "pilot" else INV
    db = get_db()
    answers, errors = {}, {}
    if request.method == "POST":
        ip = _ip_hash()
        try:
            if _signer().loads(request.form.get("form_token", ""), max_age=FORM_TOKEN_MAX_AGE) != meta["key"]:
                raise BadData("wrong form")
        except BadData:
            F.record_public_hit(db, ip, meta["key"], "bad_token")
            return _page("public/message.html", 400, title="Please try again",
                         message="This form expired or was not loaded correctly. Please reload the page and resubmit.")
        if request.form.get("website"):
            F.record_public_hit(db, ip, meta["key"], "honeypot")
            return redirect(url_for("public.open_thanks", kind=kind))
        if F.public_rate_limited(db, ip):
            F.record_public_hit(db, ip, meta["key"], "rate_limited")
            return _page("public/message.html", 429, title="Too many submissions",
                         message="We have already received several forms from this connection. Please try again "
                                 "later or call us.")
        answers, errors = mod.parse_answers(request.form, lenient=True)
        errors.update(_need_name_and_mobile(kind, answers))
        phone_key = "contact_number" if kind == "pilot" else "phone"
        if answers.get(phone_key) and phone_key not in errors:
            from ..domain.common import normalize_indian_mobile
            perr = normalize_indian_mobile(answers[phone_key])[1]
            if perr:
                errors[phone_key] = perr
        if not request.form.get("consent_contact"):
            errors["consent_contact"] = "Please tick this box so we can contact you about your answers."
        if not errors:
            try:
                token = request.form.get("draft") or ""
                draft = PIL.resolve(db, token) if token else None
                if draft and draft["kind"] == ("seller" if kind == "pilot" else "buyer"):
                    mod.finish_public(db, draft, answers)          # the same record that was saved while filling
                else:
                    token = PIL.issue_link(db, mod.submit_public(db, answers, consent=True))
                F.record_public_hit(db, ip, meta["key"], "submitted")
                return redirect(url_for("public_pilot.thanks", token=token))
            except ValidationError as e:
                raw = e.errors if isinstance(e.errors, dict) else {"_": str(e.errors)}
                # the record's field names → this form's question names, so the message shows on the right question
                rename = ({"full_name": "owner_name", "phone": "contact_number", "city": "location",
                           "locality": "location"} if kind == "pilot" else {})
                errors = {rename.get(k, k): v for k, v in raw.items()}
                errors["_"] = "Please check: " + " ".join(str(v) for v in raw.values())
    from ..domain import geo
    return _page("public/open_form.html", meta=meta, kind=kind, Q=mod, answers=answers, errors=errors,
                 form_token=_signer().dumps(meta["key"]), req_keys=REQUIRED_KEYS[kind],
                 draft=request.form.get("draft", ""), area_names=geo.known_area_names())


REQUIRED_KEYS = {"pilot": ("owner_name", "contact_number"), "investor": ("full_name", "phone")}


def _need_name_and_mobile(kind: str, answers: dict) -> dict:
    """The only two answers anyone must give: who they are and how to reach them. Everything else can be blank —
    the team completes it by call or WhatsApp."""
    name_key, phone_key = REQUIRED_KEYS[kind]
    out = {}
    if not answers.get(name_key):
        out[name_key] = "Please tell us your name."
    if not answers.get(phone_key):
        out[phone_key] = "Please give your mobile number so we can reach you."
    return out


@bp.route("/pilot/save", methods=["POST"])
@bp.route("/investor/save", methods=["POST"])
def open_save():
    """Saves the open form as it is being filled (each step), so nothing is lost if the person stops half-way.
    Needs a name and a valid mobile number; answers nothing else. Returns the personal link token (kept in the page)."""
    from ..domain import investor as INV
    from ..domain import pilot as PIL
    from ..domain.common import normalize_indian_mobile
    kind = request.path.strip("/").split("/")[-2]
    meta = OPEN_FORMS[kind]
    mod = PIL if kind == "pilot" else INV
    db = get_db()
    try:
        if _signer().loads(request.form.get("form_token", ""), max_age=FORM_TOKEN_MAX_AGE) != meta["key"]:
            raise BadData("wrong form")
    except BadData:
        return jsonify({"ok": False}), 400
    if request.form.get("website"):
        return jsonify({"ok": True})
    answers, _errors = mod.parse_answers(request.form, lenient=True)
    name_key, phone_key = REQUIRED_KEYS[kind]
    if not answers.get(name_key) or normalize_indian_mobile(answers.get(phone_key))[1]:
        return jsonify({"ok": False, "why": "name and mobile first"})
    token = request.form.get("draft") or ""
    opp = PIL.resolve(db, token) if token else None
    try:
        if opp and opp["kind"] == ("seller" if kind == "pilot" else "buyer"):
            mod.save_partial(db, opp, answers)
        else:
            if F.public_rate_limited(db, _ip_hash()):
                return jsonify({"ok": False}), 429
            token = PIL.issue_link(db, mod.submit_public(db, answers, consent=bool(request.form.get("consent_contact")),
                                                         final=False))
            F.record_public_hit(db, _ip_hash(), meta["key"], "started")
    except ValidationError:
        return jsonify({"ok": False})
    return jsonify({"ok": True, "draft": token})


@bp.route("/geo/near")
def geo_near():
    """'Use my location': the nearest known area to the phone's position (nothing is stored)."""
    from ..domain import geo
    try:
        lat, lng = float(request.args.get("lat", "")), float(request.args.get("lng", ""))
    except ValueError:
        return jsonify({"ok": False}), 400
    area, city = geo.nearest_area(lat, lng)
    label = f"{area}, {city}" if area and city and city.lower() != area.lower() else (area or "")
    return jsonify({"ok": bool(area), "area": area, "city": city, "label": label})


@bp.route("/<kind>/done")
def open_thanks(kind):
    if kind not in OPEN_FORMS:
        abort(404)
    msg = ("Thank you! We have received your answers. Our team is preparing your free SWOT report and will send it "
           "to you on WhatsApp soon." if kind == "pilot" else
           "Thank you! We have received your answers. A member of the Salon Shastra team will call you to set up a "
           "short call with our founder, Vikas Rachakonda.")
    return _page("public/message.html", title="Thank you", message=msg)


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
               "revoked": "This link has been replaced by a newer one. Please open the latest link Salon Shastra sent "
                          "you on WhatsApp — your answers so far are kept there. If you can't find it, ask your "
                          "Salon Shastra contact to send it again."}
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
