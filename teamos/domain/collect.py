"""What is still missing on a record — so the team knows exactly what to ask next (call or short form).

Customers may skip anything; nothing on a form is compulsory. The gaps land here instead: the facts that matter most
for matching and for the listing, in the order a consultant would ask them, with whether the customer already said
"I don't know" (ask differently on the call) and which photos/documents have not arrived yet.
"""
from __future__ import annotations

from . import buyer_fields as BF
from . import buyers as B
from . import form_templates as FT
from . import seller_fields as SF
from . import sellers as S

# What a consultant must know before matching or listing, most important first.
SELLER_ESSENTIALS = [
    "asking_price", "monthly_rent", "maintenance_monthly", "rent_deposit", "deposit_treatment", "lease_expiry",
    "lease_transfer_status", "floor_level", "area_type", "parking", "carpet_area_sqft", "chairs", "gender_format",
    "positioning", "started_year", "avg_monthly_sales", "avg_monthly_expenses", "staff_headcount",
    "staff_continuity", "transfer_authority", "negotiable", "equipment_condition", "address_line", "maps_link",
]
BUYER_ESSENTIALS = [
    "all_in_max", "need_type", "decision_maker", "cash_ready", "finance_dependence", "preferred_city",
    "max_monthly_rent", "chairs_min", "parking_need", "purchase_window", "min_lease_years_left", "involvement",
]
SELLER_DOCS = ["lease_agreement", "pos_report", "electricity_bill", "licence_doc"]
BUYER_DOCS = ["funding_proof"]
STATE_NOTE = {"unknown": "said they don't know", "prefer_to_discuss": "wants to discuss on a call",
              "not_available": "not available"}


def gaps(db, opp: dict, limit: int = 8) -> dict:
    """{'items': [{key, label, note}], 'photos': [(type, label)], 'docs': [(type, label)], 'extra': [str], 'total': n}"""
    if opp["kind"] == "seller":
        rec = S.load_listing(db, opp["listing_id"]) if opp.get("listing_id") else None
        prov = S.provenance(db, opp["listing_id"]) if rec else {}
        keys, defs, docs = SELLER_ESSENTIALS, SF.BY_KEY, SELLER_DOCS
    else:
        rec = B.load_mandate(db, opp["mandate_id"]) if opp.get("mandate_id") else None
        prov = B.provenance(db, opp["mandate_id"]) if rec else {}
        keys, defs, docs = BUYER_ESSENTIALS, BF.BY_KEY, BUYER_DOCS
    if not rec:
        return {"items": [], "photos": [], "docs": [], "extra": [], "total": 0}
    items = []
    for k in keys:
        fd = defs[k]
        if fd.show_if and rec.get(fd.show_if[0]) != fd.show_if[1]:
            continue
        p = prov.get(k)
        state = p["answer_state"] if p else None
        if rec.get(k) is not None or state == "not_applicable":
            continue
        items.append({"key": k, "label": fd.label, "note": STATE_NOTE.get(state, "")})
    have = {r["doc_type"] for r in db.all("SELECT DISTINCT doc_type FROM documents WHERE opportunity_id = ? AND "
                                          "review_status <> 'rejected'", (opp["id"],))}
    labels = dict(FT.DOC_TYPES[opp["kind"]])
    photos = [(k, l) for k, l in FT.PHOTO_TYPES if k not in have] if opp["kind"] == "seller" else []
    missing_docs = [(k, labels[k]) for k in docs if k not in have and k in labels]
    extra = []
    if opp["kind"] == "buyer":
        if not db.scalar("SELECT COUNT(*) FROM buyer_localities WHERE mandate_id = ? AND kind = 'preferred'",
                         (opp["mandate_id"],)):
            extra.append("Preferred areas (ranked)")
        dims = {r["dimension"] for r in db.all("SELECT DISTINCT dimension FROM buyer_preferences WHERE mandate_id = ?",
                                                (opp["mandate_id"],))}
        for dim, label, _ in BF.CHOICE_DIMENSIONS:
            if dim in ("floor_level", "area_type", "gender_format") and dim not in dims:
                extra.append(f"{label} preference")
    total = len(items) + len(photos) + len(missing_docs) + len(extra)
    return {"items": items[:limit], "more": max(0, len(items) - limit), "photos": photos, "docs": missing_docs,
            "extra": extra, "total": total, "all_keys": [i["key"] for i in items]}
