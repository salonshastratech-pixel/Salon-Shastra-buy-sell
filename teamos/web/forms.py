"""Staff screens for forms & data requests: inbox, review, sending links, documents (Milestone 4)."""
import json

from flask import (Blueprint, Response, abort, current_app, flash, g, redirect, render_template, request,
                   url_for)

from .. import messaging
from ..db import get_db
from ..domain import form_templates as FT
from ..domain import forms as F
from ..domain.errors import ValidationError
from ..permissions import user_can
from ..security import can, deny, login_required, require

bp = Blueprint("forms", __name__, url_prefix="/forms")

SUB_SELECT = ("SELECT s.*, o.ref_code, o.kind AS opp_kind, c.full_name AS contact_name, u.full_name AS reviewer_name, "
              "i.channel FROM form_submissions s LEFT JOIN opportunities o ON o.id = s.opportunity_id "
              "LEFT JOIN contacts c ON c.id = s.contact_id LEFT JOIN users u ON u.id = s.reviewed_by "
              "LEFT JOIN form_invitations i ON i.id = s.invitation_id ")


def record_context(db, user, opp: dict) -> dict:
    """Context for the 'Forms & documents' card on seller and buyer pages."""
    kind = opp["kind"]
    invitations = db.all("SELECT i.*, u.full_name AS sender_name, (SELECT m.provider_message_id FROM messages m "
                         "WHERE m.invitation_id = i.id ORDER BY m.id DESC LIMIT 1) AS provider_message_id "
                         "FROM form_invitations i LEFT JOIN users u ON u.id = i.created_by WHERE i.opportunity_id = ? "
                         "ORDER BY i.id DESC LIMIT 10", (opp["id"],))
    for i in invitations:
        i["fields"] = json.loads(i["field_keys"] or "[]")
        i["docs"] = json.loads(i["doc_types"] or "[]")
    full = F.load_opportunity(db, opp["id"])
    if kind == "seller":
        from ..domain import sellers as S
        listing = S.load_listing(db, opp["listing_id"])
        missing = [f.key for f in S.completeness(listing, S.provenance(db, listing["id"]))["missing_material"]]
    else:
        from ..domain import buyers as B
        m = B.load_mandate(db, opp["mandate_id"])
        prov = B.provenance(db, m["id"])
        missing = [f.key for f in B.completeness(m, prov, B.localities(db, m["id"]))["missing_material"]]
    return {
        "form_invitations": invitations,
        "form_documents": F.documents_for(db, opp["id"]),
        "form_can_docs": F.can_view_documents(db, user, full),
        "form_blockers": F.send_blockers(db, full, "whatsapp_mock"),
        "form_requestable": FT.requestable_fields(kind),
        "form_missing": missing,
        "form_doc_types": FT.DOC_TYPES[kind],
        "form_deep_key": f"{kind}_deep",
        "form_pending_review": db.scalar("SELECT COUNT(*) FROM form_submissions WHERE opportunity_id = ? "
                                         "AND status = 'submitted'", (opp["id"],)),
        "FORM_STATUS": F.STATUS_LABELS,
        "MOCK_LABEL": messaging.PROVIDER_LABEL,
    }


@bp.route("/")
@require("crm.view")
def inbox():
    db = get_db()
    pending = db.all(SUB_SELECT + "WHERE s.status = 'submitted' ORDER BY s.submitted_at")
    open_links = db.all("SELECT i.*, o.ref_code, c.full_name AS contact_name, u.full_name AS sender_name "
                        "FROM form_invitations i JOIN opportunities o ON o.id = i.opportunity_id "
                        "JOIN contacts c ON c.id = i.contact_id LEFT JOIN users u ON u.id = i.created_by "
                        "WHERE i.status IN ('sent','opened','started') ORDER BY i.created_at")
    reviewed = db.all(SUB_SELECT + "WHERE s.status = 'reviewed' ORDER BY s.reviewed_at DESC LIMIT 25")
    comms = db.all("SELECT m.*, o.ref_code, o.kind AS opp_kind, COALESCE(c.full_name, v.phone) AS contact_name, "
                   "u.full_name AS sender_name FROM messages m JOIN conversations v ON v.id = m.conversation_id "
                   "LEFT JOIN contacts c ON c.id = v.contact_id LEFT JOIN opportunities o ON o.id = m.opportunity_id "
                   "LEFT JOIN users u ON u.id = m.sent_by WHERE m.direction = 'out' AND m.kind = 'template' "
                   "ORDER BY m.id DESC LIMIT 15")
    base = __import__("teamos.messaging", fromlist=["public_base"]).public_base(request)
    return render_template("forms/inbox.html", pending=pending, open_links=open_links, reviewed=reviewed, comms=comms,
                           STATUS=F.STATUS_LABELS, public_links={"seller": f"{base}/f/seller",
                                                                 "buyer": f"{base}/f/buyer"},
                           MOCK_LABEL=messaging.PROVIDER_LABEL)


@bp.route("/submissions/<int:sub_id>", methods=["GET", "POST"])
@require("crm.view")
def review(sub_id):
    db = get_db()
    sub = db.one(SUB_SELECT + "WHERE s.id = ?", (sub_id,))
    if not sub or sub["status"] == "draft":
        abort(404)
    if request.method == "POST":
        try:
            r = F.apply_review(db, g.user, sub_id, request.form.getlist("accept"), request.form.get("action"),
                               request.form.get("note"))
            flash(f"Review saved: {r['outcome'].replace('_', ' ')}" +
                  (f" — {len(r['applied'])} field(s) updated on the record." if r["applied"] else "."), "ok")
            return redirect(url_for("forms.review", sub_id=sub_id))
        except ValidationError as e:
            flash(" ".join(str(v) for v in e.errors.values()), "error")
    opp = F.load_opportunity(db, sub["opportunity_id"])
    rows = F.review_rows(db, sub)
    answers = json.loads(sub["answers"] or "{}")
    docs = db.all("SELECT * FROM documents WHERE submission_id = ? ORDER BY id", (sub_id,))
    return render_template("forms/review.html", sub=sub, opp=opp, rows=rows, contact=answers.get("_contact"),
                           docs=docs, can_docs=F.can_view_documents(db, g.user, opp),
                           can_edit=user_can(db, g.user, "crm.edit"),
                           ignored=json.loads(sub["ignored_fields"] or "[]"),
                           consent=json.loads(sub["consent"] or "{}"), applied=json.loads(sub["applied_fields"] or "[]"))


@bp.route("/send/<int:opp_id>", methods=["POST"])
@login_required
def send(opp_id):
    db = get_db()
    if not can("crm.view"):
        deny("crm.view", "opportunity", opp_id)
    opp = F.load_opportunity(db, opp_id)
    if not opp:
        abort(404)
    f = request.form
    base = __import__("teamos.messaging", fromlist=["public_base"]).public_base(request)
    try:
        r = F.create_invitation(db, g.user, opp_id, f.get("template_key"), f.get("channel"),
                                f.getlist("fields"), f.getlist("docs"), f.get("purpose"), link_base=base)
    except ValidationError as e:
        flash(" ".join(str(v) for v in e.errors.values()), "error")
        return redirect(F.opp_path(opp))
    inv = db.one("SELECT * FROM form_invitations WHERE id = ?", (r["invitation_id"],))
    comm = db.one("SELECT * FROM messages WHERE id = ?", (r["communication"]["id"],)) \
        if r["communication"] and r["communication"].get("id") else None
    return render_template("forms/sent.html", opp=opp, inv=inv, link=r["link"], comm=comm,
                           back=F.opp_path(opp), MOCK_LABEL=messaging.PROVIDER_LABEL)


@bp.route("/invitations/<int:inv_id>/revoke", methods=["POST"])
@login_required
def revoke(inv_id):
    db = get_db()
    inv = db.one("SELECT * FROM form_invitations WHERE id = ?", (inv_id,))
    if not inv:
        abort(404)
    if not can("crm.view"):
        deny("crm.view", "opportunity", inv["opportunity_id"])
    try:
        F.revoke(db, g.user, inv_id)
        flash("Link revoked. It no longer works.", "ok")
    except ValidationError as e:
        flash(" ".join(str(v) for v in e.errors.values()), "error")
    return redirect(F.opp_path(F.load_opportunity(db, inv["opportunity_id"])))


@bp.route("/documents/<int:doc_id>")
@login_required
def document(doc_id):
    db = get_db()
    try:
        doc, data = F.open_document(db, g.user, doc_id)
    except ValidationError:
        abort(404)
    resp = Response(data, mimetype=doc["content_type"])
    safe = "".join(ch for ch in doc["original_name"] if ch.isalnum() or ch in "._- ")[:80] or "document"
    resp.headers["Content-Disposition"] = f'attachment; filename="{safe}"'
    resp.headers["Cache-Control"] = "no-store"
    return resp


@bp.route("/documents/<int:doc_id>/review", methods=["POST"])
@login_required
def document_review(doc_id):
    db = get_db()
    doc = db.one("SELECT * FROM documents WHERE id = ?", (doc_id,))
    if not doc:
        abort(404)
    try:
        F.review_document(db, g.user, doc_id, request.form.get("status"), request.form.get("note"))
        flash("Document review saved.", "ok")
    except ValidationError as e:
        flash(" ".join(str(v) for v in e.errors.values()), "error")
    return redirect(request.referrer or F.opp_path(F.load_opportunity(db, doc["opportunity_id"])))


@bp.app_context_processor
def _collect_helper():
    """Record pages: what is still missing, so the team knows what to ask (call or short form)."""
    def collect_gaps(opp):
        from ..domain import collect as C
        if not g.get("user") or not user_can(get_db(), g.user, "crm.view"):
            return None
        return C.gaps(get_db(), opp)
    return {"collect_gaps": collect_gaps}
