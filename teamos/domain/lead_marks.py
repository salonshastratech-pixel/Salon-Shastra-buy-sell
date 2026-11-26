"""What the team already knows about a lead, kept on the record: temperature (hot / warm / normal), category, and the
team's latest remark. Plus the honest "when did this lead come in" — an imported lead whose sheet never recorded a
date has NO lead time (it must not look like it came in on the day it was imported)."""
from __future__ import annotations

TEMPS = [("hot", "Hot"), ("warm", "Warm"), ("normal", "Normal"), ("cold", "Cold")]
TEMP_LABELS = dict(TEMPS)
CATEGORIES = {
    "seller": [("sell", "Wants to sell"), ("help", "Needs help"), ("staff", "Needs staff")],
    "buyer": [("buy_salon", "Buy a running salon"), ("new_salon", "Open a new salon"), ("investor", "Investor (passive)")],
}
CATEGORY_LABELS = {k: v for side in CATEGORIES.values() for k, v in side}
CAME_IN = [("today", "Today"), ("7d", "Last 7 days"), ("30d", "Last 30 days"), ("90d", "Last 90 days"),
           ("older", "Older than 90 days"), ("unknown", "Date not recorded")]
SORTS = [("latest", "Latest leads first (cold last)"), ("priority", "Hot first, then latest"), ("newest", "Newest lead first"), ("oldest", "Oldest lead first"),
         ("next", "Next action due"), ("updated", "Recently updated")]
SOURCES = [("import", "Sheet import"), ("meta_lead", "Facebook / Instagram ad"), ("landing_page", "Website form"),
           ("referral", "Referral"), ("whatsapp", "WhatsApp"), ("manual_call", "Phone call"), ("event", "Event")]

# When the lead came in, honestly (alias o = opportunities)
LEAD_AT = ("(CASE WHEN o.source = 'import' AND o.lead_created_at IS NULL THEN NULL "
           "ELSE COALESCE(o.lead_created_at, o.created_at) END)")


def categories(value: str | None) -> list[str]:
    return [CATEGORY_LABELS.get(c, c.replace("_", " ")) for c in (value or "").split(",") if c]


def lead_at(row: dict) -> str | None:
    if row.get("source") == "import" and not row.get("lead_created_at"):
        return None
    return row.get("lead_created_at") or row.get("created_at")


def order_by(sort: str) -> str:
    temp = ("CASE COALESCE(o.lead_temperature, 'normal') WHEN 'hot' THEN 0 WHEN 'warm' THEN 1 WHEN 'cold' THEN 3 ELSE 2 END")
    if sort == "newest":
        return f"CASE WHEN {LEAD_AT} IS NULL THEN 1 ELSE 0 END, {LEAD_AT} DESC, o.id DESC"
    if sort == "oldest":
        return f"CASE WHEN {LEAD_AT} IS NULL THEN 1 ELSE 0 END, {LEAD_AT}, o.id"
    if sort == "next":
        return "CASE WHEN o.next_action_due IS NULL THEN 1 ELSE 0 END, o.next_action_due, o.id DESC"
    if sort == "updated":
        return "o.updated_at DESC, o.id DESC"
    if sort == "priority":
        return f"{temp}, CASE WHEN {LEAD_AT} IS NULL THEN 1 ELSE 0 END, {LEAD_AT} DESC, o.id DESC"
    # default — the latest leads first (undated after dated), cold leads at the very end
    cold = "CASE WHEN o.lead_temperature = 'cold' THEN 1 ELSE 0 END"
    return f"{cold}, CASE WHEN {LEAD_AT} IS NULL THEN 1 ELSE 0 END, {LEAD_AT} DESC, o.id DESC"
