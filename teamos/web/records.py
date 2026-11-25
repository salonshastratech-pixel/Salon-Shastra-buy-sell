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
from ..security import login_required, require
from ..timeutil import parse_local_input

bp = Blueprint("records", __name__, url_prefix="/records")


def _back(kind: str):
    nxt = request.form.get("next") or ""
    if nxt.startswith("/") and not nxt.startswith("//"):
        return redirect(nxt)
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
