"""QA: what the smart import would do with each workbook — on a fresh, empty database (nothing real is touched).

  python -I scripts/qa/sheet_dryrun.py <out.db> <book1.xlsx> [book2.xlsx ...]

Prints per tab: what the tab was understood as, and per row the decision (create / update / merged / skip + reason).
Writes every row decision to <out.db>.rows.csv for a line-by-line check.
"""
import collections
import re
import csv
import os
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def main():
    out, books = sys.argv[1], sys.argv[2:]
    if os.path.exists(out):
        os.remove(out)
    from teamos import create_app
    create_app({"DATABASE_URL": f"sqlite:///{out}", "TESTING": True, "SECRET_KEY": "x"})
    from teamos.db import open_standalone
    from teamos.domain import sheet_sync as SS
    db = open_standalone(f"sqlite:///{out}")
    allrows = []
    p = SS.plan_many(db, [(Path(b).name, Path(b).read_bytes()) for b in books])
    for t in p["tabs"]:
        rows = [r for r in p["rows"] if r["tab"] == t["name"] and r["book"] == t["book"]]
        acts = collections.Counter(r["action"] for r in rows)
        kinds = collections.Counter(r["kind"] for r in rows if r["action"] != "skip")
        print(f"  {t['book'][:6]} {t['name'][:30]:30} -> {t['kind']:6} {t.get('flavour') or '':10} rows={t['rows']:4} "
              f"{dict(acts)} {dict(kinds)}  ({t['why'][:60]})")
        reasons = collections.Counter(re.sub(r"\(.*", "", r["reason"] or "")[:50] for r in rows if r["action"] == "skip")
        for why, n in reasons.most_common(6):
            print(f"        skip {n:3}  {why}")
    keep = [r for r in p["rows"] if r["action"] in ("create", "update")]
    print("\nRECORDS:", collections.Counter(r["kind"] for r in keep))
    print("temperature:", collections.Counter((r["kind"], r["understood"]["meta"]["temperature"]) for r in keep))
    print("category:", collections.Counter((r["kind"], r["understood"]["meta"]["category"]) for r in keep))
    print("with remark:", sum(1 for r in keep if r["understood"]["meta"]["remark"]), "· with lead time:",
          sum(1 for r in keep if r["canon"].get("timestamp")))
    print("skipped people (distinct phones):", collections.Counter(re.sub(r"\(.*|'.*", "", r["reason"] or "")[:40]
          for r in {r["phone"]: r for r in p["rows"] if r["action"] == "skip"}.values()))
    for r in p["rows"]:
        c = r["canon"]
        meta = r["understood"].get("meta") or {}
        allrows.append([r["book"][:10], r["tab"], r["row_no"], r["kind"], r["action"], r["reason"] or "",
                        r["phone"] or "", c.get("full_name", ""), c.get("timestamp", ""), "|".join(r["marks"]),
                        meta.get("temperature", ""), meta.get("category", ""), meta.get("remark", "")[:200],
                        " | ".join(r["notes"])[:300]])
    with open(out + ".rows.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["book", "tab", "row", "kind", "action", "reason", "phone", "name", "timestamp", "marks", "temperature", "category", "remark", "notes"])
        w.writerows(allrows)
    print(f"\nrows: {len(allrows)} · written to {out}.rows.csv")


if __name__ == "__main__":
    main()
