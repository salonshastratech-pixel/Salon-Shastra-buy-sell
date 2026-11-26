"""QA: rehearse the full sheets rebuild on a COPY of the demo database, with real workbooks from disk.

  python -I scripts/qa/rebuild_rehearsal.py <demo.db> <out.db> <dir-with-xlsx>

1. copy the demo DB, remove every record (start fresh), add a few "existing" leads like live has (incl. one the sheets
   mark cold and one TEST record), 2. dry run, 3. apply, 4. apply again (must create nothing new), 5. checks.
"""
import glob
import json
import shutil
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def main():
    src, out, d = sys.argv[1], sys.argv[2], sys.argv[3]
    shutil.copy(src, out)
    from teamos import create_app
    app = create_app({"DATABASE_URL": f"sqlite:///{out}", "TESTING": True, "SECRET_KEY": "x", "MFA_REQUIRED_ROLES": ""})
    from teamos.db import open_standalone
    from teamos.domain import purge, rebuild
    from teamos.domain import sellers as S
    db = open_standalone(f"sqlite:///{out}")
    admin = db.one("SELECT * FROM users WHERE role = 'super_admin' ORDER BY id LIMIT 1")
    ids = [r["id"] for r in db.all("SELECT id FROM opportunities")]
    if ids:
        purge.clear_all(db, admin, ids, "rehearsal: start from empty")
    files = {Path(f).stem: f for f in glob.glob(d + "/*.xlsx")}
    urls = [f"https://docs.google.com/spreadsheets/d/{k}/edit" for k in files]

    def fetcher(u):
        k = u.split("/d/")[1].split("/")[0]
        return f"google-sheet-{k[:8]}.xlsx", Path(files[k]).read_bytes()
    with app.app_context():
        # live-like starting point: one lead the sheets call Cool (Kiran-type), one TEST record, one normal lead
        def lead(name, phone, salon):
            return S.create_lead(db, admin, {"full_name": name, "phone": phone, "salon_name": salon, "city": "Hyderabad",
                                             "locality": "Kondapur", "source": "import", "consent_contact": "1",
                                             "owner_id": str(admin["id"])})["opportunity_id"]
        rows = rebuild.run(db, admin, urls, "dry", fetcher=fetcher)
        cool_phone = None
        from teamos.domain import sheet_sync as SS
        p = SS.plan_many(db, [fetcher(u) for u in urls])
        cool = next(r for r in p["rows"] if (r["understood"].get("meta") or {}).get("temperature") == "cold"
                    and r["action"] == "create" and r["phone"])
        cool_phone = cool["phone"][-10:]
        lead("Existing Cold", cool_phone, "Cold Salon")
        lead("TEST One", "9000000201", "TEST One Salon")
        t0 = time.time()
        dry = rebuild.run(db, admin, urls, "dry", ["SL-99999"], fetcher=fetcher)
        print("DRY:", json.dumps({k: v for k, v in dry.items() if k != "tabs"}, ensure_ascii=False)[:900])
        rep = rebuild.run(db, admin, urls, "apply", fetcher=fetcher)
        print(f"APPLY in {time.time() - t0:.0f}s:", rep["counts"], "removed cold:", rep["remove_existing_cold"],
              "tests:", rep["remove_tests"])
        again = rebuild.run(db, admin, urls, "apply", fetcher=fetcher)
        print("SECOND APPLY:", again["counts"], "will_create:", again["will_create"])
    q = lambda sql, *a: db.scalar(sql, a)  # noqa: E731
    print("records:", q("SELECT COUNT(*) FROM opportunities WHERE kind='seller'"), "sellers,",
          q("SELECT COUNT(*) FROM opportunities WHERE kind='buyer'"), "buyers")
    print("temperature:", db.all("SELECT kind, lead_temperature t, COUNT(*) n FROM opportunities GROUP BY 1,2"))
    print("with lead time:", q("SELECT COUNT(*) FROM opportunities WHERE lead_created_at IS NOT NULL"),
          "contacted:", q("SELECT COUNT(*) FROM opportunities WHERE first_contact_at IS NOT NULL"),
          "with remark:", q("SELECT COUNT(*) FROM opportunities WHERE team_remark IS NOT NULL AND team_remark <> ''"))
    print("exclusions:", q("SELECT COUNT(*) FROM lead_exclusions"), "· TEST left:",
          q("SELECT COUNT(*) FROM contacts WHERE full_name LIKE 'TEST%'"), "· existing cold lead kept as cold:",
          db.all("SELECT o.lead_temperature FROM opportunities o JOIN contacts c ON c.id = o.contact_id WHERE "
                 "c.full_name = 'Existing Cold'"))
    print("owners:", db.all("SELECT o.kind, u.full_name, COUNT(*) n FROM opportunities o JOIN users u ON "
                            "u.id = o.primary_owner_id GROUP BY 1,2"))
    print("fk check:", db.execute("PRAGMA foreign_key_check").fetchall()[:3])
    print("ref codes unique:", q("SELECT COUNT(DISTINCT ref_code) FROM opportunities") == q("SELECT COUNT(*) FROM opportunities"))


if __name__ == "__main__":
    main()
