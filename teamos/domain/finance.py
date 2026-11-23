"""Admin-only company finance (PRD §11.5; FR-020, FR-050; UAT-18, UAT-19, UAT-47; Milestone 6).

Two kinds of money must never be confused (§3.2):
  * the parties' SALE CONSIDERATION (buyer pays seller)   — never Salon Shastra revenue;
  * Salon Shastra's own FEES, invoices, receipts and costs — visible only with `finance.view`.

No fee policy is hard-coded (decisions D-02..D-05 are open): each agreement records who pays, the basis
(fixed or % of the agreed consideration), and when it is earned. Earned fee = fixed amount, or rate × the deal's
agreed consideration, once the earning condition is met. Due = earned − received (before tax/adjustments).
Tax is shown only where a rate has been entered on an invoice; nothing is assumed (D-05).
"""
from __future__ import annotations

from datetime import date

from .. import audit
from ..permissions import user_can
from ..timeutil import now_iso
from .common import parse_float, parse_inr
from .errors import PermissionDenied, ValidationError

PAYERS = [("seller", "Seller"), ("buyer", "Buyer")]
FEE_TYPES = [("success", "Success fee"), ("consultation", "Consultation"), ("assessment", "Assessment"),
             ("other", "Other")]
EARNING = [("on_closing", "When the deal closes"), ("on_agreement", "When the agreement is signed"),
           ("on_handover", "At handover"), ("on_signing", "On signing the engagement"),
           ("manual", "When an admin marks it earned")]
EXPENSE_CATEGORIES = ["Marketing", "Salaries", "Travel", "Software", "Office", "Legal / professional", "Other"]
STAGE_ORDER = ["introduced", "visit_scheduled", "visit_completed", "offer_received", "negotiation", "due_diligence",
               "agreement", "handover", "closed_won"]


def _require(db, actor, cap):
    if not user_can(db, actor, cap):
        audit.record(db, actor["id"] if actor else None, "finance.denied", None, None, {"capability": cap},
                     outcome="denied")
        raise PermissionDenied(cap)


def _reached(stage: str | None, target: str) -> bool:
    if stage not in STAGE_ORDER:
        return False
    return STAGE_ORDER.index(stage) >= STAGE_ORDER.index(target)


def evaluate(agreement: dict, deal: dict | None, received: int, invoiced: int) -> dict:
    stage = deal["stage"] if deal else None
    cond = agreement["earning_condition"]
    met = {"on_signing": True, "on_agreement": _reached(stage, "agreement"), "on_handover": _reached(stage, "handover"),
           "on_closing": stage == "closed_won", "manual": bool(agreement.get("earned_manually_at"))}[cond]
    basis_amount = None
    note = None
    if agreement["basis"] == "fixed":
        contractual = agreement["fixed_amount"]
    else:
        basis_amount = deal["agreed_consideration"] if deal else None
        if basis_amount is None:
            contractual = None
            note = "Percentage fee — waiting for the agreed sale consideration"
        else:
            contractual = round(basis_amount * (agreement["rate_pct"] or 0) / 100)
    earned = contractual if (met and contractual is not None and agreement["status"] == "active") else 0
    return {"contractual": contractual, "basis_amount": basis_amount, "earned": earned, "earned_condition_met": met,
            "received": received, "invoiced": invoiced, "due": max(0, earned - received),
            "unearned_received": max(0, received - earned), "note": note}


def ledger(db, actor) -> dict:
    _require(db, actor, "finance.view")
    rows = db.all(
        "SELECT a.*, d.ref_code AS deal_ref, d.stage AS deal_stage, d.agreed_consideration, d.closed_at, "
        "(SELECT COALESCE(SUM(amount),0) FROM fee_receipts r WHERE r.agreement_id = a.id) AS received, "
        "(SELECT COALESCE(SUM(amount),0) FROM fee_invoices i WHERE i.agreement_id = a.id) AS invoiced, "
        "(SELECT COALESCE(SUM(tax_amount),0) FROM fee_invoices i WHERE i.agreement_id = a.id) AS tax_invoiced "
        "FROM fee_agreements a LEFT JOIN deals d ON d.id = a.deal_id ORDER BY a.created_at DESC")
    totals = {"contractual": 0, "earned": 0, "invoiced": 0, "received": 0, "due": 0, "consideration_closed": 0}
    for r in rows:
        deal = {"stage": r["deal_stage"], "agreed_consideration": r["agreed_consideration"]} if r["deal_id"] else None
        r["calc"] = evaluate(r, deal, r["received"], r["invoiced"])
        if r["status"] == "active":
            totals["contractual"] += r["calc"]["contractual"] or 0
            totals["earned"] += r["calc"]["earned"]
            totals["due"] += r["calc"]["due"]
        totals["invoiced"] += r["invoiced"]
        totals["received"] += r["received"]
    totals["consideration_closed"] = db.scalar("SELECT COALESCE(SUM(agreed_consideration),0) FROM deals "
                                               "WHERE stage = 'closed_won'")
    expenses = db.all("SELECT e.*, d.ref_code AS deal_ref FROM company_expenses e LEFT JOIN deals d ON d.id = e.deal_id "
                      "ORDER BY spent_on DESC LIMIT 100")
    totals["expenses"] = sum(e["amount"] for e in expenses)
    totals["net_received"] = totals["received"] - totals["expenses"]
    deals = db.all("SELECT d.id, d.ref_code, d.stage, d.agreed_consideration, d.closed_at FROM deals d "
                   "ORDER BY d.created_at DESC")  # limited deal references (§3.2) — no CRM details
    return {"rows": rows, "totals": totals, "expenses": expenses, "deals": deals,
            "invoices": db.all("SELECT i.*, a.payer, d.ref_code AS deal_ref FROM fee_invoices i JOIN fee_agreements a "
                               "ON a.id = i.agreement_id LEFT JOIN deals d ON d.id = a.deal_id ORDER BY i.issued_on DESC"),
            "receipts": db.all("SELECT r.*, a.payer, d.ref_code AS deal_ref FROM fee_receipts r JOIN fee_agreements a "
                               "ON a.id = r.agreement_id LEFT JOIN deals d ON d.id = a.deal_id ORDER BY r.received_on DESC")}


def _date(v, field):
    v = (v or "").strip()
    if not v:
        raise ValidationError({field: "Enter a date."})
    try:
        date.fromisoformat(v)
    except ValueError:
        raise ValidationError({field: "Use a valid date."})
    return v


def add_agreement(db, actor, f: dict) -> int:
    _require(db, actor, "finance.edit")
    if f.get("payer") not in dict(PAYERS) or f.get("fee_type") not in dict(FEE_TYPES) \
            or f.get("earning_condition") not in dict(EARNING) or f.get("basis") not in ("fixed", "percent"):
        raise ValidationError({"agreement": "Choose payer, fee type, basis and when it is earned."})
    deal_id = int(f["deal_id"]) if str(f.get("deal_id") or "").isdigit() else None
    if not deal_id:
        raise ValidationError({"deal_id": "Choose the deal this fee belongs to."})
    try:
        fixed = parse_inr(f.get("fixed_amount")) if f["basis"] == "fixed" else None
        rate = parse_float(f.get("rate_pct"), 0, 100) if f["basis"] == "percent" else None
    except ValueError as e:
        raise ValidationError({"amount": str(e)})
    if f["basis"] == "fixed" and not fixed:
        raise ValidationError({"fixed_amount": "Enter the fixed fee amount."})
    if f["basis"] == "percent" and not rate:
        raise ValidationError({"rate_pct": "Enter the percentage."})
    now = now_iso()
    with db:
        prev = db.one("SELECT id, version FROM fee_agreements WHERE deal_id = ? AND payer = ? AND fee_type = ? "
                      "AND status = 'active'", (deal_id, f["payer"], f["fee_type"]))
        version = 1
        if prev:  # a change of terms supersedes the previous version (history kept)
            db.update("fee_agreements", prev["id"], {"status": "superseded", "updated_at": now})
            version = prev["version"] + 1
        aid = db.insert("fee_agreements", {
            "deal_id": deal_id, "payer": f["payer"], "fee_type": f["fee_type"], "basis": f["basis"],
            "fixed_amount": fixed, "rate_pct": rate, "earning_condition": f["earning_condition"],
            "agreement_ref": (f.get("agreement_ref") or "").strip() or None, "version": version, "status": "active",
            "note": (f.get("note") or "").strip() or None, "created_by": actor["id"], "created_at": now, "updated_at": now})
        if prev:  # carry invoices/receipts recorded against the earlier version
            db.execute("UPDATE fee_invoices SET agreement_id = ? WHERE agreement_id = ?", (aid, prev["id"]))
            db.execute("UPDATE fee_receipts SET agreement_id = ? WHERE agreement_id = ?", (aid, prev["id"]))
        audit.record(db, actor["id"], "finance.agreement", "deal", deal_id,
                     {"agreement": aid, "version": version, "basis": f["basis"], "payer": f["payer"]})
    return aid


def mark_earned(db, actor, agreement_id: int) -> None:
    _require(db, actor, "finance.edit")
    a = db.one("SELECT * FROM fee_agreements WHERE id = ?", (agreement_id,))
    if not a or a["earning_condition"] != "manual":
        raise ValidationError("Only agreements earned 'when an admin marks it' can be marked manually.")
    with db:
        db.update("fee_agreements", agreement_id, {"earned_manually_at": now_iso(), "updated_at": now_iso()})
        audit.record(db, actor["id"], "finance.marked_earned", "fee_agreement", agreement_id, {})


def add_invoice(db, actor, f: dict) -> int:
    _require(db, actor, "finance.edit")
    aid = int(f.get("agreement_id") or 0)
    if not db.scalar("SELECT id FROM fee_agreements WHERE id = ? AND status = 'active'", (aid,)):
        raise ValidationError({"agreement_id": "Choose an active fee agreement."})
    if not (f.get("invoice_no") or "").strip():
        raise ValidationError({"invoice_no": "Enter the invoice number."})
    try:
        amount = parse_inr(f.get("amount"))
        tax = parse_float(f.get("tax_pct"), 0, 100) if (f.get("tax_pct") or "").strip() else None
    except ValueError as e:
        raise ValidationError({"amount": str(e)})
    if not amount:
        raise ValidationError({"amount": "Enter the invoice amount (before tax)."})
    with db:
        iid = db.insert("fee_invoices", {"agreement_id": aid, "invoice_no": f["invoice_no"].strip()[:40], "amount": amount,
                                         "tax_pct": tax, "tax_amount": round(amount * tax / 100) if tax is not None else None,
                                         "issued_on": _date(f.get("issued_on"), "issued_on"),
                                         "note": (f.get("note") or "").strip() or None, "created_by": actor["id"],
                                         "created_at": now_iso()})
        audit.record(db, actor["id"], "finance.invoice", "fee_agreement", aid, {"invoice": iid})
    return iid


def add_receipt(db, actor, f: dict) -> int:
    _require(db, actor, "finance.edit")
    aid = int(f.get("agreement_id") or 0)
    if not db.scalar("SELECT id FROM fee_agreements WHERE id = ? AND status = 'active'", (aid,)):
        raise ValidationError({"agreement_id": "Choose an active fee agreement."})
    try:
        amount = parse_inr(f.get("amount"))
    except ValueError as e:
        raise ValidationError({"amount": str(e)})
    if not amount:
        raise ValidationError({"amount": "Enter the amount received."})
    with db:
        rid = db.insert("fee_receipts", {"agreement_id": aid, "amount": amount,
                                         "received_on": _date(f.get("received_on"), "received_on"),
                                         "mode": (f.get("mode") or "").strip() or None,
                                         "reference": (f.get("reference") or "").strip() or None,
                                         "note": (f.get("note") or "").strip() or None, "created_by": actor["id"],
                                         "created_at": now_iso()})
        audit.record(db, actor["id"], "finance.receipt", "fee_agreement", aid, {"receipt": rid, "amount": amount})
    return rid


def add_expense(db, actor, f: dict) -> int:
    _require(db, actor, "finance.edit")
    if f.get("category") not in EXPENSE_CATEGORIES:
        raise ValidationError({"category": "Choose a category."})
    try:
        amount = parse_inr(f.get("amount"))
    except ValueError as e:
        raise ValidationError({"amount": str(e)})
    if not amount:
        raise ValidationError({"amount": "Enter the amount."})
    with db:
        eid = db.insert("company_expenses", {"category": f["category"], "amount": amount,
                                             "spent_on": _date(f.get("spent_on"), "spent_on"),
                                             "deal_id": int(f["deal_id"]) if str(f.get("deal_id") or "").isdigit() else None,
                                             "note": (f.get("note") or "").strip() or None, "created_by": actor["id"],
                                             "created_at": now_iso()})
        audit.record(db, actor["id"], "finance.expense", None, None, {"expense": eid, "amount": amount})
    return eid
