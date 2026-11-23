"""Milestone 8 — drafted record summaries, always confirmed by a person.

"Draft summary" on a seller or buyer writes a short paragraph from the record's facts and the latest call notes.
The team member edits it and presses "Save summary"; only then is it stored (with who confirmed it). Nothing
drafted is ever saved or sent automatically.

Two drafters:
* built-in (default): plain sentences from the facts. Runs on this server; no data leaves it.
* Claude (optional, Admin → Integrations → AI writing assistant): better wording. Only non-identifying facts are
  sent — never names, phone numbers, e-mails, addresses, map links or private notes. Off until an admin adds a
  key and switches it on; needs the `anthropic` package on the server.
"""
from __future__ import annotations

from .. import audit
from ..timeutil import now_iso
from .common import format_inr
from .errors import PermissionDenied, ValidationError

# Never sent to an outside AI service, whatever the record contains.
PRIVATE_KEYS = {"address_line", "landmark", "pin_code", "maps_link", "legal_name", "signatory_name",
                "sale_reason_note", "payment_approver", "other_partners", "preferred_callback"}
SELLER_ORDER = ["gender_format", "positioning", "business_model", "franchise_brand", "started_year", "operating_years",
                "chairs", "carpet_area_sqft", "floor_level", "area_type", "parking", "sale_purpose", "asking_price",
                "negotiable", "sale_timeline", "monthly_rent", "maintenance_monthly", "rent_deposit", "lease_expiry",
                "lease_transfer_status", "avg_monthly_sales", "avg_monthly_expenses", "staff_headcount",
                "staff_continuity", "equipment_condition", "top_services", "google_rating", "sale_reason",
                "transition_support"]
BUYER_ORDER = ["need_type", "investor_type", "salon_background", "all_in_max", "budget_min", "budget_max",
               "finance_dependence", "funding_source", "cash_ready", "preferred_city", "relocate_willing",
               "chairs_min", "chairs_max", "area_min_sqft", "max_monthly_rent", "min_monthly_sales", "turnaround_ok",
               "purchase_window", "involvement", "staff_continuity_need", "parking_need", "must_haves", "deal_breakers"]


def _fields(kind):
    if kind == "seller":
        from .seller_fields import BY_KEY
    else:
        from .buyer_fields import BY_KEY
    return BY_KEY


def _show(fd, v) -> str | None:
    if v in (None, ""):
        return None
    if fd.ftype == "inr":
        try:
            return format_inr(int(v))
        except (TypeError, ValueError):
            return str(v)
    if fd.ftype == "enum":
        return dict(fd.options).get(v, str(v))
    return str(v)


def facts(db, opp: dict) -> dict:
    """Known, non-identifying facts of a record plus the latest notes, as (label, value) pairs."""
    kind = opp["kind"]
    by_key = _fields(kind)
    if kind == "seller":
        rec = db.one("SELECT * FROM seller_listings WHERE opportunity_id = ?", (opp["id"],)) or {}
        order = SELLER_ORDER
        place = ", ".join(x for x in [opp.get("locality"), opp.get("city")] if x and "not given" not in x.lower())
    else:
        rec = db.one("SELECT * FROM buyer_mandates WHERE opportunity_id = ?", (opp["id"],)) or {}
        order = BUYER_ORDER
        locs = db.all("SELECT locality FROM buyer_localities WHERE mandate_id = ? AND kind = 'preferred' ORDER BY rank_order",
                      (rec.get("id"),)) if rec else []
        place = ", ".join(r["locality"] for r in locs[:4])
    known = []
    for k in order:
        fd = by_key.get(k)
        if fd and k not in PRIVATE_KEYS:
            v = _show(fd, rec.get(k))
            if v:
                known.append((k, fd.label, v))
    notes = [r["body"] for r in db.all(
        "SELECT body FROM activities WHERE entity_type = 'opportunity' AND entity_id = ? AND body IS NOT NULL AND "
        "kind IN ('call', 'note') ORDER BY at DESC LIMIT 3", (opp["id"],)) if r["body"]]
    from .collect import gaps
    try:
        missing = [g["label"] for g in gaps(db, opp)["items"][:5]]
    except Exception:  # never let a summary fail because of the gap list
        missing = []
    return {"kind": kind, "place": place, "stage": opp.get("stage"), "known": known, "notes": notes,
            "missing": missing}


def draft_builtin(f: dict) -> str:
    k = {key: v for key, _l, v in f["known"]}
    parts = []
    if f["kind"] == "seller":
        what = " ".join(x for x in [k.get("gender_format"), k.get("positioning"), "salon"] if x)
        s = what[0].upper() + what[1:]
        if f["place"]:
            s += f" in {f['place']}"
        if k.get("started_year"):
            s += f", running since {k['started_year']}"
        elif k.get("operating_years"):
            s += f", running for {k['operating_years']} years"
        size = ", ".join(x for x in [f"{k['chairs']} chairs" if k.get("chairs") else None,
                                     f"{k['carpet_area_sqft']} sq ft" if k.get("carpet_area_sqft") else None,
                                     k.get("floor_level")] if x)
        parts.append(s + (f" ({size})" if size else "") + ".")
        sale = []
        if k.get("sale_purpose"):
            sale.append(f"Looking to: {k['sale_purpose'].lower()}")
        if k.get("asking_price"):
            sale.append(f"asking {k['asking_price']}" + (f" ({k['negotiable'].lower()})" if k.get("negotiable") else ""))
        if k.get("sale_timeline"):
            sale.append(f"timeline {k['sale_timeline'].lower()}")
        if sale:
            parts.append("; ".join(sale) + ".")
        money = []
        if k.get("monthly_rent"):
            money.append(f"rent {k['monthly_rent']}" + (f" + {k['maintenance_monthly']} maintenance"
                                                        if k.get("maintenance_monthly") else ""))
        if k.get("lease_expiry"):
            money.append(f"agreement till {k['lease_expiry']}")
        if k.get("avg_monthly_sales"):
            money.append(f"sales about {k['avg_monthly_sales']} a month (seller-declared)")
        if k.get("staff_headcount"):
            money.append(f"{k['staff_headcount']} staff")
        if money:
            parts.append(money[0][0].upper() + "; ".join(money)[1:] + ".")
        if k.get("sale_reason"):
            parts.append(f"Reason for selling: {k['sale_reason'].lower()}.")
    else:
        what = k.get("need_type") or "Buyer"
        s = what[0].upper() + what[1:]
        if k.get("investor_type"):
            s += f" ({k['investor_type'].lower()})"
        if f["place"] or k.get("preferred_city"):
            s += f", looking in {f['place'] or k['preferred_city']}"
        parts.append(s + ".")
        money = []
        if k.get("all_in_max"):
            money.append(f"total cash up to {k['all_in_max']}")
        elif k.get("budget_max"):
            money.append(f"salon price up to {k['budget_max']}")
        if k.get("finance_dependence"):
            money.append(f"loan: {k['finance_dependence'].lower()}")
        if k.get("cash_ready"):
            money.append(f"money ready {k['cash_ready'].lower()}")
        if money:
            parts.append(money[0][0].upper() + "; ".join(money)[1:] + ".")
        wants = []
        if k.get("chairs_min"):
            wants.append(f"at least {k['chairs_min']} chairs")
        if k.get("max_monthly_rent"):
            wants.append(f"rent + maintenance under {k['max_monthly_rent']}")
        if k.get("purchase_window"):
            wants.append(f"wants to buy {k['purchase_window'].lower()}")
        if k.get("involvement"):
            wants.append(k["involvement"].lower())
        if wants:
            parts.append("Wants " + ", ".join(wants) + ".")
        if k.get("must_haves"):
            parts.append(f"Must-haves: {k['must_haves']}.")
    if f["notes"]:
        parts.append(f"Latest note: {f['notes'][0].strip()[:240]}")
    if f["missing"]:
        parts.append("Still to find out: " + ", ".join(m.lower() for m in f["missing"][:4]) + ".")
    return " ".join(parts).replace("..", ".")


def _ai_ready(db) -> tuple[bool, str]:
    from ..messaging import setting
    if (setting("AI_ASSISTANT_ENABLED") or "").lower() not in ("1", "yes", "on", "true"):
        return False, "off"
    if not setting("ANTHROPIC_API_KEY"):
        return False, "no key"
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False, "the 'anthropic' package is not installed on the server"
    return True, "ready"


def draft_claude(db, f: dict) -> str:
    """Better wording from Claude, using only the non-identifying facts. Raises on any failure (caller falls back)."""
    import anthropic

    from ..messaging import setting
    client = anthropic.Anthropic(api_key=setting("ANTHROPIC_API_KEY"), timeout=40.0, max_retries=1)
    lines = [f"- {label}: {value}" for _k, label, value in f["known"]]
    who = "salon owner who may sell" if f["kind"] == "seller" else "buyer / investor looking for a salon"
    prompt = (f"Record type: {who}.\nArea: {f['place'] or 'not known'}.\nKnown facts:\n" + "\n".join(lines) +
              ("\nLatest team notes:\n" + "\n".join(f"- {n[:400]}" for n in f["notes"]) if f["notes"] else "") +
              ("\nStill unknown: " + ", ".join(f["missing"]) if f["missing"] else ""))
    resp = client.messages.create(
        model=setting("AI_MODEL") or "claude-opus-5-5",
        max_tokens=1024,
        output_config={"effort": "low"},
        system=("You write short internal summaries for Salon Shastra, a team in Hyderabad that helps salon owners "
                "sell their salons and helps buyers find one. Write one paragraph of 3-5 plain sentences a busy "
                "team member can read in 20 seconds: what this is, the key numbers, what the person wants, and "
                "what is still unknown. Use only the facts given; never invent numbers. Use Indian money style "
                "as given (₹, lakh). No headings, no bullet points, no names."),
        messages=[{"role": "user", "content": prompt}],
    )
    if resp.stop_reason == "refusal":
        raise RuntimeError("declined")
    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    if not text:
        raise RuntimeError("empty")
    return text


def draft(db, actor: dict, opp: dict) -> dict:
    """Returns {"text", "source", "note"} — never saves anything."""
    from ..permissions import user_can
    if not user_can(db, actor, "crm.view"):
        raise PermissionDenied("You cannot see this record.")
    f = facts(db, opp)
    ok, why = _ai_ready(db)
    if ok:
        try:
            return {"text": draft_claude(db, f), "source": "claude",
                    "note": "Drafted by Claude from the facts on this record (no names or contact details were sent). "
                            "Check every number before saving."}
        except Exception:  # network, key, quota… fall back quietly but say so
            return {"text": draft_builtin(f), "source": "built_in",
                    "note": "The AI assistant could not be reached, so this is the built-in draft. Check it before "
                            "saving."}
    return {"text": draft_builtin(f), "source": "built_in",
            "note": "Drafted from the facts on this record. Edit anything that is wrong, then save."}


def save(db, actor: dict, opp: dict, text: str, source: str | None) -> None:
    from ..permissions import user_can
    if not user_can(db, actor, "crm.edit"):
        raise PermissionDenied("You cannot edit this record.")
    text = (text or "").strip()
    if len(text) > 2000:
        raise ValidationError({"summary": "Keep the summary under 2,000 characters."})
    db.execute("UPDATE opportunities SET summary = ?, summary_source = ?, summary_by = ?, summary_at = ? WHERE id = ?",
               (text or None, source if source in ("built_in", "claude", "manual") else "manual", actor["id"],
                now_iso(), opp["id"]))
    audit.record(db, actor["id"], "opportunity.summary", "opportunity", opp["id"], {"source": source,
                                                                                     "length": len(text)})
