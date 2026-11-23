"""Milestone 8 — learning from declined matches (outcome-tuned weights, with founder approval).

When the team declines a match, they pick a reason (budget, location, rent…). If many pairs that the engine scored
HIGH are declined for the same reason, that part of the scoring is weighted too lightly. The system then PROPOSES
a modest increase for that family of criteria. Nothing changes until the founder approves it; every approved
change is versioned (the match rules version shows "tuned #n") and can be reset. No silent model drift.
"""
from __future__ import annotations

import json
from datetime import timedelta

from .. import audit
from ..permissions import user_can
from ..timeutil import now_iso, now_utc, to_iso
from . import matching as M
from .errors import PermissionDenied, ValidationError

REASON_FAMILY = {"budget": "A", "location": "B", "format": "C", "financials": "E", "rent": "F", "size": "F",
                 "lease": "F", "staff": "G", "timing": "H"}
WINDOW_DAYS = 90
HIGH_FIT = 60          # a pair the engine thought was a good fit
MIN_DECLINES = 5       # at least this many high-fit declines for one reason …
MIN_SHARE = 0.25       # … and at least this share of all high-fit declines
STEP = 0.25            # propose +25% …
MAX_FACTOR = 2.0       # … never more than double the starting weight
QUIET_DAYS_AFTER_REJECT = 30


def decline_stats(db, days: int = WINDOW_DAYS) -> dict:
    """Why matches were declined in the last `days`, split by whether the engine had scored them high."""
    since = to_iso(now_utc() - timedelta(days=days))
    label_to_key = {v: k for k, v in M.REJECT_REASONS}
    rows = db.all("SELECT e.reason, mc.fit_score FROM match_events e JOIN match_candidates mc ON mc.id = e.candidate_id "
                  "WHERE e.event = 'stage' AND e.to_stage IN ('not_suitable','buyer_declined','seller_declined') "
                  "AND e.at >= ?", (since,))
    by_reason: dict[str, dict] = {}
    for r in rows:
        key = label_to_key.get(r["reason"], r["reason"] or "other")
        d = by_reason.setdefault(key, {"key": key, "label": dict(M.REJECT_REASONS).get(key, key), "all": 0, "high": 0})
        d["all"] += 1
        if (r["fit_score"] or 0) >= HIGH_FIT:
            d["high"] += 1
    total = sum(d["all"] for d in by_reason.values())
    high = sum(d["high"] for d in by_reason.values())
    for d in by_reason.values():
        d["share"] = round(100 * d["all"] / total) if total else 0
        fam = REASON_FAMILY.get(d["key"])
        d["family"] = fam
        d["family_label"] = M.FAMILIES[fam][0] if fam else None
    return {"days": days, "total": total, "high": high,
            "reasons": sorted(by_reason.values(), key=lambda d: -d["all"])}


def current_weights(db) -> list[dict]:
    eff = M.load_weights(db)
    return [{"family": k, "label": v[0], "default": v[1], "weight": eff[k], "changed": eff[k] != v[1]}
            for k, v in M.FAMILIES.items()]


def propose(db) -> list[int]:
    """Create proposals from the evidence (idempotent: one open proposal per family)."""
    stats = decline_stats(db)
    eff = M.load_weights(db)
    quiet_since = to_iso(now_utc() - timedelta(days=QUIET_DAYS_AFTER_REJECT))
    made = []
    high_total = stats["high"]
    per_family: dict[str, dict] = {}
    for d in stats["reasons"]:
        if d["family"]:
            f = per_family.setdefault(d["family"], {"high": 0, "reasons": []})
            f["high"] += d["high"]
            f["reasons"].append(f"{d['label']}: {d['high']} of {d['all']}")
    for fam, f in per_family.items():
        if f["high"] < MIN_DECLINES or not high_total or f["high"] / high_total < MIN_SHARE:
            continue
        if db.scalar("SELECT 1 FROM match_weight_changes WHERE family = ? AND (status = 'proposed' OR "
                     "(status = 'rejected' AND decided_at >= ?))", (fam, quiet_since)):
            continue
        old = eff[fam]
        cap = int(M.FAMILIES[fam][1] * MAX_FACTOR)
        new = min(cap, max(old + 1, round(old * (1 + STEP))))
        if new <= old:
            continue
        label = M.FAMILIES[fam][0]
        reason = (f"In the last {stats['days']} days, {f['high']} of the {high_total} matches the system scored as a "
                  f"good fit were declined because of {label.lower()} ({', '.join(f['reasons'])}). Giving "
                  f"{label.lower()} more weight should push such pairs further down the Match Desk.")
        made.append(db.insert("match_weight_changes", {
            "family": fam, "old_weight": old, "new_weight": new, "reason": reason,
            "evidence": json.dumps({"high_fit_declines": f["high"], "all_high_fit_declines": high_total,
                                    "reasons": f["reasons"], "window_days": stats["days"]}),
            "status": "proposed", "proposed_at": now_iso()}))
    return made


def proposals(db, status: str | None = None) -> list[dict]:
    q = ("SELECT w.*, u.full_name AS decided_by_name FROM match_weight_changes w LEFT JOIN users u ON u.id = w.decided_by")
    params = []
    if status:
        q += " WHERE w.status = ?"
        params.append(status)
    rows = db.all(q + " ORDER BY w.id DESC LIMIT 50", params)
    for r in rows:
        r["family_label"] = M.FAMILIES.get(r["family"], ("?",))[0]
    return rows


def decide(db, actor: dict, change_id: int, approve: bool, note: str | None = None) -> None:
    if not user_can(db, actor, "settings.manage"):
        raise PermissionDenied("Only the founder / an admin can change how matches are scored.")
    w = db.one("SELECT * FROM match_weight_changes WHERE id = ?", (change_id,))
    if not w or w["status"] != "proposed":
        raise ValidationError({"_": "This suggestion was already decided."})
    now = now_iso()
    if approve:
        db.execute("UPDATE match_weight_changes SET status = 'superseded' WHERE family = ? AND status = 'approved'",
                   (w["family"],))
    db.execute("UPDATE match_weight_changes SET status = ?, decided_by = ?, decided_at = ?, decision_note = ? "
               "WHERE id = ?", ("approved" if approve else "rejected", actor["id"], now, note, change_id))
    audit.record(db, actor["id"], "matching.weight_" + ("approved" if approve else "rejected"), "match_weight_change",
                 change_id, {"family": w["family"], "old": w["old_weight"], "new": w["new_weight"]})
    if approve:
        _rescore(db)


def reset(db, actor: dict, family: str) -> None:
    """Return one family to its starting weight (recorded as an approved change, so history stays complete)."""
    if not user_can(db, actor, "settings.manage"):
        raise PermissionDenied("Only the founder / an admin can change how matches are scored.")
    if family not in M.FAMILIES:
        raise ValidationError({"_": "Unknown criteria family."})
    eff = M.load_weights(db)
    default = M.FAMILIES[family][1]
    if eff[family] == default:
        return
    now = now_iso()
    db.execute("UPDATE match_weight_changes SET status = 'superseded' WHERE family = ? AND status = 'approved'",
               (family,))
    cid = db.insert("match_weight_changes", {
        "family": family, "old_weight": eff[family], "new_weight": default, "reason": "Reset to the starting weight.",
        "status": "approved", "proposed_at": now, "decided_by": actor["id"], "decided_at": now})
    audit.record(db, actor["id"], "matching.weight_reset", "match_weight_change", cid, {"family": family})
    _rescore(db)


def _rescore(db) -> None:
    M.load_weights(db)
    from .. import jobs
    jobs.enqueue(db, "match_refresh", dedupe_key="match_refresh:weights:" + M.RULES_VERSION)
