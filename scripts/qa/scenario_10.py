"""QA: 10 dummy people through every module, in all the ways the team really works — on a COPY of a database.

  python -I scripts/qa/scenario_10.py <copy-of.db>

Each dummy goes through: new record (with typos to fix: letters in numbers, a wrong mobile), duplicate warning and
linking, editing details, logging calls, follow-ups, hot/warm/cold, moving sides, sending forms, the public forms
(saving while typing, submitting twice), inquiries (job / academy), screenshots, tasks, search and lists. Every page
answer is checked for crashes; every record count is checked for duplicates. At the end all dummy data is removed.
"""
import io
import re
import sys
from datetime import timedelta
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

PROBLEMS, CHECKS = [], [0]


def check(ok, what):
    CHECKS[0] += 1
    if not ok:
        PROBLEMS.append(what)
        print("  FAIL", what)


def main():
    dbfile = sys.argv[1]
    from teamos import create_app
    from teamos.db import open_standalone
    from teamos.timeutil import now_utc
    url = "sqlite:///" + dbfile.replace("\\", "/")
    app = create_app({"DATABASE_URL": url, "SECRET_KEY": "qa", "TESTING": True, "MFA_REQUIRED_ROLES": "",
                      "QUIET_HOURS": "", "FILES_KEY": "Wz3Qt1tVYv0d6m1M4mQe5Gk0b8Yq3c2y7r9s1u4x6zA="})
    db = open_standalone(url)
    before = {t: db.scalar(f"SELECT COUNT(*) FROM {t}") for t in ("opportunities", "contacts", "inquiries", "work_proofs")}

    def client(email):
        c = app.test_client()
        r = c.post("/login", data={"email": email, "password": "Demo@123"})
        check(r.status_code == 302, f"login {email}")
        c.tok = lambda html: (re.search(r'name="csrf_token" value="([^"]+)"', html) or [None, ""])[1]
        return c

    def post(c, path, data, page=None, files=False):
        html = c.get(page or path).data.decode()
        data = dict(data, csrf_token=c.tok(html))
        r = c.post(path, data=data, content_type="multipart/form-data" if files else None)
        check(r.status_code in (200, 302), f"POST {path} -> {r.status_code}")
        if r.status_code == 200:
            check("Traceback" not in r.data.decode(), f"POST {path} crashed")
        return r

    founder = client("founder@demo.salonshastra.test")
    shaheen = client("shaheen.salonshastra@gmail.com")
    harika = client("harika.salonshastra@gmail.com")
    due = (now_utc() + timedelta(days=1)).strftime("%Y-%m-%dT11:00")
    made = []
    for i in range(10):
        n = f"{i + 1:02d}"
        phone = f"93011000{n}"
        c = [shaheen, harika, founder][i % 3]
        kind = "seller" if i % 2 == 0 else "buyer"
        print(f"QA dummy {n} ({kind})")
        if kind == "seller":
            # 1. a mistake first (bad mobile): nothing saved, everything typed comes back
            r = post(c, "/sellers/new", {"full_name": f"QA Dummy {n}", "phone": "12345", "salon_name": f"QA Salon {n}",
                                         "city": "Hyderabad", "locality": "Kondapur", "source": "manual_call",
                                         "consent_contact": "1", "sale_purpose": "sell_entire",
                                         "asking_price": "2500000", "chairs": "6"})
            html = r.data.decode()
            check(f"QA Salon {n}" in html and "2500000" in html, f"dummy {n}: typed values kept after a mistake")
            # 2. fixed and saved
            r = post(c, "/sellers/new", {"full_name": f"QA Dummy {n}", "phone": phone, "salon_name": f"QA Salon {n}",
                                         "city": "Hyderabad", "locality": "Kondapur", "source": "manual_call",
                                         "consent_contact": "1", "sale_purpose": "sell_entire" if i % 4 == 0 else "seek_help",
                                         "asking_price": "2500000"})
        else:
            r = post(c, "/buyers/new", {"full_name": f"QA Dummy {n}", "phone": phone, "city": "Hyderabad",
                                        "locality": "Madhapur", "source": "meta_lead", "consent_contact": "1",
                                        "need_type": "new_salon" if i % 4 == 1 else "acquire_operating",
                                        "all_in_max": "3000000"})
        opp = db.one("SELECT o.* FROM opportunities o JOIN contacts c ON c.id = o.contact_id WHERE c.phone_normalized = ?",
                     ("+91" + phone,))
        check(opp is not None, f"dummy {n}: record created")
        if not opp:
            continue
        made.append(opp["id"])
        base = "sellers" if kind == "seller" else "buyers"
        # 3. the same person again: duplicate warning, linking, no second record
        r = post(c, f"/{base}/new", {"full_name": f"QA Dummy {n} again", "phone": phone, "salon_name": f"QA Salon {n}",
                                     "city": "Hyderabad", "locality": "Kondapur", "source": "manual_call",
                                     "consent_contact": "1"})
        check("duplicate" in r.data.decode().lower(), f"dummy {n}: duplicate warning shown")
        same = db.scalar("SELECT COUNT(*) FROM opportunities o JOIN contacts c ON c.id = o.contact_id WHERE "
                         "c.phone_normalized = ? AND o.kind = ?", ("+91" + phone, kind))
        check(same == 1, f"dummy {n}: still one record after the duplicate warning ({same})")
        # 4. profile pages, every tab
        for tab in ("overview", "intake", "requirements", "qualification", "matches", "history", "pilot", "investor"):
            r = c.get(f"/{base}/{opp['id']}?tab={tab}")
            check(r.status_code == 200 and "Traceback" not in r.data.decode(), f"dummy {n}: tab {tab}")
        # 5. edit details, hot/warm/cold, remark
        post(c, f"/records/{opp['id']}/basics", {"full_name": f"QA Dummy {n}", "phone": phone, "salon_name": f"QA Salon {n}",
                                                 "city": "Hyderabad", "locality": "Kondapur",
                                                 "lead_temperature": ["hot", "warm", "cold", "normal"][i % 4],
                                                 "team_remark": f"QA remark {n}"}, page=f"/{base}/{opp['id']}")
        # 6. calls with each outcome + follow-up
        for outcome in ("no_answer", "connected"):
            post(c, f"/{base}/{opp['id']}/call", {"outcome": outcome, "notes": f"QA call {outcome}",
                                                  "next_title": "QA follow up", "next_due": due}, page=f"/{base}/{opp['id']}")
        # 7. send each form of this side (opens WhatsApp) and one of the other side (asks to move)
        for form in [k for k, s, _l, _w in __import__("teamos.domain.switch_side", fromlist=["x"]).FORMS]:
            r = post(c, f"/records/{opp['id']}/send-form", {"form": form}, page=f"/{base}/{opp['id']}")
            loc = r.headers.get("Location", "")
            check(loc.startswith("https://wa.me/") or "/move?" in loc or r.status_code == 200, f"dummy {n}: send {form} -> {loc[:40]}")
        made_after = db.scalar("SELECT COUNT(*) FROM opportunities o JOIN contacts c ON c.id = o.contact_id WHERE "
                               "c.phone_normalized = ?", ("+91" + phone,))
        check(made_after == 1, f"dummy {n}: sending forms made no extra records ({made_after})")
    # 8. move one buyer to salon owners, with history
    if len(made) > 1:
        bid = made[1]
        r = post(harika, f"/records/{bid}/move", {"to": "seller", "salon_name": "QA Moved Salon", "city": "Hyderabad",
                                                  "locality": "KPHB", "lead_category": ["sell"]}, page=f"/records/{bid}/move?to=seller")
        check(db.scalar("SELECT stage FROM opportunities WHERE id = ?", (bid,)) == "moved", "move: old record closed as moved")
        new = db.scalar("SELECT id FROM opportunities WHERE source_detail = ? ORDER BY id DESC", (f"Moved from BY-{bid:05d}",))
        if new:
            made.append(new)
            check(db.scalar("SELECT COUNT(*) FROM activities WHERE entity_id = ? AND kind = 'call'", (new,)) == 2,
                  "move: both calls came along")
    # 9. public forms: saving while typing, twice, then submitting twice -> one record each
    pub = app.test_client()
    for path, nk, pk, kind, ph in (("/f/investor", "full_name", "phone", "buyer", "9301100091"),
                                  ("/f/pilot", "owner_name", "contact_number", "seller", "9301100092")):
        tok = re.search(r'name="form_token" value="([^"]+)"', pub.get(path).data.decode()).group(1)
        base = {"form_token": tok, nk: "QA Public", pk: ph, "consent_contact": "1"}
        for _ in range(2):
            pub.post(path + "/save", data=base)
        pub.post(path, data=base)
        pub.post(path, data=base)
        cnt = db.scalar("SELECT COUNT(*) FROM opportunities o JOIN contacts c ON c.id = o.contact_id WHERE "
                        "c.phone_normalized = ? AND o.kind = ?", ("+91" + ph, kind))
        check(cnt == 1, f"public {path}: one record after saves and two submits ({cnt})")
        made += [r["id"] for r in db.all("SELECT o.id FROM opportunities o JOIN contacts c ON c.id = o.contact_id WHERE "
                                          "c.phone_normalized = ?", ("+91" + ph,))]
    # 10. inquiries: job and academy, with a duplicate number
    for k, extra in (("job", {"roles": ["Hair stylist (women)"], "experience": "3–5 years", "expected_salary": "22000"}),
                     ("academy", {"courses": ["Makeup artistry"], "fee_approach": "Lowest fee possible"})):
        post(shaheen, "/others/new", {"kind": k, "full_name": f"QA10 {k}", "phone": "9301100095" if k == "job" else "9301100096",
                                      "hometown": "Nellore", **extra}, page=f"/others/new?kind={k}")
        r = post(shaheen, "/others/new", {"kind": k, "full_name": f"QA10 {k} again",
                                          "phone": "9301100095" if k == "job" else "9301100096"}, page=f"/others/new?kind={k}")
        check("already has" in r.data.decode() and f"QA10 {k} again" in r.data.decode(), f"inquiry {k}: duplicate caught, typing kept")
    check(db.scalar("SELECT COUNT(*) FROM inquiries WHERE full_name LIKE 'QA10 %'") == 2, "inquiries: exactly two")
    # 11. screenshots
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", (300, 600), (220, 230, 255)).save(b, "PNG")
    html = harika.get("/work/").data.decode()
    r = harika.post("/work/", data={"csrf_token": harika.tok(html), "kind": "calls",
                                    "files": [(io.BytesIO(b.getvalue()), "a.png")]}, content_type="multipart/form-data")
    check(r.status_code == 302, "screenshot upload")
    # 12. lists, search, team pages
    for c, path in ((founder, "/sellers/"), (founder, "/buyers/?q=QA+Dummy"), (founder, "/search?q=QA+Dummy"),
                    (founder, "/work/team"), (founder, "/others/?kind=job"), (harika, "/tasks/"), (shaheen, "/")):
        r = c.get(path)
        check(r.status_code == 200 and "Traceback" not in r.data.decode(), f"page {path}")
    # 13. no duplicates anywhere for the dummies
    dup = db.scalar("SELECT COUNT(*) FROM (SELECT c.phone_normalized, o.kind FROM opportunities o JOIN contacts c ON "
                    "c.id = o.contact_id WHERE o.stage <> 'moved' AND c.phone_normalized LIKE '+9193011%' GROUP BY 1, 2 "
                    "HAVING COUNT(*) > 1) x")
    check(dup == 0, f"no duplicate dummy records ({dup})")
    # 14. remove every dummy record (as an admin would)
    from teamos.domain import purge
    admin = db.one("SELECT * FROM users WHERE email = 'founder@demo.salonshastra.test'")
    ids = [r["id"] for r in db.all("SELECT o.id FROM opportunities o JOIN contacts c ON c.id = o.contact_id WHERE "
                                   "c.phone_normalized LIKE '+9193011%'")]
    with app.test_request_context():
        purge.delete_records(db, admin, ids, "QA dummy data", remember=False)
    db.execute("DELETE FROM inquiries WHERE full_name LIKE 'QA10 %'")
    for p in db.all("SELECT * FROM work_proofs WHERE note IS NULL AND day >= '2026-11-27'"):
        db.execute("DELETE FROM document_blobs WHERE stored_name = ?", (p["stored_name"],))
        db.execute("DELETE FROM work_proofs WHERE id = ?", (p["id"],))
    after = {t: db.scalar(f"SELECT COUNT(*) FROM {t}") for t in before}
    check(after["opportunities"] == before["opportunities"] and after["inquiries"] == before["inquiries"],
          f"dummy data removed: {before} -> {after}")
    print(f"\n{CHECKS[0]} checks, {len(PROBLEMS)} problems")
    for p in PROBLEMS:
        print(" -", p)


if __name__ == "__main__":
    main()
