"""Bring the team's sheets in as the source of truth, once: every lead from every workbook judged together.

  dry   — work out what would happen and save the report; change nothing
  apply — remove test / dummy records only; import everyone else (new records created, existing ones updated —
          hot / warm / normal / cold, category, the team's latest remark, when the lead came in). Nothing real is deleted:
          cold and not-interested leads come in marked cold and sort last. Job seekers are not leads and stay out.

Progress and the report are kept in automation_settings 'sheets_rebuild' (JSON) for the admin page / a later check.
"""
from __future__ import annotations

import json
import re

from ..timeutil import now_iso
from . import purge
from . import sheet_sync as SS

KEY = "sheets_rebuild"
NEGATIVE = ("Looking for a job",)   # nothing else is left out: cold leads come in, marked cold
TEST_NAME = re.compile(r"^\s*(test|testing|sample|dummy)\b", re.I)


def _status(db, st: dict) -> None:
    st["at"] = now_iso()
    if db.scalar("SELECT 1 FROM automation_settings WHERE key = ?", (KEY,)):
        db.execute("UPDATE automation_settings SET note = ?, updated_at = ? WHERE key = ?", (json.dumps(st), st["at"], KEY))
    else:
        db.insert("automation_settings", {"key": KEY, "enabled": 1, "note": json.dumps(st), "updated_at": st["at"]})


def status(db) -> dict | None:
    raw = db.scalar("SELECT note FROM automation_settings WHERE key = ?", (KEY,))
    return json.loads(raw) if raw else None


def _good_call(db, opp_id: int) -> bool:
    return bool(db.scalar("SELECT 1 FROM activities WHERE entity_type = 'opportunity' AND entity_id = ? AND kind = 'call' "
                          "AND call_outcome IN ('connected', 'callback_requested')", (opp_id,)))


def run(db, actor: dict, urls: list[str], mode: str = "dry", remove_refs: list[str] | None = None,
        fetcher=None) -> dict:
    from . import connected_sheets as CS
    fetch = fetcher or CS.fetch
    _status(db, {"state": "running", "mode": mode, "step": "downloading sheets"})
    try:
        files, seen = [], set()
        for u in urls:
            key = CS.sheet_key(u)
            if key and key not in seen:
                seen.add(key)
                cs = db.one("SELECT * FROM connected_sheets WHERE sheet_key = ? AND active = 1", (key,))
                files.append((*fetch(u), CS.chosen(cs) if cs else None))
        _status(db, {"state": "running", "mode": mode, "step": f"reading {len(files)} workbooks"})
        p = SS.plan_many(db, files)
        rows = p["rows"]
        kept = {r["phone"] for r in rows if r["phone"] and r["action"] in ("create", "update", "merged")}
        out = {}
        for r in rows:
            if r["phone"] and r["action"] == "skip" and r["phone"] not in kept and \
                    any((r["reason"] or "").startswith(n) for n in NEGATIVE):
                out.setdefault(r["phone"], r["reason"])
        existing_out = db.all(
            "SELECT o.id, o.ref_code, c.full_name, c.phone_normalized FROM opportunities o JOIN contacts c ON "
            "c.id = o.contact_id")
        to_remove = []   # the founder's rule: never delete leads — cold ones are kept and sorted last
        kept_anyway = []
        tests = [e for e in existing_out if (remove_refs and e["ref_code"] in remove_refs)
                 or TEST_NAME.search(e["full_name"] or "")]
        report = {
            "workbooks": len(files), "rows": len(rows),
            "will_create": {k: sum(1 for r in rows if r["action"] == "create" and r["kind"] == k) for k in ("seller", "buyer")},
            "will_update": sum(1 for r in rows if r["action"] == "update"),
            "left_out_people": len(out), "cold": sum(1 for r in rows if r["action"] in ("create", "update") and (r["understood"].get("meta") or {}).get("temperature") == "cold"),
            "left_out_by_reason": _by_reason(out),
            "remove_existing_cold": [e["ref_code"] for e in to_remove],
            "kept_despite_sheet_cold": kept_anyway,
            "remove_tests": [e["ref_code"] for e in tests],
            "tabs": [{"book": t["book"][:20], "tab": t["name"], "kind": t["kind"], "why": t["why"][:80]} for t in p["tabs"]],
        }
        if mode != "apply":
            _status(db, {"state": "done", "mode": mode, "report": report})
            return report
        _status(db, {"state": "running", "mode": mode, "step": "removing test and cold records", "report": report})
        if tests:
            purge.delete_records(db, actor, [e["id"] for e in tests], "Test / sample records", remember=False)
        with db:
            for ph, why in out.items():
                purge.exclude(db, ph, why)
        _status(db, {"state": "running", "mode": mode, "step": "importing", "report": report})
        iid = SS.create_import_many(db, actor, "Team sheets — full rebuild", files)
        counts = SS.commit(db, actor, iid)
        report["import_id"], report["counts"] = iid, counts
        _status(db, {"state": "done", "mode": mode, "report": report})
        return report
    except Exception as e:  # noqa: BLE001 — reported; whatever finished stays finished
        _status(db, {"state": "failed", "mode": mode, "error": f"{type(e).__name__}: {e}"[:400]})
        raise


def _by_reason(out: dict) -> dict:
    agg: dict[str, int] = {}
    for why in out.values():
        k = re.sub(r"\s*[\(—'].*$", "", why)[:40]
        agg[k] = agg.get(k, 0) + 1
    return agg
