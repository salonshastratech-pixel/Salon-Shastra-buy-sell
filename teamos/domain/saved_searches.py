"""Milestone 10 — saved searches on the Sellers and Buyers lists, with "new since you last looked".

The list filters live here (one definition), so the list page and the "N new" count can never disagree.
"Owner: me" always means the person who saved the search.
"""
from __future__ import annotations

from urllib.parse import parse_qsl, urlencode

from .. import audit
from ..timeutil import now_iso
from .errors import ValidationError

MAX_PER_PERSON = 20
KEYS = {"seller": ["q", "stage", "owner", "status", "temp", "cat", "came", "src", "contact", "sort"],
        "buyer": ["q", "stage", "owner", "need", "status", "queue", "temp", "cat", "came", "src", "contact", "sort"]}
FROM = {
    "seller": ("FROM opportunities o JOIN contacts c ON c.id = o.contact_id LEFT JOIN salon_businesses b "
               "ON b.id = o.business_id LEFT JOIN users u ON u.id = o.primary_owner_id LEFT JOIN seller_listings l "
               "ON l.opportunity_id = o.id "),
    "buyer": ("FROM opportunities o JOIN contacts c ON c.id = o.contact_id LEFT JOIN users u "
              "ON u.id = o.primary_owner_id LEFT JOIN buyer_mandates m ON m.opportunity_id = o.id "),
}


def _get(args, k):
    return (args.get(k) or "").strip()


def where(kind: str, args, user_id: int) -> tuple[list[str], list]:
    """SQL conditions for the list filters in `args` (request.args or a saved query)."""
    w, p = [f"o.kind = '{'seller' if kind == 'seller' else 'buyer'}'"], []
    q = _get(args, "q")
    if q:
        like = f"%{q.lower()}%"
        digits = "".join(ch for ch in q if ch.isdigit())
        if kind == "seller":
            clause = ("(lower(c.full_name) LIKE ? OR lower(b.name) LIKE ? OR lower(b.locality) LIKE ? OR "
                      "lower(o.ref_code) LIKE ?")
        else:
            clause = ("(lower(c.full_name) LIKE ? OR lower(o.ref_code) LIKE ? OR lower(m.preferred_city) LIKE ? OR "
                      "EXISTS (SELECT 1 FROM buyer_localities bl WHERE bl.mandate_id = m.id AND "
                      "lower(bl.locality) LIKE ?)")
        p += [like, like, like, like]
        if len(digits) >= 4:
            clause += " OR c.phone_normalized LIKE ?"
            p.append(f"%{digits[-10:]}%")
        w.append(clause + ")")
    if _get(args, "stage"):
        w.append("o.stage = ?")
        p.append(_get(args, "stage"))
    owner = _get(args, "owner")
    if owner == "me":
        w.append("o.primary_owner_id = ?")
        p.append(user_id)
    elif owner.isdigit():
        w.append("o.primary_owner_id = ?")
        p.append(int(owner))
    if kind == "buyer":
        if _get(args, "need"):
            w.append("m.need_type = ?")
            p.append(_get(args, "need"))
        queue = _get(args, "queue")
        if queue == "criteria":
            w.append("(m.criteria_reviewed_version IS NULL OR m.criteria_reviewed_version <> m.data_version)")
        elif queue == "funding":
            w.append("m.funding_status IN ('not_reviewed','evidence_requested')")
    from .lead_marks import LEAD_AT
    temp = _get(args, "temp")
    if temp in ("hot", "warm", "normal", "cold"):
        w.append("COALESCE(o.lead_temperature, 'normal') = ?")
        p.append(temp)
    cat = _get(args, "cat")
    if cat and cat.replace("_", "").isalpha():
        w.append("(',' || COALESCE(o.lead_category, '') || ',') LIKE ?")
        p.append(f"%,{cat},%")
    came = _get(args, "came")
    if came:
        from datetime import timedelta

        from ..timeutil import ist_day_bounds, now_utc, to_iso
        days = {"7d": 7, "30d": 30, "90d": 90}
        if came == "today":
            w.append(f"{LEAD_AT} >= ?")
            p.append(ist_day_bounds()[0])
        elif came in days:
            w.append(f"{LEAD_AT} >= ?")
            p.append(to_iso(now_utc() - timedelta(days=days[came])))
        elif came == "older":
            w.append(f"{LEAD_AT} < ?")
            p.append(to_iso(now_utc() - timedelta(days=90)))
        elif came == "unknown":
            w.append(f"{LEAD_AT} IS NULL")
    src = _get(args, "src")
    if src and src.replace("_", "").isalpha():
        w.append("o.source = ?")
        p.append(src)
    contact = _get(args, "contact")
    if contact == "not_yet":
        w.append("o.first_contact_at IS NULL")
    elif contact == "done":
        w.append("o.first_contact_at IS NOT NULL")
    elif contact == "remark":
        w.append("o.team_remark IS NOT NULL AND o.team_remark <> ''")
    status = _get(args, "status") or "active"
    if status in ("active", "holding", "closed"):
        w.append("o.status = ?")
        p.append(status)
    return w, p


def query_string(kind: str, args) -> str:
    """The filters worth saving, in a stable order (empty values and the default status dropped)."""
    pairs = [(k, _get(args, k)) for k in KEYS[kind] if _get(args, k)]
    return urlencode([(k, v) for k, v in pairs if not (k == "status" and v == "active")])


def describe(kind: str, qs: str, stage_labels: dict, users: dict) -> str:
    a = dict(parse_qsl(qs))
    bits = []
    if a.get("q"):
        bits.append(f"“{a['q']}”")
    if a.get("stage"):
        bits.append(stage_labels.get(a["stage"], a["stage"]))
    if a.get("owner") == "me":
        bits.append("mine")
    elif a.get("owner", "").isdigit():
        bits.append(users.get(int(a["owner"]), "someone"))
    if a.get("need"):
        bits.append(a["need"].replace("_", " "))
    if a.get("queue"):
        bits.append({"criteria": "criteria to review", "funding": "funding to review"}.get(a["queue"], a["queue"]))
    if a.get("status"):
        bits.append(a["status"])
    return " · ".join(bits) or "all active"


def _count_new(db, s) -> int:
    w, p = where(s["kind"], dict(parse_qsl(s["query"])), s["user_id"])
    return db.scalar("SELECT COUNT(*) " + FROM[s["kind"]] + "WHERE " + " AND ".join(w) + " AND o.created_at > ?",
                     p + [s["last_seen_at"]]) or 0


def mine(db, user_id: int, kind: str | None = None) -> list[dict]:
    rows = db.all("SELECT * FROM saved_searches WHERE user_id = ?" + (" AND kind = ?" if kind else "") +
                  " ORDER BY name", (user_id, kind) if kind else (user_id,))
    for s in rows:
        s["new"] = _count_new(db, s)
    return rows


def save(db, actor: dict, kind: str, name: str, args) -> int:
    if kind not in KEYS:
        raise ValidationError("Unknown list.")
    name = (name or "").strip()[:60]
    if not name:
        raise ValidationError("Give the search a short name, e.g. “Kondapur buyers”.")
    qs = query_string(kind, args)
    if not qs:
        raise ValidationError("Choose at least one filter first, then save.")
    same = db.one("SELECT id FROM saved_searches WHERE user_id = ? AND kind = ? AND query = ?", (actor["id"], kind, qs))
    now = now_iso()
    if same:
        db.update("saved_searches", same["id"], {"name": name})
        return same["id"]
    if db.scalar("SELECT COUNT(*) FROM saved_searches WHERE user_id = ?", (actor["id"],)) >= MAX_PER_PERSON:
        raise ValidationError(f"You can keep up to {MAX_PER_PERSON} saved searches — delete one first.")
    sid = db.insert("saved_searches", {"user_id": actor["id"], "kind": kind, "name": name, "query": qs,
                                       "last_seen_at": now, "created_at": now})
    audit.record(db, actor["id"], "saved_search.created", None, None, {"kind": kind, "query": qs})
    return sid


def open_search(db, actor: dict, sid: int) -> dict | None:
    """Mark as seen now (the "new" count resets) and return it — only the owner's own searches."""
    s = db.one("SELECT * FROM saved_searches WHERE id = ? AND user_id = ?", (sid, actor["id"]))
    if s:
        db.update("saved_searches", sid, {"last_seen_at": now_iso()})
    return s


def delete(db, actor: dict, sid: int) -> None:
    db.execute("DELETE FROM saved_searches WHERE id = ? AND user_id = ?", (sid, actor["id"]))



PAGE_SIZE = 100


def page(args) -> int:
    v = _get(args, "page")
    return max(1, min(int(v), 1000)) if v.isdigit() else 1
