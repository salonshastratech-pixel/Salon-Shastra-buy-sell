"""Actions on many records at once from the Salon Owners / Buyers lists (assign, follow-up task, delete), CSV export
of a list, and permanent delete of one record — the everyday CRM tools for the team and the admin."""
from __future__ import annotations

import csv
import io

from flask import Blueprint, Response, abort, flash, g, redirect, request, url_for

from .. import audit
from ..db import get_db
from ..domain import buyers as B
from ..domain import purge
from ..domain import sellers as S
from ..domain import tasks as T
from ..domain.errors import PermissionDenied, ValidationError
from ..permissions import user_can
from ..security import login_required, require, safe_next
from ..timeutil import parse_local_input

bp = Blueprint("records", __name__, url_prefix="/records")


def _back(kind: str):
    nxt = request.form.get("next") or ""
    if safe_next(nxt, ""):
        return redirect(safe_next(nxt, ""))
    return redirect(url_for("buyers.index" if kind == "buyer" else "sellers.index"))


def _msg(e) -> str:
    return " ".join(str(v) for v in e.errors.values()) if isinstance(e, ValidationError) and isinstance(e.errors, dict) \
        else str(getattr(e, "errors", e))


@bp.route("/bulk", methods=["POST"])
@login_required
def bulk():
    db = get_db()
    f = request.form
    kind = "buyer" if f.get("kind") == "buyer" else "seller"
    ids = [int(x) for x in f.getlist("ids") if x.isdigit()][:500]
    action = f.get("action") or ""
    if not ids:
        flash("Tick at least one record first.", "error")
        return _back(kind)
    mod = B if kind == "buyer" else S
    rows = [mod.load(db, i) for i in ids]
    rows = [r for r in rows if r and r["kind"] == kind]
    done, failed = 0, []
    try:
        if action == "assign":
            owner = int(f.get("owner_id") or 0)
            for r in rows:
                try:
                    mod.assign_owner(db, g.user, r, owner, f.get("reason") or "Bulk reassignment")
                    done += 1
                except (ValidationError, PermissionDenied) as e:
                    failed.append(f"{r['ref_code']}: {_msg(e)}")
            flash(f"Owner changed on {done} record(s)." + (f" Not changed: {'; '.join(failed[:5])}" if failed else ""),
                  "ok" if done else "error")
        elif action == "task":
            title = (f.get("title") or "").strip()
            due = parse_local_input(f.get("due_at"))
            T.need_when(due)
            for r in rows:
                try:
                    with db:
                        T.create_task(db, g.user, title=title, assignee_id=int(f.get("assignee_id") or 0)
                                      or r["primary_owner_id"], due_at=due, task_type="follow_up",
                                      entity_type="opportunity", entity_id=r["id"])
                    done += 1
                except (ValidationError, PermissionDenied) as e:
                    failed.append(f"{r['ref_code']}: {_msg(e)}")
            flash(f"Task added to {done} record(s)." + (f" Not added: {'; '.join(failed[:5])}" if failed else ""),
                  "ok" if done else "error")
        elif action == "delete":
            if (f.get("confirm") or "").strip().upper() != "DELETE":
                flash("Type DELETE to confirm — deleted records cannot be brought back.", "error")
                return _back(kind)
            res = purge.delete_records(db, g.user, [r["id"] for r in rows], f.get("reason") or "")
            flash(f"Deleted {res['records']} record(s) for good: {', '.join(res['refs'][:10])}"
                  + ("…" if len(res["refs"]) > 10 else ""), "ok")
        else:
            flash("Choose what to do with the ticked records.", "error")
    except PermissionDenied:
        abort(403)
    except ValidationError as e:
        flash(_msg(e), "error")
    return _back(kind)


@bp.route("/<int:opp_id>/delete", methods=["POST"])
@require("users.manage")
def delete_one(opp_id):
    db = get_db()
    opp = db.one("SELECT id, kind, ref_code FROM opportunities WHERE id = ?", (opp_id,))
    if not opp:
        abort(404)
    if (request.form.get("confirm") or "").strip().upper() != opp["ref_code"].upper():
        flash(f"Type {opp['ref_code']} to confirm — a deleted record cannot be brought back.", "error")
        return redirect(url_for("buyers.detail" if opp["kind"] == "buyer" else "sellers.detail", opp_id=opp_id))
    try:
        purge.delete_records(db, g.user, [opp_id], request.form.get("reason") or "")
    except ValidationError as e:
        flash(_msg(e), "error")
        return redirect(url_for("buyers.detail" if opp["kind"] == "buyer" else "sellers.detail", opp_id=opp_id))
    flash(f"{opp['ref_code']} deleted for good.", "ok")
    return redirect(url_for("buyers.index" if opp["kind"] == "buyer" else "sellers.index"))


# -- CSV export of a list (admins) ------------------------------------------------------------------------------------
SELLER_COLS = [("ref_code", "Ref"), ("salon_name", "Salon"), ("contact_name", "Contact"), ("phone_normalized", "Mobile"),
               ("locality", "Area"), ("city", "City"), ("stage", "Stage"), ("status", "Status"), ("owner_name", "Owner"),
               ("source", "Source"), ("source_detail", "Source detail"), ("lead_created_at", "Lead came in"),
               ("first_contact_at", "First contact"), ("last_contact_at", "Last contact"), ("next_action", "Next action"),
               ("next_action_due", "Next action due"), ("chairs", "Chairs"), ("asking_price", "Asking price"),
               ("form_status", "Form"), ("form_answered", "Answers given")]
BUYER_COLS = [("ref_code", "Ref"), ("contact_name", "Name"), ("phone_normalized", "Mobile"), ("contact_city", "City"),
              ("stage", "Stage"), ("status", "Status"), ("owner_name", "Owner"), ("source", "Source"),
              ("source_detail", "Source detail"), ("need_type", "Wants"), ("all_in_max", "Budget (all-in)"),
              ("preferred_city", "Preferred city"), ("purchase_window", "Timeline"), ("funding_status", "Funding"),
              ("lead_created_at", "Lead came in"), ("first_contact_at", "First contact"),
              ("last_contact_at", "Last contact"), ("next_action", "Next action"), ("next_action_due", "Next action due")]


def export_csv(kind: str, rows: list[dict], who: dict) -> Response:
    if not user_can(get_db(), who, "users.manage"):
        abort(403)
    cols = BUYER_COLS if kind == "buyer" else SELLER_COLS
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([label for _k, label in cols])
    for r in rows:
        w.writerow(["" if r.get(k) is None else r.get(k) for k, _l in cols])
    audit.record(get_db(), who["id"], "list.exported", kind, None, {"rows": len(rows)})
    name = "salon-owners" if kind == "seller" else "buyers-investors"
    return Response(chr(0xFEFF) + buf.getvalue(), mimetype="text/csv",  # BOM: Excel opens Telugu / ₹ correctly
                    headers={"Content-Disposition": f'attachment; filename="{name}.csv"', "Cache-Control": "no-store"})


# -- Admin: who gets new leads, and start fresh ----------------------------------------------------------------------
@bp.route("/manage")
@require("users.manage")
def manage():
    from flask import render_template

    from ..domain import progress as PR
    db = get_db()
    counts = {r["kind"]: r["n"] for r in db.all("SELECT kind, COUNT(*) AS n FROM opportunities GROUP BY kind")}
    from ..domain import rebuild as RB
    sheets = [r["url"] for r in db.all("SELECT url FROM connected_sheets WHERE active = 1")]
    return render_template("admin/leads_data.html", split=PR.current_split(db), counts=counts,
                           clear=purge.clear_status(db), CONFIRM_WORD=purge.CONFIRM_WORD, rebuild=RB.status(db),
                           sheet_links=chr(10).join(sheets),
                           excluded=db.scalar("SELECT COUNT(*) FROM lead_exclusions"),
                           wa_all=__import__("teamos.domain.wa_leads", fromlist=["x"]).all_chats_on(db))


@bp.route("/split", methods=["POST"])
@require("users.manage")
def split():
    from ..domain import progress as PR
    db = get_db()
    f = request.form
    try:
        moved = PR.set_split(db, g.user, [int(x) for x in f.getlist("seller_ids") if x.isdigit()],
                             [int(x) for x in f.getlist("buyer_ids") if x.isdigit()], f.get("move_existing") == "1")
        flash("Saved. New leads now go to the chosen people in turn — from forms, sheets, ads and imports."
              + (f" Moved {moved['seller']} salon owner(s) and {moved['buyer']} investor(s) with their open tasks."
                 if f.get("move_existing") == "1" else ""), "ok")
    except ValidationError as e:
        flash(_msg(e), "error")
    except PermissionDenied:
        abort(403)
    return redirect(url_for("records.manage"))


@bp.route("/clear-all", methods=["POST"])
@require("users.manage")
def clear_all():
    from flask import current_app

    from .. import background
    db = get_db()
    try:
        ids = purge.check_clear_all(db, g.user, request.form.get("confirm"), request.form.get("reason"))
    except ValidationError as e:
        flash(_msg(e), "error")
        return redirect(url_for("records.manage"))
    except PermissionDenied:
        abort(403)
    actor, reason = dict(g.user), request.form.get("reason")
    background.run(current_app._get_current_object(), "clear_all", lambda bdb: purge.clear_all(bdb, actor, ids, reason))
    flash(f"Removing {len(ids)} record(s) and everything linked to them. This page shows the progress.", "ok")
    return redirect(url_for("records.manage"))


@bp.route("/review")
@require("crm.view")
def review():
    """Lead review: records that probably don't belong any more, each with the reason — tick and remove."""
    from flask import render_template

    from ..domain import lead_review as LR
    kind = "buyer" if request.args.get("kind") == "buyer" else "seller"
    why = request.args.get("why") or ""
    res = LR.review(get_db(), kind, why if why in LR.REASON_LABELS else "")
    return render_template("admin/lead_review.html", kind=kind, why=why, rows=res["rows"], counts=res["counts"],
                           REASONS=LR.REASONS)


@bp.route("/rebuild", methods=["POST"])
@require("users.manage")
def rebuild_from_sheets():
    """Bring the team's sheets in as the source of truth: dry run (report only) or apply. Runs in the background."""
    from flask import current_app

    from .. import background
    from ..domain import rebuild as RB
    db = get_db()
    urls = [u.strip() for u in (request.form.get("links") or "").split() if "docs.google.com/spreadsheets" in u]
    mode = "apply" if request.form.get("mode") == "apply" else "dry"
    if not urls:
        flash("Paste at least one Google Sheets link (shared as 'Anyone with the link can view').", "error")
        return redirect(url_for("records.manage") + "#rebuild")
    if mode == "apply" and (request.form.get("confirm") or "").strip().upper() != "APPLY":
        flash("Type APPLY to confirm — or run a dry run first to see exactly what will change.", "error")
        return redirect(url_for("records.manage") + "#rebuild")
    st = RB.status(db)
    if st and st.get("state") == "running":
        flash("A sheets rebuild is already running.", "error")
        return redirect(url_for("records.manage") + "#rebuild")
    actor_id = g.user["id"]
    with db:
        db.execute("DELETE FROM automation_settings WHERE key = ?", (RB.KEY,))
        db.insert("automation_settings", {"key": RB.KEY, "enabled": 1, "note": '{"state": "running", "mode": "%s", '
                  '"step": "starting"}' % mode, "updated_at": __import__("teamos.timeutil", fromlist=["now_iso"]).now_iso()})
    background.run(current_app._get_current_object(), "sheets_rebuild",
                   lambda bdb: RB.run(bdb, bdb.one("SELECT * FROM users WHERE id = ?", (actor_id,)), urls, mode))
    flash(("Dry run started — the report appears below in a minute or two. Nothing is changed."
           if mode == "dry" else "Rebuild started — this takes a few minutes. Progress shows below."), "ok")
    return redirect(url_for("records.manage") + "#rebuild")


@bp.route("/<int:opp_id>/pass", methods=["POST"])
@require("crm.assign")
def pass_lead(opp_id):
    """'Pass' on My Day: give a lead to a colleague - its open tasks and history go with it, they are told."""
    db = get_db()
    kind = db.scalar("SELECT kind FROM opportunities WHERE id = ?", (opp_id,))
    if kind not in ("seller", "buyer"):
        abort(404)
    mod = S if kind == "seller" else B
    opp = mod.load(db, opp_id)
    to = int(request.form.get("to_id") or 0)
    note = " ".join((request.form.get("note") or "").split())[:200]
    back = safe_next(request.form.get("next"), url_for("main.my_day"))
    if to == opp["primary_owner_id"]:
        flash("They already own it.", "error")
        return redirect(back)
    try:
        mod.assign_owner(db, g.user, opp, to, f"Passed by {g.user['full_name']}" + (f": {note}" if note else ""))
        name = db.scalar("SELECT full_name FROM users WHERE id = ?", (to,))
        flash(f"{opp['ref_code']} passed to {name} with its open tasks. They have been told.", "ok")
    except ValidationError as e:
        flash("Not passed: " + _msg(e), "error")
    except PermissionDenied:
        abort(403)
    return redirect(back)


@bp.route("/<int:opp_id>/basics", methods=["POST"])
@require("crm.edit")
def edit_basics(opp_id):
    """The Edit panel on a salon owner / buyer profile: name, numbers, e-mail, salon, place, hot/warm/cold, category,
    team remark."""
    from ..domain import basics as BA
    db = get_db()
    kind = db.scalar("SELECT kind FROM opportunities WHERE id = ?", (opp_id,))
    if kind not in ("seller", "buyer"):
        abort(404)
    opp = (S.load(db, opp_id) if kind == "seller" else B.load(db, opp_id))
    target = url_for("sellers.detail" if kind == "seller" else "buyers.detail", opp_id=opp_id)
    try:
        changed = BA.update(db, g.user, opp, request.form)
        flash(("Saved: " + ", ".join(changed)) if changed else "Nothing changed.", "ok")
    except ValidationError as e:
        flash("Not saved: " + " ".join(e.errors.values()), "error")
        return redirect(target + "?edit=1#edit")
    except PermissionDenied:
        abort(403)
    return redirect(target)



@bp.route("/wa-chats", methods=["POST"])
@require("users.manage")
def wa_chats():
    """Switch: should every new WhatsApp chat become a lead, or only chats that come from a Meta ad?"""
    from ..domain import wa_leads as WL
    from ..timeutil import now_iso
    db = get_db()
    on = 1 if request.form.get("on") == "1" else 0
    with db:
        if db.scalar("SELECT 1 FROM automation_settings WHERE key = ?", (WL.ALL_CHATS_KEY,)):
            db.execute("UPDATE automation_settings SET enabled = ?, updated_at = ? WHERE key = ?",
                       (on, now_iso(), WL.ALL_CHATS_KEY))
        else:
            db.insert("automation_settings", {"key": WL.ALL_CHATS_KEY, "enabled": on, "updated_at": now_iso()})
    flash("Every new WhatsApp chat now becomes a lead." if on else "Only chats from Meta ads become leads.", "ok")
    return redirect(url_for("records.manage") + "#wa")


# -- Send the right form / move a lead to the right side ----------------------------------------------------------
def _wa(phone, text):
    from urllib.parse import quote
    digits = "".join(ch for ch in (phone or "") if ch.isdigit())
    return f"https://wa.me/{digits}?text={quote(text)}" if digits else f"https://wa.me/?text={quote(text)}"


def _detail(kind, opp_id):
    return url_for("sellers.detail" if kind == "seller" else "buyers.detail", opp_id=opp_id)


@bp.route("/<int:opp_id>/send-form", methods=["POST"])
@require("crm.view")
def send_form(opp_id):
    """'Send form' on a profile: the chosen form opens in WhatsApp, ready to send. A form for the other side first
    asks to move the lead there (a salon owner who answered a buyer ad, or the other way round)."""
    from .. import messaging
    from ..domain import forms as F
    from ..domain import investor as I
    from ..domain import pilot as P
    from ..domain import switch_side as SW
    db = get_db()
    opp = SW.load_any(db, opp_id)
    if not opp:
        abort(404)
    key = request.form.get("form") or ""
    if key not in SW.FORM_SIDE:
        flash("Choose a form to send.", "error")
        return redirect(_detail(opp["kind"], opp_id))
    if SW.FORM_SIDE[key] != opp["kind"]:
        return redirect(url_for("records.move", opp_id=opp_id, to=SW.FORM_SIDE[key], then=key))
    base = messaging.public_base(request)
    try:
        if key in ("owner_checkup", "investor"):
            M = P if key == "owner_checkup" else I
            token = P.create_link(db, g.user, opp)     # one personal link: form, report and booking with Vikas
            text = M.form_message(opp, f"{base}/s/{token}")
            if M.profile(db, opp_id)["form_status"] in ("not_sent", "not_filled"):
                M.set_form_status(db, g.user, opp, "sent")
        else:
            r = F.create_invitation(db, g.user, opp_id, "seller_deep" if key == "owner_sell" else "buyer_deep",
                                    "copy_link", link_base=base)
            text = SW.deep_form_message(opp, r["link"], key)
    except PermissionDenied:
        abort(403)
    except ValidationError as e:
        flash(_msg(e), "error")
        return redirect(_detail(opp["kind"], opp_id))
    T.add_activity(db, g.user["id"], "opportunity", opp_id, "note",
                   f"Sent on WhatsApp: {SW.FORM_LABEL[key]} (answers are kept if they stop half-way)")
    return redirect(_wa(opp.get("phone_normalized"), text))


@bp.route("/<int:opp_id>/move", methods=["GET", "POST"])
@require("crm.edit")
def move(opp_id):
    from flask import render_template

    from ..domain import switch_side as SW
    from ..domain.lead_marks import CATEGORIES
    db = get_db()
    opp = SW.load_any(db, opp_id)
    if not opp:
        abort(404)
    to = request.values.get("to") or ("buyer" if opp["kind"] == "seller" else "seller")
    then = request.values.get("then") or ""
    then = then if SW.FORM_SIDE.get(then) == to else ""
    errors, values = {}, SW.defaults(db, opp)
    if request.method == "POST":
        values = {"salon_name": request.form.get("salon_name", ""), "city": request.form.get("city", ""),
                  "locality": request.form.get("locality", ""),
                  "categories": request.form.getlist("lead_category"), "reason": request.form.get("reason", "")}
        try:
            new_id = SW.move(db, g.user, opp_id, to, request.form, keep_old=request.form.get("keep_old") == "1")
        except PermissionDenied:
            abort(403)
        except ValidationError as e:
            errors = e.errors if isinstance(e.errors, dict) else {"to": str(e.errors)}
        else:
            ref = db.scalar("SELECT ref_code FROM opportunities WHERE id = ?", (new_id,))
            flash(f"Moved to {SW.SIDE_LABEL[to]} as {ref}. Calls, notes and tasks came along.", "ok")
            if then:   # they chose a form of this side: the profile opens with that form ready to send
                return redirect(_detail(to, new_id) + f"?send={then}#send-form")
            return redirect(_detail(to, new_id))
    return render_template("records/move.html", opp=opp, to=to, then=then, values=values, errors=errors,
                           SW=SW, cats=CATEGORIES[to])
