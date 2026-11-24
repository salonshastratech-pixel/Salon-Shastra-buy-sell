"""Matching engine (PRD §8; Milestone 5). Deterministic, explainable, versioned.

For every (buyer, salon) pair it produces FOUR separate outputs — never collapsed into one number (§8.3):
  1. gate        pass | review | blocked           — hard conflicts and things that need a human look
  2. fit_score   0–100 over KNOWN applicable criteria  — weighted compatibility, not a deal probability
  3. coverage    % of applicable weight known on both sides (+ a confidence label)
  4. readiness   what the team may do next (information required … ready to approach)
plus an outcome (ready / near / blocked / insufficient) and the next questions that would unblock it.

Rules: unknown is never rewarded (it is excluded from fit and lowers coverage); "Not important" removes a
criterion; price is always the seller's ALL-IN cash requirement vs the buyer's ALL-IN ceiling; consent and
authority are never overridden by a score. The catalogue below is the "matching rules catalogue v0.1" —
weights are starting hypotheses (PRD §8.2, decision D-09) and the version is stored with every result.
"""
from __future__ import annotations

import json
from datetime import date

from .. import audit
from ..permissions import user_can
from ..timeutil import now_iso, now_utc
from . import buyer_fields as BFD
from . import buyers as B
from . import sellers as S
from . import tasks as T
from .common import format_inr
from .errors import PermissionDenied, ValidationError
from .seller_fields import BY_KEY as SF_BY_KEY

RULES_VERSION = "match-rules v0.2 (v0.1 + floor, type of area, parking, rent incl. maintenance)"

FAMILIES = {  # family: (label, weight)
    "A": ("Affordability & all-in cost", 20), "B": ("Location", 15), "C": ("Format & positioning", 10),
    "D": ("Deal structure & authority", 10), "E": ("Economics", 10), "F": ("Property & lease", 10),
    "G": ("Staff & handover", 8), "H": ("Timing", 8), "I": ("Asset quality & track record", 5),
    "J": ("Terms & flexibility", 4),
}
BASE_RULES_VERSION = RULES_VERSION
WEIGHTS = {k: v[1] for k, v in FAMILIES.items()}   # effective weights = defaults + founder-approved tuning (M8)


def load_weights(db) -> dict:
    """Apply founder-approved weight changes (Reports → Learning). The rules version records the tuning."""
    global RULES_VERSION
    weights = {k: v[1] for k, v in FAMILIES.items()}
    try:
        rows = db.all("SELECT id, family, new_weight FROM match_weight_changes WHERE status = 'approved' ORDER BY id")
    except Exception:  # table not migrated yet
        rows = []
    for r in rows:
        if r["family"] in weights:
            weights[r["family"]] = r["new_weight"]
    WEIGHTS.clear()
    WEIGHTS.update(weights)
    RULES_VERSION = BASE_RULES_VERSION + (f" · tuned #{rows[-1]['id']}" if rows else "")
    return weights


# criterion: (family, share of the family weight, label)
CRITERIA = {
    "all_in": ("A", 14, "All-in cash requirement vs ceiling"), "price_band": ("A", 4, "Asking price vs price band"),
    "cash_timing": ("A", 2, "Money ready vs seller timeline"),
    "city": ("B", 6, "City"), "locality": ("B", 9, "Locality / micro-market"), "area_type": ("B", 2, "Type of area"),
    "gender_format": ("C", 5, "Salon format"), "positioning": ("C", 3, "Positioning"),
    "business_model": ("C", 2, "Independent / franchise"),
    "sale_structure": ("D", 5, "Deal structure"), "authority": ("D", 5, "Seller's authority to transfer"),
    "sales": ("E", 6, "Monthly sales vs minimum"), "profitability": ("E", 4, "Profitability vs turnaround appetite"),
    "chairs": ("F", 4, "Chairs / stations"), "rent": ("F", 2, "Rent + maintenance vs maximum"),
    "floor_level": ("F", 1, "Floor"), "parking": ("F", 1, "Customer parking"),
    "lease": ("F", 3, "Lease remaining & transferability"), "area": ("F", 1, "Carpet area"),
    "staff": ("G", 4, "Staff continuity"), "transition": ("G", 4, "Seller transition support"),
    "timing": ("H", 8, "Purchase window vs sale timeline"),
    "track_record": ("I", 3, "Years in operation"), "equipment": ("I", 2, "Equipment condition"),
    "negotiable": ("J", 2, "Price flexibility"), "liabilities": ("J", 2, "Existing liabilities vs tolerance"),
}
SELL_PURPOSES = {"sell_entire", "partnership", "franchise_transfer"}
NEAR_GAP_INR = 3_00_000          # "₹3 lakh budget gap where seller is explicitly negotiable" (§8.4)
NEAR_GAP_PCT = 0.10
MIN_COVERAGE_TO_LIST = 30       # a pair with less than 30% of facts known is not listed yet (D-58)

READINESS = {
    "blocked": "Not suitable as things stand", "info_required": "Information required",
    "awaiting_verification": "Awaiting verification", "awaiting_consent": "Awaiting consent",
    "ready_to_approach": "Ready to approach", "in_conversation": "In conversation", "closed": "Closed / not suitable",
}
OUTCOMES = {"ready": "Ready candidate", "near": "Near match", "blocked": "Blocked", "insufficient": "Insufficient data"}
STAGES = {
    "suggested": "Suggested", "shortlisted": "Shortlisted", "approved_to_approach": "Approved to approach",
    "interest_confirmed": "Interest confirmed", "not_suitable": "Not suitable", "buyer_declined": "Buyer declined",
    "seller_declined": "Seller declined", "on_hold": "On hold",
}
SUPPRESSING = {"not_suitable", "buyer_declined", "seller_declined"}
REJECT_REASONS = [("budget", "Budget / all-in cost"), ("location", "Location"), ("rent", "Rent"),
                  ("format", "Format / positioning"), ("size", "Size / chairs"), ("lease", "Lease terms"),
                  ("financials", "Sales / financials"), ("timing", "Timing"), ("staff", "Staff / handover"),
                  ("not_interested", "Not interested"), ("other", "Other")]
FUTURE_STAGES = "Introductions, visits and offers arrive with Deals (Milestone 6)"


# -- Loading -------------------------------------------------------------------------
def _consent(db, contact_id, purpose) -> bool:
    return bool(db.scalar("SELECT granted FROM contact_consents WHERE contact_id = ? AND purpose = ? "
                          "ORDER BY captured_at DESC, id DESC LIMIT 1", (contact_id, purpose)))


def load_buyer(db, opp_id: int) -> dict | None:
    opp = B.load(db, opp_id)
    if not opp:
        return None
    m = B.load_mandate(db, opp["mandate_id"])
    d = {"opp": opp, "m": m, "prov": B.provenance(db, m["id"]), "locs": B.localities(db, m["id"]),
         "prefs": B.preferences(db, m["id"]), "share": _consent(db, opp["contact_id"], "share_teaser"),
         "service": _consent(db, opp["contact_id"], "service_contact")}
    d["fp"] = (f"{m['data_version']}|{m['funding_status']}|{m['criteria_reviewed_version']}|{int(d['share'])}"
               f"|{int(d['service'])}|{opp['status']}|{opp['do_not_contact']}")
    return d


def load_seller(db, opp_id: int) -> dict | None:
    opp = S.load(db, opp_id)
    if not opp:
        return None
    listing = S.load_listing(db, opp["listing_id"])
    prov = S.provenance(db, listing["id"])
    fin = S.financial_summary(db, listing["id"])
    d = {"opp": opp, "l": listing, "prov": prov, "cost": S.all_in_cost(listing, prov), "fin": fin,
         "share": _consent(db, opp["contact_id"], "share_teaser"),
         "service": _consent(db, opp["contact_id"], "service_contact")}
    d["fp"] = (f"{listing['data_version']}|{listing['approval_state']}|{fin['months_complete']}/{fin['months_evidenced']}"
               f"|{int(d['share'])}|{int(d['service'])}|{opp['status']}|{opp['do_not_contact']}")
    return d


def buyer_eligible(b: dict) -> bool:
    return b["opp"]["status"] == "active" and b["m"].get("need_type") in BFD.RESALE_NEEDS


def seller_eligible(s: dict) -> bool:
    return s["opp"]["status"] == "active" and s["l"].get("sale_purpose") not in ("seek_help", "unsure")


# -- Evaluation helpers --------------------------------------------------------------
def _lbl(fields, key, v):
    fd = fields.get(key)
    if v is None:
        return None
    if fd and fd.options:
        return dict(fd.options).get(v, str(v))
    if fd and fd.ftype == "inr":
        return format_inr(v)
    return str(v)


def _years_left(expiry: str | None) -> float | None:
    if not expiry:
        return None
    try:
        y, m, d = map(int, expiry.split("-"))
        return round((date(y, m, d) - now_utc().date()).days / 365.25, 1)
    except ValueError:
        return None


class _Eval:
    def __init__(self, b, s):
        self.b, self.s = b, s
        self.factors, self.gates, self.questions = [], [], []

    def add(self, key, result, buyer, seller, note="", ratio=None, hard=False):
        fam, share, label = CRITERIA[key]
        weight = WEIGHTS[fam] * share / sum(v[1] for v in CRITERIA.values() if v[0] == fam)
        earned = {"match": 1.0, "miss": 0.0, "partial": ratio if ratio is not None else 0.5}.get(result, 0.0) * weight
        self.factors.append({"key": key, "family": fam, "label": label, "result": result, "buyer": buyer,
                             "seller": seller, "note": note, "weight": round(weight, 2), "earned": round(earned, 2),
                             "hard": hard})

    def gate(self, severity, text):
        self.gates.append({"severity": severity, "text": text})

    def ask(self, side, text, fields=(), weight=1.0, blocking=False, criterion=None):
        self.questions.append({"side": side, "text": text, "fields": list(fields), "weight": weight,
                               "blocking": blocking, "criterion": criterion})


def _choice(ev: _Eval, dim: str, seller_field: str, label: str):
    b, s = ev.b, ev.s
    prefs = {c: imp for (d, c), imp in b["prefs"].items() if d == dim}
    stated = {c: imp for c, imp in prefs.items() if imp != "not_important"}
    if not stated:
        ev.add(dim, "na", "No preference", None)
        return
    musts = [c for c, imp in stated.items() if imp == "must"]
    wants = ", ".join(f"{BFD.option_label(dim, c)} ({BFD.IMPORTANCE_LABELS[imp].lower()})" for c, imp in stated.items())
    val = s["l"].get(seller_field)
    if val is None:
        ev.add(dim, "unknown", wants, None, f"{label} not recorded for this salon")
        ev.ask("seller", f"Confirm the salon's {label.lower()}", [seller_field], 1.0)
        return
    shown = _lbl(SF_BY_KEY, seller_field, val)
    imp = prefs.get(val)
    if musts and val not in musts:
        ev.add(dim, "miss", wants, shown, f"Buyer must have {', '.join(BFD.option_label(dim, c) for c in musts)}",
               hard=True)
        ev.gate("blocked", f"{label}: buyer must have {', '.join(BFD.option_label(dim, c) for c in musts)}; "
                           f"salon is {shown}")
    elif imp in ("must", "prefer"):
        ev.add(dim, "match", wants, shown)
    elif imp == "flexible":
        ev.add(dim, "partial", wants, shown, "Buyer is flexible on this", ratio=0.85)
    elif imp == "avoid":
        ev.add(dim, "miss", wants, shown, f"Buyer prefers to avoid {shown}")
    elif imp == "not_important":
        ev.add(dim, "match", wants, shown, "Not important to the buyer")
    else:  # buyer stated preferences for other options only
        ev.add(dim, "partial", wants, shown, f"{shown} not among the buyer's stated preferences", ratio=0.3)
        ev.ask("buyer", f"Ask whether a {shown.lower()} {label.lower()} would be acceptable", [], 0.5)


PARKING_OK = {"car": {"car_and_bike"}, "bike": {"car_and_bike", "bike_only"}}


def _parking(ev: _Eval):
    need = ev.b["m"].get("parking_need")
    imp = ev.b["prefs"].get(("parking_need", ""))
    have = ev.s["l"].get("parking")
    shown = _lbl(SF_BY_KEY, "parking", have) if have else None
    if need in (None, "not_needed") or imp == "not_important":
        ev.add("parking", "na", None, shown)
        return
    wanted = BFD.option_label("parking_need", need)
    if have is None:
        ev.add("parking", "unknown", wanted, None, "Parking not recorded for this salon")
        ev.ask("seller", "Ask about customer parking", ["parking"], 0.5)
    elif have in PARKING_OK[need]:
        ev.add("parking", "match", wanted, shown)
    elif imp == "must":
        ev.add("parking", "miss", wanted, shown, "Buyer must have this parking", hard=True)
        ev.gate("blocked", f"Parking: buyer needs '{wanted}'; salon has {shown.lower()}")
    else:
        ev.add("parking", "partial", wanted, shown, "Less parking than the buyer wants", ratio=0.3)


def _numeric_min(ev, key, buyer_key, seller_val, seller_label_val, unit_fmt, what, field_for_seller=None):
    """Buyer minimum (with importance) vs a seller figure. Returns nothing; records factor/gate/question."""
    b = ev.b
    need = b["m"].get(buyer_key)
    imp = b["prefs"].get((buyer_key, ""))
    if need is None or imp == "not_important":
        ev.add(key, "na", None, seller_label_val)
        return
    want = f"≥ {unit_fmt(need)}" + (f" ({BFD.IMPORTANCE_LABELS[imp].lower()})" if imp else "")
    if seller_val is None:
        ev.add(key, "unknown", want, None, f"{what} not recorded")
        if field_for_seller:
            ev.ask("seller", f"Collect the salon's {what.lower()}", [field_for_seller], 1.0, blocking=imp == "must")
        return
    if seller_val >= need:
        ev.add(key, "match", want, seller_label_val)
    elif imp == "must":
        ev.add(key, "miss", want, seller_label_val, f"Below the buyer's must-have minimum", hard=True)
        ev.gate("blocked", f"{what}: buyer must have ≥ {unit_fmt(need)}; salon has {seller_label_val}")
    else:
        ratio = max(0.0, min(1.0, seller_val / need)) * (0.8 if imp in (None, "prefer") else 0.9)
        ev.add(key, "partial", want, seller_label_val,
               f"Shortfall: {seller_label_val} vs {unit_fmt(need)} — buyer {('prefers' if imp != 'flexible' else 'is flexible on')} this",
               ratio=ratio)
        ev.ask("buyer", f"Check the buyer would accept {seller_label_val} ({what.lower()})", [], 0.6)


def evaluate(b: dict, s: dict) -> dict:
    ev = _Eval(b, s)
    m, l, bo, so = b["m"], s["l"], b["opp"], s["opp"]
    SF = SF_BY_KEY

    # Outreach permission and seller intent are hard gates, independent of any score (§8.3, §8.6).
    if bo.get("do_not_contact") or so.get("do_not_contact"):
        ev.gate("blocked", "A party has asked not to be contacted")
    if l.get("sale_purpose") is None:
        ev.gate("review", "Seller's sale intent not confirmed yet")
        ev.ask("seller", "Confirm what the owner wants (sell entire salon, part stake …)", ["sale_purpose"], 1.0, True)

    # A. Affordability — always all-in vs all-in (Appendix D) ---------------------------
    cost, ceiling = s["cost"], m.get("all_in_max")
    total = cost["known_total"]
    total_lbl = (format_inr(total) + ("" if cost["complete"] else " + unknown")) if total is not None else None
    if ceiling is None:
        ev.add("all_in", "unknown", None, total_lbl, "Buyer's all-in ceiling not recorded")
        ev.ask("buyer", "Get the buyer's maximum TOTAL cash (all-in ceiling)", ["all_in_max"], 3.0, True)
    elif total is None:
        ev.add("all_in", "unknown", f"≤ {format_inr(ceiling)}", None, "Salon's asking price not recorded")
        ev.ask("seller", "Collect the asking price", ["asking_price"], 3.0, True)
    else:
        gap = total - ceiling
        if gap > 0:
            flex_min = l.get("negotiable_min_price")
            flex_total = total - (l.get("asking_price") or 0) + flex_min if flex_min is not None else None
            if flex_total is not None and flex_total <= ceiling and cost["complete"]:
                ev.add("all_in", "partial", f"≤ {format_inr(ceiling)}", total_lbl,
                       f"{format_inr(gap)} over the ceiling — fits only within the seller's volunteered price "
                       "flexibility (needs negotiation)", ratio=0.6)
                ev.gate("review", f"Negotiation needed: {format_inr(gap)} over the buyer's all-in ceiling")
            elif l.get("negotiable") == "yes" and cost["complete"] and (gap <= NEAR_GAP_INR or gap <= ceiling * NEAR_GAP_PCT):
                ev.add("all_in", "partial", f"≤ {format_inr(ceiling)}", total_lbl,
                       f"{format_inr(gap)} over the ceiling; seller says the price is negotiable", ratio=0.5)
                ev.gate("review", f"Near match: {format_inr(gap)} over ceiling — needs a price concession or a higher "
                                  "buyer ceiling (do not assume either)")
                ev.ask("buyer", f"Ask if the buyer can stretch the all-in budget by {format_inr(gap)}", ["all_in_max"], 2.0)
            else:
                ev.add("all_in", "miss", f"≤ {format_inr(ceiling)}", total_lbl,
                       f"{'Minimum known total' if not cost['complete'] else 'All-in total'} is {format_inr(gap)} over "
                       "the ceiling", hard=True)
                ev.gate("blocked", f"Budget: salon needs {'at least ' if not cost['complete'] else ''}{format_inr(total)} "
                                   f"all-in; buyer ceiling is {format_inr(ceiling)}")
                ev.ask("buyer", f"Only if realistic: could the buyer go up to {format_inr(total)} all-in "
                                f"(currently {format_inr(ceiling)})?", ["all_in_max"], 2.0, criterion="all_in")
        elif not cost["complete"]:
            ev.add("all_in", "unknown", f"≤ {format_inr(ceiling)}", total_lbl,
                   "Known costs fit, but the all-in total is incomplete: " + ", ".join(cost["missing"]))
            ev.ask("seller", "Complete the all-in cost: " + ", ".join(cost["missing"]).lower(),
                   _cost_fields(cost["missing"]), 2.5, True)
        else:
            ev.add("all_in", "match", f"≤ {format_inr(ceiling)}", total_lbl,
                   f"{format_inr(-gap)} headroom" if gap < 0 else "Exactly at the ceiling")
            if m.get("includes_deposit") is None and (l.get("deposit_treatment") == "buyer_pays_separately"):
                ev.ask("buyer", "Verify the buyer's ceiling includes the rent deposit", ["includes_deposit"], 1.0)
    bmax, ask_p = m.get("budget_max"), l.get("asking_price")
    if bmax is None or ask_p is None:
        ev.add("price_band", "na" if bmax is None else "unknown", _lbl(BFD.BY_KEY, "budget_max", bmax),
               _lbl(SF, "asking_price", ask_p))
    else:
        ev.add("price_band", "match" if ask_p <= bmax else "partial", f"≤ {format_inr(bmax)}", format_inr(ask_p),
               "" if ask_p <= bmax else "Asking price above the buyer's price band", ratio=max(0, 1 - (ask_p - bmax) / bmax))
    cr, st = m.get("cash_ready"), l.get("sale_timeline")
    if cr is None or st is None:
        ev.add("cash_timing", "na", _lbl(BFD.BY_KEY, "cash_ready", cr), _lbl(SF, "sale_timeline", st))
    else:
        bad = cr == "planned" and st == "immediate"
        ev.add("cash_timing", "partial" if bad else "match", _lbl(BFD.BY_KEY, "cash_ready", cr),
               _lbl(SF, "sale_timeline", st), "Seller wants to exit now; buyer's money is not arranged" if bad else "",
               ratio=0.3)

    # B. Location --------------------------------------------------------------------------
    from .common import known_place
    bcity = (known_place(m.get("preferred_city")) or "").strip().lower()
    scity = (known_place(so.get("city")) or "").strip().lower()
    loc = (known_place(so.get("locality")) or "").strip()
    if not bcity:
        ev.add("city", "unknown", None, known_place(so.get("city")))
        ev.ask("buyer", "Confirm the buyer's preferred city", ["preferred_city"], 1.5)
    elif not scity:
        ev.add("city", "unknown", m["preferred_city"], None, "Salon's city not recorded yet")
        ev.ask("seller", "Confirm the salon's city and area", [], 1.5)
    elif bcity == scity:
        ev.add("city", "match", m["preferred_city"], so.get("city"))
    elif m.get("relocate_willing") == "yes":
        ev.add("city", "partial", m["preferred_city"], so.get("city"), "Different city — buyer is open to relocating",
               ratio=0.4)
    else:
        ev.add("city", "miss", m["preferred_city"], so.get("city"), "Different city", hard=True)
        ev.gate("blocked", f"City: buyer wants {m['preferred_city']}; salon is in {so.get('city')}")
    pref = {r["locality"].strip().lower(): r for r in b["locs"]["preferred"]}
    excl = {r["locality"].strip().lower() for r in b["locs"]["excluded"]}
    ranked = " > ".join(r["locality"] for r in b["locs"]["preferred"]) or None
    if loc.lower() in excl:
        ev.add("locality", "miss", ranked, loc, f"{loc} is on the buyer's excluded list", hard=True)
        ev.gate("blocked", f"Location: buyer excluded {loc}")
    elif loc.lower() in pref:
        rank = pref[loc.lower()]["rank_order"]
        ev.add("locality", "match" if rank == 1 else "partial", ranked, loc,
               f"Buyer's #{rank} preferred area", ratio=max(0.6, 1 - 0.1 * (rank - 1)))
    elif not pref:
        ev.add("locality", "unknown", None, loc, "Buyer has not ranked any areas")
        ev.ask("buyer", "Ask the buyer to rank their preferred areas", [], 1.0)
    elif bcity and bcity == scity:
        ev.add("locality", "partial", ranked, loc, f"{loc} is not in the buyer's list — may be acceptable nearby",
               ratio=0.4)
        ev.ask("buyer", f"Ask if the buyer would consider {loc}", [], 0.8)
    else:
        ev.add("locality", "miss", ranked, loc, "Not in the buyer's areas")

    # C. Format & positioning / D. structure -----------------------------------------------
    _choice(ev, "gender_format", "gender_format", "Format")
    _choice(ev, "positioning", "positioning", "Positioning")
    _choice(ev, "business_model", "business_model", "Model")
    _choice(ev, "sale_structure", "sale_structure", "Deal structure")
    _choice(ev, "area_type", "area_type", "Type of area")
    _choice(ev, "floor_level", "floor_level", "Floor")
    ta = l.get("transfer_authority")
    if ta == "yes":
        ev.add("authority", "match", "Required", "Confirmed")
    elif ta == "no":
        ev.add("authority", "miss", "Required", "No authority", "Respondent cannot transfer the salon", hard=True)
        ev.gate("blocked", "Authority: the seller contact does not have authority to transfer the salon")
    else:
        ev.add("authority", "unknown", "Required", None, "Transfer authority not confirmed")
        ev.ask("seller", "Confirm the seller has authority to transfer", ["transfer_authority"], 1.5)

    # E. Economics — never an ROI/payback claim; uncertainty is explicit (§8.7 ex. 5) -------
    fin = s["fin"]
    uncertain = bool(fin["rows"]) and (fin["months_complete"] < 3 or any("Costs missing" in w for w in fin["warnings"]))
    sales = l.get("avg_monthly_sales")
    _numeric_min(ev, "sales", "min_monthly_sales", sales, _lbl(SF, "avg_monthly_sales", sales), format_inr,
                 "Monthly sales", "avg_monthly_sales")
    if uncertain and ev.factors[-1]["result"] in ("match", "partial"):
        ev.factors[-1]["note"] = (ev.factors[-1]["note"] + "; " if ev.factors[-1]["note"] else "") + \
            "economics uncertain — costs missing for some months"
    surplus = fin["avg_reported_surplus"]
    if surplus is None and sales is not None and l.get("avg_monthly_expenses") is not None:
        surplus = sales - l["avg_monthly_expenses"]
    if m.get("turnaround_ok") is None:
        ev.add("profitability", "na", None, _lbl(SF, "avg_monthly_sales", surplus))
    elif surplus is None:
        ev.add("profitability", "unknown", "Profitable salon" if m["turnaround_ok"] == "no" else "Turnaround OK",
               None, "Monthly surplus not known" + (" (costs missing)" if uncertain else ""))
        ev.ask("seller", "Collect monthly operating costs", ["avg_monthly_expenses"], 1.0)
    elif surplus <= 0 and m["turnaround_ok"] == "no":
        ev.add("profitability", "miss", "Profitable salon", f"Declared surplus {format_inr(surplus)}",
               "Buyer does not want a loss-making salon")
    else:
        ev.add("profitability", "match", "Profitable salon" if m["turnaround_ok"] == "no" else "Turnaround OK",
               f"Declared surplus {format_inr(surplus)}", "Seller-declared; not an earnings forecast")

    # F. Property & lease ----------------------------------------------------------------
    chairs = l.get("chairs")
    _numeric_min(ev, "chairs", "chairs_min", chairs, str(chairs) if chairs is not None else None, str,
                 "Chairs", "chairs")
    if chairs is not None and m.get("chairs_max") is not None and chairs > m["chairs_max"]:
        ev.factors[-1].update(result="partial", note=f"Larger than the buyer's maximum of {m['chairs_max']}",
                              earned=round(ev.factors[-1]["weight"] * 0.6, 2))
    # rent is compared as rent + maintenance (CAM) — what the buyer actually pays every month
    base_rent, maint = l.get("monthly_rent"), l.get("maintenance_monthly")
    rent = base_rent + maint if base_rent is not None and maint is not None else base_rent
    rmax = m.get("max_monthly_rent")
    rimp = b["prefs"].get(("max_monthly_rent", ""))
    if rmax is not None and rimp != "not_important" and base_rent is not None and maint is None:
        ev.ask("seller", "Ask the monthly maintenance (CAM) charges", ["maintenance_monthly"], 0.5)
    if rmax is None or rimp == "not_important":
        ev.add("rent", "na", None, _lbl(SF, "monthly_rent", rent))
    elif rent is None:
        ev.add("rent", "unknown", f"≤ {format_inr(rmax)}", None)
        ev.ask("seller", "Collect the monthly rent", ["monthly_rent"], 1.0)
    elif rent <= rmax:
        ev.add("rent", "match", f"≤ {format_inr(rmax)}", format_inr(rent))
    elif rimp == "must":
        ev.add("rent", "miss", f"≤ {format_inr(rmax)}", format_inr(rent), "Rent above the buyer's hard maximum",
               hard=True)
        ev.gate("blocked", f"Rent: {format_inr(rent)}/month vs buyer maximum {format_inr(rmax)}")
    else:
        ev.add("rent", "partial", f"≤ {format_inr(rmax)}", format_inr(rent), "Rent above the buyer's preference",
               ratio=max(0, 1 - (rent - rmax) / rmax))
    _parking(ev)
    years = _years_left(l.get("lease_expiry"))
    lts, renew = l.get("lease_transfer_status"), l.get("lease_renewal")
    need_years = m.get("min_lease_years_left")
    lease_label = (f"{years} yrs left" if years is not None else "Expiry unknown") + \
        (f", {_lbl(SF, 'lease_transfer_status', lts)}" if lts else "")
    if lts == "not_permitted":
        ev.add("lease", "miss", "Transferable lease", lease_label, "Landlord does not permit transfer", hard=True)
        ev.gate("blocked", "Lease: transfer not permitted by the landlord")
    elif need_years is not None and years is not None and years < need_years and renew == "no":
        ev.add("lease", "miss", f"≥ {need_years} yrs", lease_label, "Lease too short and no renewal", hard=True)
        ev.gate("blocked", f"Lease: {years} years left, no renewal; buyer needs ≥ {need_years}")
    elif years is None:
        ev.add("lease", "unknown", f"≥ {need_years} yrs" if need_years else "Transferable lease", lease_label)
        ev.ask("seller", "Collect the lease expiry date", ["lease_expiry"], 1.2)
    elif need_years is not None and years < need_years:
        ev.add("lease", "partial", f"≥ {need_years} yrs", lease_label,
               "Short lease; renewal " + ("possible" if renew in ("yes", "negotiable") else "not confirmed"), ratio=0.4)
        ev.ask("seller", "Confirm lease renewal terms with the landlord", ["lease_renewal"], 1.0)
    elif lts in (None, "unchecked", "landlord_pending"):
        ev.add("lease", "partial", f"≥ {need_years} yrs" if need_years else "Transferable lease", lease_label,
               "Transfer not yet confirmed with the landlord", ratio=0.7)
        ev.ask("seller", "Confirm the landlord will allow a lease transfer", ["lease_transfer_status"], 1.2)
    else:
        ev.add("lease", "match", f"≥ {need_years} yrs" if need_years else "Transferable lease", lease_label)
    area = l.get("carpet_area_sqft")
    _numeric_min(ev, "area", "area_min_sqft", area, f"{area} sq ft" if area is not None else None,
                 lambda v: f"{v} sq ft", "Carpet area", "carpet_area_sqft")

    # G. Staff & handover ------------------------------------------------------------------
    need, have = m.get("staff_continuity_need"), l.get("staff_continuity")
    simp = b["prefs"].get(("staff_continuity_need", ""))
    if need in (None, "no") or simp == "not_important":
        ev.add("staff", "na", _lbl(BFD.BY_KEY, "staff_continuity_need", need), _lbl(SF, "staff_continuity", have))
    elif have is None:
        ev.add("staff", "unknown", _lbl(BFD.BY_KEY, "staff_continuity_need", need), None)
        ev.ask("seller", "Ask whether staff are expected to stay after the sale", ["staff_continuity"], 0.8)
    else:
        rank = {"yes": 2, "partial": 1, "no": 0}
        ok = rank[have] >= rank[need]
        if ok:
            ev.add("staff", "match", _lbl(BFD.BY_KEY, "staff_continuity_need", need), _lbl(SF, "staff_continuity", have))
        elif simp == "must" and have == "no":
            ev.add("staff", "miss", _lbl(BFD.BY_KEY, "staff_continuity_need", need), _lbl(SF, "staff_continuity", have),
                   "Staff not expected to stay; buyer requires it", hard=True)
            ev.gate("blocked", "Staff: buyer needs staff to stay; seller says they will not")
        else:
            ev.add("staff", "partial", _lbl(BFD.BY_KEY, "staff_continuity_need", need),
                   _lbl(SF, "staff_continuity", have), "Only partial staff continuity", ratio=0.5)
    tn, ts = m.get("transition_support_need"), l.get("transition_support")
    if tn in (None, "no") or b["prefs"].get(("transition_support_need", "")) == "not_important":
        ev.add("transition", "na", _lbl(BFD.BY_KEY, "transition_support_need", tn), _lbl(SF, "transition_support", ts))
    elif ts is None:
        ev.add("transition", "unknown", _lbl(BFD.BY_KEY, "transition_support_need", tn), None)
        ev.ask("seller", "Ask if the owner will support the handover", ["transition_support"], 0.6)
    else:
        ev.add("transition", {"yes": "match", "negotiable": "partial", "no": "miss" if tn == "yes" else "partial"}[ts],
               _lbl(BFD.BY_KEY, "transition_support_need", tn), _lbl(SF, "transition_support", ts), ratio=0.6)

    # H. Timing ------------------------------------------------------------------------------
    bw = {"30d": 1, "90d": 3, "180d": 6, "later": 12}.get(m.get("purchase_window"))
    sw = {"immediate": 0, "within_3m": 3, "3_6m": 6, "6_12m": 12, "no_rush": 18}.get(l.get("sale_timeline"))
    bwl, swl = _lbl(BFD.BY_KEY, "purchase_window", m.get("purchase_window")), _lbl(SF, "sale_timeline", l.get("sale_timeline"))
    if bw is None or sw is None:
        ev.add("timing", "unknown", bwl, swl)
        if bw is None:
            ev.ask("buyer", "Ask when the buyer wants to complete a purchase", ["purchase_window"], 0.8)
        else:
            ev.ask("seller", "Ask the seller's sale timeline", ["sale_timeline"], 0.8)
    else:
        diff = abs(bw - sw)
        ev.add("timing", "match" if diff <= 3 else "partial" if diff <= 9 else "miss", bwl, swl,
               "" if diff <= 3 else "Timelines differ", ratio=0.5)

    # I. Asset quality & track record / J. Terms ---------------------------------------------
    oy = l.get("operating_years")
    _numeric_min(ev, "track_record", "min_operating_years", oy, f"{oy} yrs" if oy is not None else None,
                 lambda v: f"{v} yrs", "Years in operation", "operating_years")
    eq = l.get("equipment_condition")
    if eq is None:
        ev.add("equipment", "na", None, None)
    else:
        ev.add("equipment", "match" if eq in ("excellent", "good") else "partial", None, _lbl(SF, "equipment_condition", eq),
               "" if eq in ("excellent", "good") else "Refit likely — check the buyer's renovation budget",
               ratio=0.6 if eq == "fair" else 0.3)
    ng = l.get("negotiable")
    ev.add("negotiable", {"yes": "match", "no": "partial"}.get(ng, "unknown"), None, _lbl(SF, "negotiable", ng),
           "Fixed price" if ng == "no" else "", ratio=0.5)
    tol = m.get("liability_tolerance")
    loans, lit = l.get("outstanding_loans"), l.get("litigation")
    if tol is None:
        ev.add("liabilities", "na", None, None)
    elif loans is None and lit is None:
        ev.add("liabilities", "unknown", _lbl(BFD.BY_KEY, "liability_tolerance", tol), None)
        ev.ask("seller", "Ask about outstanding loans and disputes", ["outstanding_loans", "litigation"], 0.6)
    else:
        burden = (loans or 0) > 0 or lit == "yes"
        res = "miss" if burden and tol == "none" else "partial" if burden else "match"
        ev.add("liabilities", res, _lbl(BFD.BY_KEY, "liability_tolerance", tol),
               ("Litigation pending; " if lit == "yes" else "") + (f"loans {format_inr(loans)}" if loans else "no loans"),
               "Liabilities exceed the buyer's tolerance" if res == "miss" else "", ratio=0.5)
    return _summarise(ev, b, s, uncertain)


def _cost_fields(missing: list[str]) -> list[str]:
    m = {"Asking price": "asking_price", "Rent deposit amount": "rent_deposit", "Deposit treatment": "deposit_treatment",
         "Separately payable stock": "stock_value_separate", "Franchise transfer fee": "franchise_transfer_fee",
         "Independent / franchise": "business_model"}
    return [m[x] for x in missing if x in m]


def _summarise(ev: _Eval, b, s, uncertain) -> dict:
    applicable = [f for f in ev.factors if f["result"] != "na"]
    known = [f for f in applicable if f["result"] != "unknown"]
    w_app = sum(f["weight"] for f in applicable)
    w_known = sum(f["weight"] for f in known)
    fit = round(100 * sum(f["earned"] for f in known) / w_known) if w_known else None
    coverage = round(100 * w_known / w_app) if w_app else 0
    gate = "blocked" if any(g["severity"] == "blocked" for g in ev.gates) else \
        "review" if ev.gates else "pass"
    m, l = b["m"], s["l"]
    verified = (B.criteria_review_state(m) == "current" and l["approval_state"] == "approved"
                and m["funding_status"] in ("staff_reviewed", "verified"))
    confidence = "low" if coverage < 50 or (uncertain and coverage < 75) else \
        "high" if coverage >= 80 and verified and not uncertain else "medium"
    # a "near" match has a real shortfall (earned < 75% of weight), not merely a criterion the buyer is flexible on
    partial_flex = any(f["result"] == "partial" and f["earned"] < 0.75 * f["weight"] for f in known)
    if gate == "blocked":
        outcome = "blocked"
    elif coverage < 50:
        outcome = "insufficient"
    elif gate == "review" or partial_flex or (fit or 0) < 60:
        outcome = "near" if (fit or 0) >= 40 else "insufficient"
    else:
        outcome = "ready"
    key_unknown = any(f["result"] == "unknown" and f["key"] in ("all_in", "city") for f in applicable)
    if gate == "blocked":
        readiness = "blocked"
    elif coverage < 60 or key_unknown or any(q["blocking"] for q in ev.questions):
        readiness = "info_required"
    elif not verified:
        readiness = "awaiting_verification"
    elif not (b["share"] and s["share"] and b["service"] and s["service"]):
        readiness = "awaiting_consent"
    else:
        readiness = "ready_to_approach"
    ev.questions.sort(key=lambda q: (not q["blocking"], -q["weight"]))
    if gate == "blocked":
        # Only questions that could resolve the blocker are useful; a true conflict has none (§8.4).
        hard_keys = {f["key"] for f in applicable if f["hard"]}
        ev.questions = [q for q in ev.questions if q.get("criterion") in hard_keys]
    red = sum(1 for f in applicable if f["result"] == "miss") + len([g for g in ev.gates if g["severity"] == "blocked"])
    if ev.gates:
        headline = ev.gates[0]["text"]
    else:
        partials = sorted((f for f in known if f["result"] == "partial" and f["earned"] < 0.75 * f["weight"]),
                          key=lambda f: f["earned"] - f["weight"])
        headline = (f"Near: {partials[0]['label']} — {partials[0]['note'] or 'partial fit'}" if partials else
                    "Strong fit on everything known" if coverage >= 80 else "Good fit so far — some facts missing")
    rank = (fit or 0) * (0.4 + 0.6 * coverage / 100) - (1000 if gate == "blocked" else 0)
    verification = []
    if B.criteria_review_state(m) != "current":
        verification.append("buyer's criteria not confirmed by a reviewer")
    if m["funding_status"] not in ("staff_reviewed", "verified"):
        verification.append("buyer's funding not reviewed")
    if l["approval_state"] != "approved":
        verification.append("salon listing not approved")
    consents = []
    for who, d in (("buyer", b), ("seller", s)):
        if not d["service"]:
            consents.append(f"{who} contact consent")
        if not d["share"]:
            consents.append(f"{who} consent to share details")
    return {"gate": gate, "gates": ev.gates, "fit": fit, "coverage": coverage, "confidence": confidence,
            "outcome": outcome, "readiness": readiness, "factors": ev.factors, "questions": ev.questions,
            "headline": headline, "rank": round(rank, 2), "red_flags": red, "uncertain_economics": uncertain,
            "all_in_total": s["cost"]["known_total"], "all_in_complete": s["cost"]["complete"],
            "buyer_ceiling": m.get("all_in_max"), "verification_missing": verification, "consent_missing": consents,
            "rules_version": RULES_VERSION}


# -- Persistence & refresh (§8.6) ----------------------------------------------------------
def _digest(r: dict) -> tuple:
    return (r["gate"], r["fit"], r["coverage"], r["outcome"], r["readiness"], r["headline"], r["confidence"])


def _sides(questions) -> str | None:
    sides = {q["side"] for q in questions}
    return "both" if len(sides) == 2 else (sides.pop() if sides else None)


def _persist(db, b, s, r, existing, trigger, actor_id) -> int:
    now = now_iso()
    nq = r["questions"][0]["text"] if r["questions"] else None
    row = {"gate": r["gate"], "outcome": r["outcome"], "fit_score": r["fit"], "coverage_pct": r["coverage"],
           "confidence": r["confidence"], "readiness": r["readiness"], "rank_score": r["rank"],
           "all_in_total": r["all_in_total"], "all_in_complete": int(r["all_in_complete"]),
           "buyer_ceiling": r["buyer_ceiling"], "headline": r["headline"][:300], "next_question": nq,
           "red_flags": r["red_flags"], "info_needed_from": _sides(r["questions"]), "rules_version": RULES_VERSION, "buyer_fp": b["fp"], "seller_fp": s["fp"],
           "stale": 0, "computed_at": now, "updated_at": now}
    snap = {"rules_version": RULES_VERSION, "buyer_version": b["m"]["data_version"],
            "seller_version": s["l"]["data_version"], "result": json.dumps(r, default=str), "computed_by": actor_id,
            "computed_at": now}
    if existing is None:
        cid = db.insert("match_candidates", {**row, "buyer_opp_id": b["opp"]["id"], "seller_opp_id": s["opp"]["id"],
                                             "created_at": now})
        db.insert("match_snapshots", {**snap, "candidate_id": cid, "trigger": "created"})
        db.insert("match_events", {"candidate_id": cid, "event": "suggested", "to_stage": "suggested",
                                   "body": f"Suggested by {RULES_VERSION.split(' (')[0]}", "actor_id": actor_id,
                                   "at": now})
        return cid
    cid = existing["id"]
    changed = _digest(r) != (existing["gate"], existing["fit_score"], existing["coverage_pct"], existing["outcome"],
                             existing["readiness"], existing["headline"], existing["confidence"])
    db.update("match_candidates", cid, row)
    if changed or trigger == "manual":
        db.insert("match_snapshots", {**snap, "candidate_id": cid, "trigger": trigger})
    return cid


def _maybe_resurface(db, cand, b, s, actor_id) -> None:
    """UAT-49: a rejected candidate comes back only when either side's facts change, or on its revisit date."""
    if not cand["suppressed"]:
        return
    facts_changed = (b["m"]["data_version"] != cand["suppressed_buyer_version"]
                     or s["l"]["data_version"] != cand["suppressed_seller_version"])
    due = cand["revisit_on"] and cand["revisit_on"] <= now_iso()
    if facts_changed or due:
        now = now_iso()
        db.update("match_candidates", cand["id"], {"suppressed": 0, "stage": "suggested", "revisit_on": None,
                                                   "updated_at": now})
        db.insert("match_events", {"candidate_id": cand["id"], "event": "resurfaced", "from_stage": cand["stage"],
                                   "to_stage": "suggested", "actor_id": actor_id, "at": now,
                                   "reason": "revisit date reached" if due and not facts_changed else "facts changed",
                                   "body": f"Back for review: {'revisit date reached' if due and not facts_changed else 'buyer or salon details changed since it was marked ' + STAGES[cand['stage']].lower()}"})


def _notify_strong(db, cid, b, s, r) -> None:
    cand = db.one("SELECT notified_at FROM match_candidates WHERE id = ?", (cid,))
    if cand["notified_at"] or r["outcome"] not in ("ready", "near") or (r["fit"] or 0) < 70 or r["coverage"] < 60:
        return
    title = (f"New {'strong' if r['outcome'] == 'ready' else 'near'} match: {b['opp']['ref_code']} "
             f"{b['opp']['contact_name']} ↔ {s['opp']['ref_code']} {s['opp']['salon_name']} (fit {r['fit']})")
    for uid in {b["opp"]["primary_owner_id"], s["opp"]["primary_owner_id"]} - {None}:
        T.notify(db, uid, "match", title, f"/matches/{cid}")
    db.update("match_candidates", cid, {"notified_at": now_iso()})


def refresh(db, buyer_id: int | None = None, seller_id: int | None = None, force: bool = False,
            actor_id: int | None = None) -> dict:
    """Incremental re-evaluation. Only pairs whose input fingerprints changed are recomputed (unless force)."""
    load_weights(db)
    if buyer_id:
        buyers = [x for x in [load_buyer(db, buyer_id)] if x]
    else:
        buyers = [load_buyer(db, r["id"]) for r in db.all("SELECT id FROM opportunities WHERE kind = 'buyer' "
                                                           "AND status = 'active'")]
    if seller_id:
        sellers = [x for x in [load_seller(db, seller_id)] if x]
    else:
        sellers = [load_seller(db, r["id"]) for r in db.all("SELECT id FROM opportunities WHERE kind = 'seller' "
                                                             "AND status = 'active'")]
    buyers = [b for b in buyers if buyer_eligible(b)]
    sellers = [s for s in sellers if seller_eligible(s)]
    existing = {}
    if buyers and sellers:
        for c in db.all("SELECT * FROM match_candidates WHERE buyer_opp_id IN (%s) AND seller_opp_id IN (%s)" % (
                ",".join("?" * len(buyers)), ",".join("?" * len(sellers))),
                [b["opp"]["id"] for b in buyers] + [s["opp"]["id"] for s in sellers]):
            existing[(c["buyer_opp_id"], c["seller_opp_id"])] = c
    stats = {"evaluated": 0, "new": 0, "unchanged": 0}
    with db:
        for b in buyers:
            for s in sellers:
                cand = existing.get((b["opp"]["id"], s["opp"]["id"]))
                due = cand and cand["suppressed"] and cand["revisit_on"] and cand["revisit_on"] <= now_iso()
                if cand and not force and not due and cand["buyer_fp"] == b["fp"] and cand["seller_fp"] == s["fp"] \
                        and cand["rules_version"] == RULES_VERSION:
                    stats["unchanged"] += 1
                    continue
                r = evaluate(b, s)
                if cand is None and r["outcome"] == "insufficient" and (r["coverage"] or 0) < MIN_COVERAGE_TO_LIST:
                    # too little known on either side to be worth a row on the Match Desk yet; the records' own
                    # "Still to collect" cards drive the missing questions, and the pair appears once data arrives
                    stats["held_back"] = stats.get("held_back", 0) + 1
                    continue
                if cand:
                    _maybe_resurface(db, cand, b, s, actor_id)
                cid = _persist(db, b, s, r, cand, "manual" if force else "data_change" if cand else "created", actor_id)
                _notify_strong(db, cid, b, s, r)
                stats["evaluated"] += 1
                stats["new"] += 0 if cand else 1
    return stats


_LAST_STALE_CHECK = {"at": 0.0, "n": 0}


def mark_stale_throttled(db, every_seconds: int = 60) -> int:
    """Pages call this; the full check runs at most once a minute per process (jobs run it too)."""
    import time as _t
    if _t.time() - _LAST_STALE_CHECK["at"] < every_seconds:
        return _LAST_STALE_CHECK["n"]
    n = mark_stale(db)
    _LAST_STALE_CHECK.update(at=_t.time(), n=n)
    return n


def queue_counts(db, extra_where: str = "", params: list | None = None) -> dict:
    """All queue counts in ONE pass over the candidates."""
    sums = ", ".join(f"SUM(CASE WHEN ({cond}) THEN 1 ELSE 0 END) AS q_{k}" for k, (_l, cond) in QUEUES.items())
    row = db.one("SELECT " + sums + " FROM match_candidates mc JOIN opportunities bo ON bo.id = mc.buyer_opp_id "
                 "JOIN opportunities so ON so.id = mc.seller_opp_id WHERE " + ACTIVE_WHERE + extra_where, params or [])
    return {k: (row[f"q_{k}"] or 0) if row else 0 for k in QUEUES}


def attach_open_tasks(db, rows: list[dict]) -> list[dict]:
    """Open sub-task counts for just the rows shown (instead of a sub-query per candidate)."""
    ids = [r["id"] for r in rows]
    counts = {}
    if ids:
        for r in db.all("SELECT p.entity_id AS cid, COUNT(*) AS n FROM tasks t JOIN tasks p ON p.id = t.parent_task_id "
                        "WHERE t.status = 'open' AND p.entity_type = 'match' AND p.entity_id IN (%s) GROUP BY p.entity_id"
                        % ",".join("?" * len(ids)), ids):
            counts[r["cid"]] = r["n"]
    for r in rows:
        r["open_tasks"] = counts.get(r["id"], 0)
    return rows


def mark_stale(db, only: tuple | None = None) -> int:
    """Flag candidates whose inputs changed since evaluation (UAT-14). Cheap: fingerprints only.
    only=(buyer_opp_id, seller_opp_id) checks just that pair (the match page)."""
    n = 0
    fps_b, fps_s = {}, {}
    extra, args = ("AND mc.buyer_opp_id = ? AND mc.seller_opp_id = ? ", list(only)) if only else ("", [])
    for c in db.all("SELECT mc.id, mc.buyer_opp_id, mc.seller_opp_id, mc.buyer_fp, mc.seller_fp, mc.stale "
                    "FROM match_candidates mc JOIN opportunities bo ON bo.id = mc.buyer_opp_id "
                    "JOIN opportunities so ON so.id = mc.seller_opp_id WHERE " + ACTIVE_WHERE + extra, args):
        if c["buyer_opp_id"] not in fps_b:
            b = load_buyer(db, c["buyer_opp_id"])
            fps_b[c["buyer_opp_id"]] = b["fp"] if b else None
        if c["seller_opp_id"] not in fps_s:
            s = load_seller(db, c["seller_opp_id"])
            fps_s[c["seller_opp_id"]] = s["fp"] if s else None
        stale = int(fps_b[c["buyer_opp_id"]] != c["buyer_fp"] or fps_s[c["seller_opp_id"]] != c["seller_fp"])
        if stale != c["stale"]:
            db.update("match_candidates", c["id"], {"stale": stale})
        n += stale
    return n


# -- Reading -----------------------------------------------------------------------------
CAND_SELECT = (
    "SELECT mc.*, bo.ref_code AS buyer_ref, bc.full_name AS buyer_name, bo.primary_owner_id AS buyer_owner_id, "
    "bu.full_name AS buyer_owner, bo.last_contact_at AS buyer_last_contact, bo.status AS buyer_status, "
    "so.ref_code AS seller_ref, sb.name AS salon_name, sb.locality AS salon_locality, sb.city AS salon_city, "
    "so.primary_owner_id AS seller_owner_id, su.full_name AS seller_owner, so.last_contact_at AS seller_last_contact, "
    "so.status AS seller_status, mu.full_name AS match_owner, ip.screen_verdict AS investor_verdict, "
    "ip.form_status AS investor_form, "
    "(SELECT COUNT(*) FROM tasks t WHERE t.status = 'open' AND t.parent_task_id IN "
    " (SELECT id FROM tasks p WHERE p.entity_type = 'match' AND p.entity_id = mc.id)) AS open_tasks "
    "FROM match_candidates mc JOIN opportunities bo ON bo.id = mc.buyer_opp_id JOIN contacts bc ON bc.id = bo.contact_id "
    "JOIN opportunities so ON so.id = mc.seller_opp_id LEFT JOIN salon_businesses sb ON sb.id = so.business_id "
    "LEFT JOIN users bu ON bu.id = bo.primary_owner_id LEFT JOIN users su ON su.id = so.primary_owner_id "
    "LEFT JOIN users mu ON mu.id = mc.match_owner_id LEFT JOIN investor_profiles ip ON ip.opportunity_id = mc.buyer_opp_id ")
ACTIVE_WHERE = "bo.status = 'active' AND so.status = 'active' "

QUEUES = {  # key: (label, SQL condition)
    "all": ("All active", "mc.suppressed = 0"),
    "new": ("New candidates", "mc.suppressed = 0 AND mc.stage = 'suggested' AND mc.outcome IN ('ready','near')"),
    "info": ("Need information", "mc.suppressed = 0 AND mc.gate <> 'blocked' AND mc.info_needed_from IS NOT NULL"),
    "ready": ("Ready to approach", "mc.suppressed = 0 AND mc.readiness = 'ready_to_approach'"),
    "near": ("Near matches worth negotiating", "mc.suppressed = 0 AND mc.outcome = 'near' AND mc.fit_score >= 50"),
    "buyer_info": ("Needs buyer data", "mc.suppressed = 0 AND mc.gate <> 'blocked' AND mc.info_needed_from IN ('buyer','both')"),
    "seller_info": ("Needs seller data", "mc.suppressed = 0 AND mc.gate <> 'blocked' AND mc.info_needed_from IN ('seller','both')"),
    "verify": ("Awaiting verification", "mc.suppressed = 0 AND mc.readiness = 'awaiting_verification'"),
    "consent": ("Awaiting consent", "mc.suppressed = 0 AND mc.readiness = 'awaiting_consent'"),
    "shortlisted": ("Shortlisted", "mc.suppressed = 0 AND mc.stage = 'shortlisted'"),
    "approved": ("Approved / in conversation", "mc.suppressed = 0 AND mc.stage IN ('approved_to_approach','interest_confirmed')"),
    "stale": ("Stale — recompute", "mc.stale = 1"),
    "blocked": ("Blocked", "mc.suppressed = 0 AND mc.gate = 'blocked'"),
    "suppressed": ("Not suitable / declined", "mc.suppressed = 1"),
}


def my_tiles(db, user_id: int) -> list[dict]:
    """My Day matching tiles (PRD §11.3): counts for matches where I own the buyer, the salon or the match."""
    mine = "(bo.primary_owner_id = ? OR so.primary_owner_id = ? OR mc.match_owner_id = ?)"
    out = []
    for key in ("new", "info", "near", "consent", "ready"):
        label, cond = QUEUES[key]
        n = db.scalar("SELECT COUNT(*) FROM match_candidates mc JOIN opportunities bo ON bo.id = mc.buyer_opp_id "
                      "JOIN opportunities so ON so.id = mc.seller_opp_id WHERE " + ACTIVE_WHERE +
                      f" AND ({cond}) AND {mine}", (user_id, user_id, user_id))
        out.append({"key": key, "label": label, "count": n})
    return out


def latest_result(db, cid: int) -> dict:
    row = db.one("SELECT result FROM match_snapshots WHERE candidate_id = ? ORDER BY id DESC LIMIT 1", (cid,))
    return json.loads(row["result"]) if row else {}


def readiness_label(c: dict) -> str:
    if c["suppressed"]:
        return READINESS["closed"]
    if c["stage"] == "interest_confirmed":
        return READINESS["in_conversation"]
    return READINESS.get(c["readiness"], c["readiness"])


def unblock_suggestions(db, opp: dict, limit: int = 3) -> list[dict]:
    """'What unblocks the most deals' for one record: questions on THIS side, ranked by how many matches they hold up."""
    side = "buyer" if opp["kind"] == "buyer" else "seller"
    col = "buyer_opp_id" if side == "buyer" else "seller_opp_id"
    agg: dict[str, dict] = {}
    # one query for every candidate's latest result (was one query per candidate)
    for c in db.all("SELECT s.candidate_id AS id, s.result FROM match_snapshots s WHERE s.id IN ("
                    "SELECT MAX(s2.id) FROM match_snapshots s2 JOIN match_candidates mc ON mc.id = s2.candidate_id "
                    f"WHERE mc.{col} = ? AND mc.suppressed = 0 AND mc.gate <> 'blocked' GROUP BY s2.candidate_id) "
                    "ORDER BY s.candidate_id", (opp["id"],)):
        for q in json.loads(c["result"] or "{}").get("questions", []):
            if q["side"] != side:
                continue
            a = agg.setdefault(q["text"], {**q, "count": 0, "candidates": []})
            a["count"] += 1
            a["candidates"].append(c["id"])
    return sorted(agg.values(), key=lambda a: (-a["count"], not a["blocking"], -a["weight"]))[:limit]


# -- Actions --------------------------------------------------------------------------------
def _cand(db, cid):
    c = db.one(CAND_SELECT + "WHERE mc.id = ?", (cid,))
    if not c:
        raise ValidationError("Match not found.")
    return c


def _event(db, cid, event, actor_id, from_stage=None, to_stage=None, reason=None, body=None):
    db.insert("match_events", {"candidate_id": cid, "event": event, "from_stage": from_stage, "to_stage": to_stage,
                               "reason": reason, "body": body, "actor_id": actor_id, "at": now_iso()})


def _log_both(db, actor_id, c, text):
    for oid in (c["buyer_opp_id"], c["seller_opp_id"]):
        T.add_activity(db, actor_id, "opportunity", oid, "system", text)


def change_stage(db, actor: dict, cid: int, to: str, reason: str | None = None, note: str | None = None,
                 revisit_on: str | None = None) -> None:
    if not user_can(db, actor, "crm.edit"):
        raise PermissionDenied("crm.edit")
    if to not in STAGES:
        raise ValidationError({"stage": FUTURE_STAGES if to in ("introduced", "visit", "offer") else "Unknown stage."})
    refresh(db, buyer_id=_cand(db, cid)["buyer_opp_id"], seller_id=_cand(db, cid)["seller_opp_id"],
            actor_id=actor["id"])  # never decide on stale data
    c = _cand(db, cid)
    note = (note or "").strip() or None
    if to in SUPPRESSING and reason not in dict(REJECT_REASONS):
        raise ValidationError({"reason": "Choose why it is not suitable — it stops the match reappearing for the "
                                         "same facts."})
    if to == "approved_to_approach":
        if not user_can(db, actor, "evidence.review"):
            raise PermissionDenied("evidence.review")
        if c["gate"] != "pass":
            raise ValidationError({"stage": f"Cannot approve: {c['headline']}"})
        if c["readiness"] != "ready_to_approach":
            r = latest_result(db, cid)
            missing = r.get("consent_missing") if c["readiness"] == "awaiting_consent" else \
                r.get("verification_missing") if c["readiness"] == "awaiting_verification" else [c["next_question"]]
            audit.record(db, actor["id"], "match.approve_blocked", "match", cid, {"readiness": c["readiness"],
                                                                                 "missing": missing}, outcome="denied")
            raise ValidationError({"stage": f"Cannot approve to approach — {READINESS[c['readiness']].lower()}: "
                                            + "; ".join(x for x in (missing or []) if x)})
    if to == "interest_confirmed" and c["stage"] != "approved_to_approach":
        raise ValidationError({"stage": "Interest can be recorded only after the match is approved to approach."})
    now = now_iso()
    with db:
        upd = {"stage": to, "updated_at": now}
        if to in SUPPRESSING:
            b, s = load_buyer(db, c["buyer_opp_id"]), load_seller(db, c["seller_opp_id"])
            upd.update(suppressed=1, suppressed_reason=reason, suppressed_note=note,
                       suppressed_buyer_version=b["m"]["data_version"], suppressed_seller_version=s["l"]["data_version"],
                       revisit_on=revisit_on)
        elif to == "on_hold":
            upd.update(revisit_on=revisit_on)
        else:
            upd.update(suppressed=0)
        db.update("match_candidates", cid, upd)
        label = dict(REJECT_REASONS).get(reason) if reason else None
        _event(db, cid, "stage", actor["id"], c["stage"], to, label, note)
        _log_both(db, actor["id"], c, f"Match {c['buyer_ref']} ↔ {c['seller_ref']}: {STAGES[c['stage']]} → {STAGES[to]}"
                  + (f" ({label})" if label else "") + (f" — {note}" if note else ""))
        audit.record(db, actor["id"], "match.stage", "match", cid, {"from": c["stage"], "to": to, "reason": reason})


def create_subtasks(db, actor: dict, cid: int, buyer_title: str | None, seller_title: str | None,
                    due_at: str | None) -> dict:
    """UAT-35: one shared parent match task with independent buyer-side and seller-side subtasks."""
    if not user_can(db, actor, "crm.log_activity"):
        raise PermissionDenied("crm.log_activity")
    c = _cand(db, cid)
    buyer_title, seller_title = (buyer_title or "").strip(), (seller_title or "").strip()
    if not buyer_title and not seller_title:
        raise ValidationError({"tasks": "Describe at least one side's task."})
    due_at = due_at or T.first_contact_due(24 * 60)
    with db:
        parent = db.one("SELECT * FROM tasks WHERE entity_type = 'match' AND entity_id = ? AND status = 'open' "
                        "AND parent_task_id IS NULL ORDER BY id LIMIT 1", (cid,))
        pid = parent["id"] if parent else T.create_task(
            db, actor, title=f"Resolve match {c['buyer_ref']} ↔ {c['seller_ref']} ({c['salon_name']})",
            assignee_id=c["match_owner_id"] or actor["id"], due_at=due_at, task_type="match_review", priority="high",
            entity_type="match", entity_id=cid)
        made = {}
        for side, title, opp_id, owner in (("buyer", buyer_title, c["buyer_opp_id"], c["buyer_owner_id"]),
                                           ("seller", seller_title, c["seller_opp_id"], c["seller_owner_id"])):
            if not title:
                continue
            dup = db.one("SELECT id FROM tasks WHERE parent_task_id = ? AND entity_id = ? AND status = 'open' "
                         "AND lower(title) = lower(?)", (pid, opp_id, title))
            if dup:  # avoid two people chasing the same thing (§8.7 ex. 9)
                made[side] = dup["id"]
                continue
            made[side] = T.create_task(db, actor, title=title, assignee_id=owner or actor["id"], due_at=due_at,
                                       task_type="collect_info", priority="high", entity_type="opportunity",
                                       entity_id=opp_id, parent_task_id=pid)
        _event(db, cid, "tasks", actor["id"], body="Tasks: " + "; ".join(
            f"{side}: {t}" for side, t in (("buyer", buyer_title), ("seller", seller_title)) if t))
        audit.record(db, actor["id"], "match.subtasks", "match", cid, {"parent": pid, **made})
    return {"parent": pid, **made}


def subtask_progress(db, cid: int) -> list[dict]:
    out = []
    for p in db.all("SELECT t.*, u.full_name AS assignee_name FROM tasks t LEFT JOIN users u ON u.id = t.assignee_id "
                    "WHERE t.entity_type = 'match' AND t.entity_id = ? ORDER BY t.id DESC", (cid,)):
        p["children"] = db.all("SELECT t.*, u.full_name AS assignee_name, o.kind AS opp_kind FROM tasks t "
                               "LEFT JOIN users u ON u.id = t.assignee_id LEFT JOIN opportunities o ON o.id = t.entity_id "
                               "WHERE t.parent_task_id = ? ORDER BY t.id", (p["id"],))
        p["done"] = sum(1 for ch in p["children"] if ch["status"] == "done")
        out.append(p)
    return out


def add_note(db, actor, cid: int, body: str) -> None:
    if not user_can(db, actor, "crm.log_activity"):
        raise PermissionDenied("crm.log_activity")
    if not (body or "").strip():
        raise ValidationError({"body": "Write a note."})
    c = _cand(db, cid)
    with db:
        _event(db, cid, "note", actor["id"], body=body.strip()[:2000])
        for u in db.all("SELECT id, full_name FROM users WHERE is_active = 1"):
            if f"@{u['full_name'].split()[0].lower()}" in body.lower() and u["id"] != actor["id"]:
                T.notify(db, u["id"], "mention", f"{actor['full_name']} mentioned you on match {c['buyer_ref']} ↔ "
                                                 f"{c['seller_ref']}", f"/matches/{cid}")


def set_owner(db, actor, cid: int, user_id: int | None) -> None:
    if not user_can(db, actor, "crm.edit"):
        raise PermissionDenied("crm.edit")
    c = _cand(db, cid)
    u = db.one("SELECT id, full_name FROM users WHERE id = ? AND is_active = 1 AND role <> 'finance'", (user_id,)) \
        if user_id else None
    if user_id and not u:
        raise ValidationError({"owner": "Choose an active team member."})
    with db:
        db.update("match_candidates", cid, {"match_owner_id": u["id"] if u else None, "updated_at": now_iso()})
        _event(db, cid, "owner", actor["id"], body=f"Match co-ordinator: {u['full_name'] if u else 'none'}")
        if u and u["id"] != actor["id"]:
            T.notify(db, u["id"], "assignment", f"You are co-ordinating match {c['buyer_ref']} ↔ {c['seller_ref']}",
                     f"/matches/{cid}")


# The desk lists up to 200 rows: the open-task count is attached afterwards for just those rows.
CAND_SELECT_LIGHT = CAND_SELECT.replace(
    "(SELECT COUNT(*) FROM tasks t WHERE t.status = 'open' AND t.parent_task_id IN "
    " (SELECT id FROM tasks p WHERE p.entity_type = 'match' AND p.entity_id = mc.id)) AS open_tasks ", "0 AS open_tasks ")
assert CAND_SELECT_LIGHT != CAND_SELECT
