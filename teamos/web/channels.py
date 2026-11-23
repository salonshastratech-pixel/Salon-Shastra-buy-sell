"""Milestone 9 screens: calendar (+ private subscribe link), tap-to-call helpers, Facebook / Instagram lead ads."""
import json

from flask import (Blueprint, Response, abort, current_app, flash, g, redirect, render_template, request,
                   url_for)

from .. import messaging
from ..db import get_db
from ..domain import calendar as C
from ..domain import leadads as LA
from ..domain.errors import ValidationError
from ..permissions import user_can
from ..security import login_required, require
from .inbox import webhook  # the public, signature-checked webhook blueprint (/webhooks/…)

bp = Blueprint("channels", __name__)
feed_bp = Blueprint("public_calendar", __name__)


# -- Calendar --------------------------------------------------------------------------------------------------
@bp.route("/calendar")
@login_required
def calendar():
    db = get_db()
    who = g.user["id"]
    if request.args.get("who") and user_can(db, g.user, "crm.assign"):
        who = int(request.args["who"])
    start = C.week_start(request.args.get("week"))
    from datetime import timedelta
    people = db.all("SELECT id, full_name FROM users WHERE is_active = 1 AND role <> 'finance' ORDER BY full_name") \
        if user_can(db, g.user, "crm.assign") else []
    return render_template("calendar.html", days=C.week(db, who, start), start=start, who=who, people=people,
                           prev=(start - timedelta(days=7)).isoformat(), next=(start + timedelta(days=7)).isoformat(),
                           feed=C.has_feed(db, g.user["id"]), new_link=None)


@bp.route("/calendar/link", methods=["POST"])
@login_required
def calendar_link():
    db = get_db()
    token = C.new_feed(db, g.user)
    link = messaging.public_base(request) + url_for("public_calendar.feed", token=token)
    start = C.week_start(None)
    from datetime import timedelta
    return render_template("calendar.html", days=C.week(db, g.user["id"], start), start=start, who=g.user["id"],
                           people=[], prev=(start - timedelta(days=7)).isoformat(),
                           next=(start + timedelta(days=7)).isoformat(), feed=C.has_feed(db, g.user["id"]),
                           new_link=link)


@bp.route("/calendar/link/revoke", methods=["POST"])
@login_required
def calendar_revoke():
    C.revoke_feed(get_db(), g.user)
    flash("Your calendar link was switched off. Calendars using it will stop updating.", "ok")
    return redirect(url_for("channels.calendar"))


@feed_bp.route("/cal/<token>.ics")
def feed(token):
    db = get_db()
    user = C.feed_user(db, token)
    if not user:
        abort(404)
    resp = Response(C.ics(db, user, messaging.public_base(request)), mimetype="text/calendar")
    resp.headers["Cache-Control"] = "private, max-age=900"
    resp.headers["X-Robots-Tag"] = "noindex"
    return resp


# -- Lead ads --------------------------------------------------------------------------------------------------
SIM_FIELDS = [("full_name", "Name"), ("phone_number", "Phone number"), ("city", "City"),
              ("area", "Area / locality"), ("what_is_your_budget?", "Budget (buyers)"),
              ("salon_name", "Salon name (sellers)"), ("monthly_rent", "Monthly rent (sellers)"),
              ("when_do_you_want_to_sell?", "When to sell (sellers)"), ("when_are_you_planning_to_buy?",
                                                                        "When to buy (buyers)")]


@bp.route("/admin/lead-ads", methods=["GET", "POST"])
@require("settings.manage")
def lead_ads():
    db = get_db()
    result, errors = None, {}
    if request.method == "POST":
        try:
            result = LA.simulate(db, request.form.get("kind", "buyer"),
                                 {k: request.form.get(k, "") for k, _l in SIM_FIELDS})
            flash(f"Test lead: {result['status']} — {result['detail']}", "ok" if result["status"] == "imported"
                  else "warn")
            return redirect(url_for("channels.lead_ads"))
        except ValidationError as e:
            errors = e.errors
    live = (messaging.setting("META_LEADS_LIVE") or "").lower() in ("yes", "1", "on", "true") and \
        bool(messaging.setting("META_LEADS_PAGE_TOKEN"))
    return render_template("admin/lead_ads.html", recent=LA.recent(db), fields=SIM_FIELDS, errors=errors,
                           live=live, hook=messaging.public_base(request) + "/webhooks/meta-leads")


@webhook.route("/meta-leads", methods=["GET", "POST"])
def meta_leads():
    if request.method == "GET":  # Meta's subscription handshake
        token = messaging.setting("META_LEADS_VERIFY_TOKEN")
        if token and request.args.get("hub.mode") == "subscribe" and request.args.get("hub.verify_token") == token:
            return Response(request.args.get("hub.challenge", ""), mimetype="text/plain")
        abort(403)
    raw = request.get_data()
    secret = messaging.setting("META_LEADS_APP_SECRET") or messaging.setting("WHATSAPP_APP_SECRET")
    if not LA.signature_ok(secret, raw, request.headers.get("X-Hub-Signature-256")):
        abort(403)
    try:
        payload = json.loads(raw.decode() or "{}")
    except ValueError:
        abort(400)
    db = get_db()
    live = (messaging.setting("META_LEADS_LIVE") or "").lower() in ("yes", "1", "on", "true")
    token = messaging.setting("META_LEADS_PAGE_TOKEN")
    ids = LA.from_webhook(db, payload, LA.graph_fetch(token) if live and token else None)
    from .. import jobs
    if ids:
        jobs.enqueue(db, "lead_ads", {"ids": ids})
    current_app.logger.info("meta lead webhook: %s new", len(ids))
    return {"ok": True, "received": len(ids)}
