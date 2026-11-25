"""WhatsApp team inbox, batches, webhook endpoint, integrations/templates admin and reports (Milestone 7)."""
import json
import uuid

from flask import (Blueprint, Response, abort, current_app, flash, g, redirect, render_template, request, url_for)

from .. import messaging
from ..db import get_db
from ..domain import inbox as I
from ..domain import reports as R
from ..domain import tasks as I_T
from ..domain.errors import ValidationError
from ..permissions import user_can
from ..security import login_required, require
from ..timeutil import now_iso
from .sellers import _active_users

bp = Blueprint("inbox", __name__, url_prefix="/inbox")
webhook = Blueprint("public_webhook", __name__, url_prefix="/webhooks")
reports_bp = Blueprint("reports", __name__, url_prefix="/reports")

CONV_SELECT = ("SELECT v.*, c.full_name AS contact_name, u.full_name AS assignee_name, lu.full_name AS last_by_name, "
               "(SELECT body FROM messages m WHERE m.conversation_id = v.id ORDER BY m.id DESC LIMIT 1) AS last_body "
               "FROM conversations v LEFT JOIN contacts c ON c.id = v.contact_id LEFT JOIN users u ON u.id = v.assigned_to "
               "LEFT JOIN users lu ON lu.id = v.last_outbound_by ")
QUEUES = {"mine": ("Assigned to me", "v.status = 'open' AND v.assigned_to = ?"),
          "unassigned": ("Unassigned", "v.status = 'open' AND v.assigned_to IS NULL AND v.contact_id IS NOT NULL"),
          "unmatched": ("Unknown numbers", "v.status = 'open' AND v.contact_id IS NULL"),
          "open": ("All open", "v.status = 'open'"), "closed": ("Closed", "v.status = 'closed'")}


def _msg(e):
    return " ".join(str(v) for v in e.errors.values())


@bp.route("/")
@require("crm.view")
def index():
    db = get_db()
    q = request.args.get("q", "mine")
    if q not in QUEUES:
        q = "mine"
    cond = QUEUES[q][1]
    params = [g.user["id"]] if "?" in cond else []
    rows = db.all(CONV_SELECT + f"WHERE {cond} ORDER BY v.unread DESC, v.last_message_at DESC LIMIT 200", params)
    counts = {k: db.scalar("SELECT COUNT(*) FROM conversations v WHERE " + c, [g.user["id"]] if "?" in c else [])
              for k, (_l, c) in QUEUES.items()}
    uncertain = db.all("SELECT m.*, v.phone, c.full_name AS contact_name FROM messages m JOIN conversations v ON v.id = "
                       "m.conversation_id LEFT JOIN contacts c ON c.id = v.contact_id WHERE m.status = 'uncertain' "
                       "ORDER BY m.id DESC")
    team_numbers = db.all("SELECT full_name, wa_phone_number_id FROM users WHERE wa_phone_number_id IS NOT NULL AND "
                          "is_active = 1 ORDER BY full_name")
    return render_template("inbox/index.html", rows=rows, q=q, counts=counts, QUEUES=QUEUES, uncertain=uncertain,
                           team_numbers=team_numbers,
                           wa=messaging.whatsapp_status(current_app.config))


@bp.route("/<int:conv_id>")
@require("crm.view")
def thread(conv_id):
    db = get_db()
    conv = db.one(CONV_SELECT + "WHERE v.id = ?", (conv_id,))
    if not conv:
        abort(404)
    if conv["unread"] and conv["assigned_to"] in (None, g.user["id"]):
        db.update("conversations", conv_id, {"unread": 0})
    msgs = db.all("SELECT m.*, u.full_name AS by_name FROM messages m LEFT JOIN users u ON u.id = m.sent_by "
                  "WHERE conversation_id = ? ORDER BY m.id", (conv_id,))
    links = db.all("SELECT o.id, o.ref_code, o.kind, o.stage, b.name AS salon_name FROM conversation_links l "
                   "JOIN opportunities o ON o.id = l.opportunity_id LEFT JOIN salon_businesses b ON b.id = o.business_id "
                   "WHERE l.conversation_id = ?", (conv_id,))
    if conv["contact_id"]:
        other = db.all("SELECT o.id, o.ref_code, o.kind, o.stage FROM opportunities o WHERE o.contact_id = ? AND o.id NOT IN "
                       "(SELECT opportunity_id FROM conversation_links WHERE conversation_id = ?)", (conv["contact_id"], conv_id))
    else:
        other = []
    I.ensure_templates(db)
    templates = I.usable_templates(db, messaging.whatsapp_status(current_app.config)["live"])
    for t in templates:
        t["fields"] = [p for p in messaging.placeholders(t["body"]) if p not in ("name", "sender")]
    blocks, warns = I.check_send(db, g.user, conv["contact_id"], "service") if conv["contact_id"] else (["unknown number — link it to a record first"], [])
    from datetime import timedelta
    from ..timeutil import now_utc, to_iso
    window_open = bool(conv["last_inbound_at"] and conv["last_inbound_at"] >= to_iso(now_utc() - timedelta(hours=24)))
    search = request.args.get("find", "").strip()
    found = []
    if search and not conv["contact_id"]:
        like = f"%{search.lower()}%"
        found = db.all("SELECT o.id, o.ref_code, o.kind, c.full_name, c.phone_normalized FROM opportunities o JOIN contacts c "
                       "ON c.id = o.contact_id WHERE lower(c.full_name) LIKE ? OR lower(o.ref_code) LIKE ? OR "
                       "c.phone_normalized LIKE ? LIMIT 10", (like, like, like))
    reply_from = I.number_label(db, I.sender_number(db, g.user, conv, free_text=True))
    template_from = I.number_label(db, I.sender_number(db, g.user))
    return render_template("inbox/thread.html", reply_from=reply_from, template_from=template_from,
                           conv=conv, msgs=msgs, links=links, other=other, templates=templates,
                           blocks=blocks, warns=warns, window_open=window_open, users=_active_users(db), found=found,
                           search=search, STATUS=I.STATUS_LABELS, wa=messaging.whatsapp_status(current_app.config))


def _back(conv_id):
    return redirect(url_for("inbox.thread", conv_id=conv_id))


def _opp_for(db, conv):
    return db.scalar("SELECT opportunity_id FROM conversation_links WHERE conversation_id = ? ORDER BY linked_at DESC "
                     "LIMIT 1", (conv["id"],))


@bp.route("/<int:conv_id>/send", methods=["POST"])
@require("crm.log_activity")
def send(conv_id):
    db = get_db()
    conv = db.one("SELECT * FROM conversations WHERE id = ?", (conv_id,))
    if not conv:
        abort(404)
    f = request.form
    if not conv["contact_id"]:
        if f.get("mode") != "text":
            flash("Link this number to a buyer or seller before sending a template.", "error")
            return _back(conv_id)
        try:
            m = I.reply_unmatched(db, g.user, conv_id, f.get("text"))
            flash(f"Message {I.STATUS_LABELS[m['status']].lower()}." + (f" {m['detail']}" if m.get("detail") else ""),
                  "error" if m["status"] in ("failed", "uncertain") else "ok")
        except ValidationError as e:
            flash(_msg(e), "error")
        return _back(conv_id)
    try:
        if f.get("mode") == "text":
            m = I.send(db, g.user, contact_id=conv["contact_id"], opportunity_id=_opp_for(db, conv), text=f.get("text"),
                       acknowledge=f.get("acknowledge") == "1")
        else:
            name = db.scalar("SELECT full_name FROM contacts WHERE id = ?", (conv["contact_id"],)).split()[0]
            variables = {k[4:]: v for k, v in f.items() if k.startswith("var_")}
            m = I.send(db, g.user, contact_id=conv["contact_id"], opportunity_id=_opp_for(db, conv),
                       template_key=f.get("template_key"), variables={"name": name,
                                                                      "sender": g.user["full_name"].split()[0], **variables},
                       acknowledge=f.get("acknowledge") == "1")
        flash(f"Message {I.STATUS_LABELS[m['status']].lower()}." + (f" {m['detail']}" if m.get("detail") else ""),
              "error" if m["status"] in ("failed", "uncertain") else "ok")
    except I.CollisionWarning as w:
        flash(w.message + " Tick 'send anyway' to confirm.", "error")
    except ValidationError as e:
        flash(_msg(e), "error")
    return _back(conv_id)


@bp.route("/<int:conv_id>/action", methods=["POST"])
@login_required
def action(conv_id):
    db = get_db()
    f = request.form
    try:
        a = f.get("action")
        if a == "assign":
            I.assign(db, g.user, conv_id, int(f.get("user_id") or 0) or None, f.get("note"))
        elif a == "link":
            I.link(db, g.user, conv_id, int(f.get("opportunity_id") or 0))
        elif a in ("close", "reopen"):
            I.set_status(db, g.user, conv_id, "closed" if a == "close" else "open")
        else:
            abort(400)
        flash("Done.", "ok")
    except ValidationError as e:
        flash(_msg(e), "error")
    return _back(conv_id)


@bp.route("/messages/<int:mid>/resolve", methods=["POST"])
@login_required
def resolve(mid):
    db = get_db()
    m = db.one("SELECT conversation_id FROM messages WHERE id = ?", (mid,))
    if not m:
        abort(404)
    try:
        I.resolve_uncertain(db, g.user, mid, request.form.get("outcome"))
        flash("Recorded. You can send again if it did not arrive.", "ok")
    except ValidationError as e:
        flash(_msg(e), "error")
    return _back(m["conversation_id"])


@bp.route("/start/<int:opp_id>", methods=["POST"])
@require("crm.view")
def start(opp_id):
    """Open (or create) the WhatsApp conversation for a buyer/seller record."""
    db = get_db()
    o = db.one("SELECT o.id, c.id AS contact_id, c.phone_normalized FROM opportunities o JOIN contacts c ON "
               "c.id = o.contact_id WHERE o.id = ?", (opp_id,))
    if not o or not o["phone_normalized"]:
        abort(404)
    with db:
        conv = I.conversation_for(db, o["phone_normalized"], o["contact_id"])
        db.execute("INSERT INTO conversation_links (conversation_id, opportunity_id, linked_by, linked_at) SELECT ?, ?, ?, ? "
                   "WHERE NOT EXISTS (SELECT 1 FROM conversation_links WHERE conversation_id = ? AND opportunity_id = ?)",
                   (conv["id"], opp_id, g.user["id"], now_iso(), conv["id"], opp_id))
    return _back(conv["id"])


@bp.route("/wa/<int:opp_id>", methods=["POST"])
@require("crm.log_activity")
def wa_open(opp_id):
    """One-click WhatsApp. With the WhatsApp Business API live, open the record's conversation in TeamOS; until then
    open WhatsApp itself (phone app or WhatsApp Web) with a ready greeting, and note it on the record."""
    from urllib.parse import quote
    db = get_db()
    o = db.one("SELECT o.id, o.kind, c.do_not_contact, c.full_name, c.phone_normalized FROM opportunities o "
               "JOIN contacts c ON c.id = o.contact_id WHERE o.id = ?", (opp_id,))
    if not o or not o["phone_normalized"]:
        abort(404)
    ep = "sellers.detail" if o["kind"] == "seller" else "buyers.detail"
    if o["do_not_contact"]:
        flash("This person asked not to be contacted.", "error")
        return redirect(url_for(ep, opp_id=opp_id))
    if messaging.whatsapp_status(current_app.config)["live"]:
        return start(opp_id)
    digits = "".join(ch for ch in o["phone_normalized"] if ch.isdigit())
    digits = "91" + digits if len(digits) == 10 else digits
    first = (o["full_name"] or "").split(" ")[0] or "Sir/Madam"
    me = (g.user.get("full_name") or "").split(" ")[0]
    text = f"Hello {first}, this is {me} from Salon Shastra." if me else f"Hello {first}, greetings from Salon Shastra."
    with db:
        I_T.add_activity(db, g.user["id"], "opportunity", opp_id, "note", "WhatsApp opened from TeamOS (personal WhatsApp)")
        from ..domain.timeline import mark_contacted
        mark_contacted(db, opp_id)
    return redirect(f"https://wa.me/{digits}?text={quote(text)}")


@bp.route("/simulate", methods=["POST"])
@require("crm.view")
def simulate():
    """TEST-ONLY: pretend a customer sent a WhatsApp message (disabled when a live provider is configured)."""
    db = get_db()
    if messaging.whatsapp_status(current_app.config)["live"]:
        abort(404)
    phone = (request.form.get("phone") or "").strip()
    text = (request.form.get("text") or "").strip()
    if not phone or not text:
        flash("Enter a phone number and a message.", "error")
        return redirect(url_for("inbox.index"))
    m = I.record_inbound(db, phone, text, f"sim-{uuid.uuid4().hex[:16]}", provider="mock-whatsapp",
                         business_number_id=request.form.get("to_number") or None)
    flash("Simulated inbound message recorded (test only).", "ok")
    return _back(m["conversation_id"]) if m else redirect(url_for("inbox.index"))


# -- Batches -------------------------------------------------------------------------------------
@bp.route("/batches", methods=["GET", "POST"])
@require("crm.view")
def batches():
    db = get_db()
    I.ensure_templates(db)
    if request.method == "POST":
        f = request.form
        try:
            bid = I.create_batch(db, g.user, f.get("name"), f.get("template_key"), f.get("audience"),
                                 {k[4:]: v for k, v in f.items() if k.startswith("var_") and v.strip()})
            flash("Batch drafted — check who is eligible, then a reviewer approves it.", "ok")
            return redirect(url_for("inbox.batch", batch_id=bid))
        except ValidationError as e:
            flash(_msg(e), "error")
    rows = db.all("SELECT b.*, u.full_name AS by_name, a.full_name AS approver_name FROM batches b LEFT JOIN users u ON "
                  "u.id = b.created_by LEFT JOIN users a ON a.id = b.approved_by ORDER BY b.id DESC")
    for r in rows:
        r["counts_d"] = json.loads(r["counts"]) if r["counts"] else None
    templates = [t for t in I.usable_templates(db, messaging.whatsapp_status(current_app.config)["live"])
                 if I.batch_allowed(t)]
    for t in templates:
        t["fields"] = [p for p in messaging.placeholders(t["body"]) if p not in ("name", "sender")]
    return render_template("inbox/batches.html", rows=rows, templates=templates, AUDIENCES=I.AUDIENCES,
                           wa=messaging.whatsapp_status(current_app.config))


@bp.route("/batches/<int:batch_id>", methods=["GET", "POST"])
@require("crm.view")
def batch(batch_id):
    db = get_db()
    b = db.one("SELECT * FROM batches WHERE id = ?", (batch_id,))
    if not b:
        abort(404)
    if request.method == "POST":
        try:
            if request.form.get("action") == "approve":
                I.approve_batch(db, g.user, batch_id)
                flash("Batch approved. Eligibility is checked again for every person at the moment of sending.", "ok")
            elif request.form.get("action") == "send":
                c = I.send_batch(db, g.user, batch_id)
                flash(f"Batch run: {c['sent']} sent, {c['suppressed']} suppressed, {c['failed']} failed.", "ok")
            elif request.form.get("action") == "cancel" and b["status"] in ("draft", "approved"):
                if b["created_by"] != g.user["id"] and not user_can(db, g.user, "crm.assign"):
                    abort(403)
                db.update("batches", batch_id, {"status": "cancelled", "updated_at": now_iso()})
                flash("Batch cancelled.", "ok")
        except ValidationError as e:
            flash(_msg(e), "error")
        return redirect(url_for("inbox.batch", batch_id=batch_id))
    t = I.template(db, b["template_key"])
    rows = I.batch_preview(db, batch_id)
    return render_template("inbox/batch.html", b=b, t=t, rows=rows, aud=json.loads(b["audience"]), AUDIENCES=I.AUDIENCES,
                           can_approve=user_can(db, g.user, "crm.assign"), wa=messaging.whatsapp_status(current_app.config),
                           counts=json.loads(b["counts"]) if b["counts"] else None)


# -- Webhook (provider → us). Public, but every POST must carry a valid signature. -------------------
@webhook.route("/whatsapp", methods=["GET", "POST"])
def whatsapp():
    cfg = current_app.config
    if request.method == "GET":  # Meta's subscription handshake
        token = messaging._cfg("WHATSAPP_VERIFY_TOKEN", cfg)
        if token and request.args.get("hub.mode") == "subscribe" and request.args.get("hub.verify_token") == token:
            return Response(request.args.get("hub.challenge", ""), mimetype="text/plain")
        abort(403)
    secret = messaging._cfg("WHATSAPP_APP_SECRET", cfg)
    raw = request.get_data()
    if not messaging.verify_signature(secret, raw, request.headers.get("X-Hub-Signature-256")):
        abort(403)
    try:
        payload = json.loads(raw.decode() or "{}")
    except ValueError:
        abort(400)
    stats = I.ingest_webhook(get_db(), payload)
    return {"ok": True, **stats}


# -- Reports --------------------------------------------------------------------------------------
@reports_bp.route("/")
@require("crm.view")
def index():
    db = get_db()
    p = R.period_bounds(request.args.get("period", "today"), request.args.get("start"), request.args.get("end"))
    r = R.build(db, g.user, p)
    if request.args.get("format") == "csv":
        from .. import audit
        audit.record(db, g.user["id"], "report.export", None, None, {"period": p["key"], "start": p["start"]})
        return Response(R.to_csv(r), mimetype="text/csv",
                        headers={"Content-Disposition": f"attachment; filename=teamos-report-{p['start_date']}_{p['end_date']}.csv"})
    return render_template("reports/index.html", r=r, p=p, PERIODS=R.PERIODS)


# -- One-click follow-ups on a record (after a call, or any time) --------------------------------
@bp.app_context_processor
def _quick_messages_helper():
    def quick_messages(opp_id, outcome=None):
        from ..domain import quick_messages as Q
        if not g.get("user") or not user_can(get_db(), g.user, "crm.log_activity"):
            return {"items": [], "sending_from": ""}
        db = get_db()
        return {"items": Q.suggest(db, g.user, opp_id, outcome),
                "sending_from": I.number_label(db, I.sender_number(db, g.user)),
                "test_only": not messaging.whatsapp_status(current_app.config)["live"]}
    return {"quick_messages": quick_messages}


@bp.route("/quick/<int:opp_id>", methods=["POST"])
@require("crm.log_activity")
def quick_send(opp_id):
    from ..domain import quick_messages as Q
    db = get_db()
    kind = db.scalar("SELECT kind FROM opportunities WHERE id = ?", (opp_id,))
    if not kind:
        abort(404)
    f = request.form
    outcome = f.get("outcome") or None
    try:
        r = Q.send(db, g.user, opp_id, f.get("sid"), outcome, acknowledge=f.get("acknowledge") == "1",
                   link_base=messaging.public_base(request))
        flash(f"WhatsApp sent: {r['label']} — {I.STATUS_LABELS.get(r['status'], r['status']).lower()}.", "ok")
        outcome = None  # done: show the normal record page again
    except I.CollisionWarning as w:
        flash(w.message + " Tick 'send anyway' to confirm.", "error")
    except ValidationError as e:
        flash(_msg(e), "error")
    ep = "sellers.detail" if kind == "seller" else "buyers.detail"
    return redirect(url_for(ep, opp_id=opp_id, after_call=outcome) + "#quick")
