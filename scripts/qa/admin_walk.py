"""QA: an admin's day, on a COPY of a database.

  python -I scripts/qa/admin_walk.py <copy-of.db>

Adds, renames, re-roles, resets and disables team members (with every mistake an admin can make), decides who
gets new leads, hands a person's work to colleagues, bulk-changes owners, resolves duplicates, approves and
rejects a listing, and checks that a team member is kept out of every admin page. Each step checks the saved
data and that the page explains itself. Dummy people and records are removed at the end.
"""
import re
import sys
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
    return ok


def main():
    dbfile = sys.argv[1]
    from teamos import create_app
    from teamos.db import open_standalone
    url = "sqlite:///" + dbfile.replace("\\", "/")
    app = create_app({"DATABASE_URL": url, "SECRET_KEY": "qa", "TESTING": True, "MFA_REQUIRED_ROLES": "",
                      "QUIET_HOURS": "", "FILES_KEY": "Wz3Qt1tVYv0d6m1M4mQe5Gk0b8Yq3c2y7r9s1u4x6zA="})
    db = open_standalone(url)
    before = {t: db.scalar(f"SELECT COUNT(*) FROM {t}") for t in ("opportunities", "users")}

    def client(email, pw="Demo@123"):
        c = app.test_client()
        r = c.post("/login", data={"email": email, "password": pw})
        c.ok = r.status_code == 302
        return c

    def tok(html):
        return (re.search(r'name="csrf_token" value="([^"]+)"', html) or [None, ""])[1]

    def post(c, path, data, at):
        html = c.get(at).data.decode()
        r = c.post(path, data=dict(data, csrf_token=tok(html)))
        check(r.status_code in (200, 302), f"POST {path} -> {r.status_code}")
        body = (c.get(r.headers["Location"]) if r.status_code == 302 else r).data.decode()
        check("Traceback" not in body, f"POST {path}: crashed")
        return body

    def flashes(html):
        return " | ".join(m.strip() for m in re.findall(r'class="flash[^"]*"[^>]*>(.*?)<', html, re.S))

    admin = client("founder@demo.salonshastra.test")
    check(admin.ok, "admin signs in")
    U = "/admin/users"
    good = {"full_name": "QA AW Kavya", "new_member_email": "qa.aw.kavya@example.test", "role": "team_member",
            "team": "seller", "password": "Kavya2026Salon"}

    print("1. adding people, with the mistakes admins make")
    for role in ("team_member", "super_admin", "finance"):
        h = post(admin, U, dict(good, role=role, password="short"), U)
        check(db.scalar("SELECT COUNT(*) FROM users WHERE email = ?", (good["new_member_email"],)) == 0
              and "10 characters" in flashes(h), f"weak password refused for {role} ({flashes(h)[:90]})")
        h = post(admin, U, dict(good, role=role, new_member_email="shaheen.salonshastra@gmail.com"), U)
        check("already belongs to" in flashes(h), f"existing email refused for {role} ({flashes(h)[:90]})")
    h = post(admin, U, dict(good, new_member_email=""), U)
    check("valid email" in flashes(h), "missing email refused")
    h = post(admin, U, good, U)
    k = db.one("SELECT * FROM users WHERE email = ?", (good["new_member_email"],))
    if not check(k is not None, f"new team member created ({flashes(h)[:100]})"):
        return
    check(k["must_change_password"] == 1 and k["team"] == "seller", "new person must change password; works on salon owners")
    kc = client(good["new_member_email"], good["password"])
    check(kc.ok, "new person can sign in")
    r = kc.get("/")
    check(r.status_code == 302 and "/me/password" in r.headers.get("Location", ""), "first sign-in goes to choose a password")

    print("2. rename, role, reset password")
    h = post(admin, f"{U}/{k['id']}/name", {"full_name": "  QA AW   Kavya Reddy "}, U)
    check(db.scalar("SELECT full_name FROM users WHERE id = ?", (k["id"],)) == "QA AW Kavya Reddy", f"rename saved tidy ({flashes(h)})")
    h = post(admin, f"{U}/{k['id']}/name", {"full_name": " "}, U)
    check("Write the person" in flashes(h), "empty rename refused")
    me = db.scalar("SELECT id FROM users WHERE email = 'founder@demo.salonshastra.test'")
    old = db.scalar("SELECT full_name FROM users WHERE id = ?", (me,))
    post(admin, f"{U}/{me}/name", {"full_name": old + " X"}, U)
    check(db.scalar("SELECT full_name FROM users WHERE id = ?", (me,)) == old + " X", "admin can rename themselves")
    post(admin, f"{U}/{me}/name", {"full_name": old}, U)
    h = post(admin, f"{U}/{me}/role", {"role": "team_member"}, U)
    check(db.scalar("SELECT role FROM users WHERE id = ?", (me,)) == "super_admin", "admin cannot demote themselves")
    post(admin, f"{U}/{k['id']}/role", {"role": "finance"}, U)
    check(db.scalar("SELECT role FROM users WHERE id = ?", (k["id"],)) == "finance", "role changed to Finance")
    r = kc.get("/sellers/")
    check(r.status_code in (302, 403), f"finance person no longer opens salon owners ({r.status_code})")
    post(admin, f"{U}/{k['id']}/role", {"role": "team_member"}, U)
    h = post(admin, f"{U}/{k['id']}/reset-password", {}, U)
    check(db.scalar("SELECT must_change_password FROM users WHERE id = ?", (k["id"],)) == 1, "reset sets a temporary password")
    check(not client(good["new_member_email"], good["password"]).ok, "old password stops working after reset")

    print("3. who gets new leads, and a new lead goes to them in turn")
    sh = db.scalar("SELECT id FROM users WHERE email LIKE 'shaheen%'")
    ha = db.scalar("SELECT id FROM users WHERE email LIKE 'harika%'")
    h = post(admin, "/records/split", {"seller_ids": [str(sh), str(k["id"])], "buyer_ids": [str(ha)]}, "/records/manage")
    check("Saved" in flashes(h), f"lead split saved ({flashes(h)[:80]})")
    owners = []
    for i in range(4):
        db.execute("DELETE FROM public_form_hits")      # one test machine = one address: skip the spam limit
        tc = app.test_client()
        page = tc.get("/f/pilot").data.decode()
        t = re.search(r'name="form_token" value="([^"]+)"', page)
        if not t:
            break
        tc.post("/f/pilot", data={"form_token": t.group(1), "owner_name": f"QA AW Owner {i}",
                                  "contact_number": f"930130000{i}", "salon_name": f"QA AW Salon {i}", "city": "Hyderabad",
                                  "consent_contact": "1"})
        owners.append(db.scalar("SELECT o.primary_owner_id FROM opportunities o JOIN contacts c ON c.id = o.contact_id "
                                "WHERE c.phone_normalized = ? AND o.kind = 'seller'", (f"+91930130000{i}",)))
    check(len(owners) == 4 and set(owners) == {sh, k["id"]}, f"new salon owners shared in turn ({owners})")

    print("4. disabling someone who still has work, then handing it over")
    h = post(admin, f"{U}/{k['id']}/active", {}, U)
    check(db.scalar("SELECT is_active FROM users WHERE id = ?", (k["id"],)) == 0, "disabled")
    check("still hold" in flashes(h) and "Hand over" in flashes(h), f"admin told about their open work ({flashes(h)[:140]})")
    r = kc.get("/")
    check(r.status_code == 302 and "/login" in r.headers.get("Location", ""), "a disabled person is signed out at once")
    h = admin.get("/team").data.decode()
    held = db.scalar("SELECT COUNT(*) FROM opportunities WHERE primary_owner_id = ? AND status <> 'closed'", (k["id"],))
    h = post(admin, "/team/hand-over", {"from_id": str(k["id"]), "to_ids": [str(sh)], "kind": "all", "go": "1"}, "/team")
    left = db.scalar("SELECT COUNT(*) FROM opportunities WHERE primary_owner_id = ? AND status <> 'closed'", (k["id"],))
    lt = db.scalar("SELECT COUNT(*) FROM tasks WHERE assignee_id = ? AND status = 'open'", (k["id"],))
    check(held > 0 and left == 0 and lt == 0, f"hand-over moved {held} records and their tasks ({left} / {lt} left) {flashes(h)[:80]}")
    nxt = []
    for i in range(4, 6):
        db.execute("DELETE FROM public_form_hits")      # one test machine = one address: skip the spam limit
        tc = app.test_client()
        t = re.search(r'name="form_token" value="([^"]+)"', tc.get("/f/pilot").data.decode())
        tc.post("/f/pilot", data={"form_token": t.group(1), "owner_name": f"QA AW Owner {i}", "contact_number": f"930130000{i}",
                                  "salon_name": f"QA AW Salon {i}", "city": "Hyderabad", "consent_contact": "1"})
        nxt.append(db.scalar("SELECT o.primary_owner_id FROM opportunities o JOIN contacts c ON c.id = o.contact_id "
                             "WHERE c.phone_normalized = ?", (f"+91930130000{i}",)))
    check(k["id"] not in nxt and all(nxt), f"a disabled person gets no new leads ({nxt})")

    print("5. bulk owner change moves tasks too")
    ids = [r["id"] for r in db.all("SELECT o.id FROM opportunities o JOIN contacts c ON c.id = o.contact_id WHERE "
                                   "c.phone_normalized LIKE '+9193013%' AND o.kind = 'seller'")]
    h = post(admin, "/records/bulk", {"kind": "seller", "action": "assign", "owner_id": str(ha), "reason": "QA",
                                      "ids": [str(i) for i in ids]}, "/sellers/")
    wrong = db.scalar(f"SELECT COUNT(*) FROM tasks WHERE status = 'open' AND entity_type = 'opportunity' AND entity_id IN "
                      f"({','.join(str(i) for i in ids)}) AND assignee_id <> ?", (ha,))
    check(wrong == 0, f"bulk owner change: every open task followed ({wrong} left behind) {flashes(h)[:60]}")
    h = post(admin, "/records/bulk", {"kind": "seller", "action": "delete", "confirm": "delete it",
                                      "ids": [str(ids[0])]}, "/sellers/")
    check(db.scalar("SELECT COUNT(*) FROM opportunities WHERE id = ?", (ids[0],)) == 1 and "Type DELETE" in flashes(h),
          "bulk delete needs DELETE typed")

    print("6. duplicate review")
    d = db.one("SELECT * FROM duplicate_reviews WHERE status = 'open' LIMIT 1")
    if d:
        h = post(admin, f"/admin/duplicates/{d['id']}", {"status": "distinct"}, "/admin/duplicates")
        check(db.scalar("SELECT status FROM duplicate_reviews WHERE id = ?", (d["id"],)) == "open", "resolving needs a reason")
        post(admin, f"/admin/duplicates/{d['id']}", {"status": "distinct", "rationale": "different salons"}, "/admin/duplicates")
        check(db.scalar("SELECT status FROM duplicate_reviews WHERE id = ?", (d["id"],)) == "distinct", "duplicate resolved")
        db.execute("UPDATE duplicate_reviews SET status = 'open', rationale = NULL, resolved_by = NULL, resolved_at = NULL "
                   "WHERE id = ?", (d["id"],))

    print("7. listing approval")
    oid = ids[1] if len(ids) > 1 else None
    if oid:
        h = post(admin, f"/sellers/{oid}/decision", {"decision": "rejected"}, f"/sellers/{oid}?tab=approval")
        check("reason" in flashes(h).lower(), f"rejecting needs a reason ({flashes(h)[:80]})")
        h = post(admin, f"/sellers/{oid}/decision", {"decision": "approved"}, f"/sellers/{oid}?tab=approval")
        check(db.scalar("SELECT approval_state FROM seller_listings WHERE opportunity_id = ?", (oid,)) != "approved"
              and flashes(h), f"approving an empty listing is blocked with the reason ({flashes(h)[:100]})")
        h = post(admin, f"/sellers/{oid}/decision", {"decision": "bogus"}, f"/sellers/{oid}?tab=approval")
        check(flashes(h), "unknown decision explained, not a crash")

    print("8. a team member is kept out of admin")
    shc = client("shaheen.salonshastra@gmail.com")
    for p in ("/admin/users", "/admin/finance", "/records/manage", "/admin/jobs", "/admin/integrations",
              "/admin/templates", "/admin/database-move"):
        r = shc.get(p)
        check(r.status_code in (302, 403, 404), f"team member blocked from {p} ({r.status_code})")
    for p, data in ((f"{U}", good), (f"{U}/{sh}/role", {"role": "super_admin"}), (f"{U}/{ha}/active", {}),
                    (f"{U}/{ha}/name", {"full_name": "x"}), ("/records/split", {"seller_ids": [str(sh)]})):
        h = shc.get("/").data.decode()
        r = shc.post(p, data=dict(data, csrf_token=tok(h)))
        check(r.status_code in (302, 403), f"team member cannot POST {p} ({r.status_code})")
    check(db.scalar("SELECT role FROM users WHERE id = ?", (sh,)) == "team_member" and
          db.scalar("SELECT is_active FROM users WHERE id = ?", (ha,)) == 1, "nothing changed by the team member")

    print("9. every admin page opens")
    for p in ("/tools", "/team", "/desk", "/reports/", "/admin/users", "/admin/duplicates", "/admin/jobs", "/admin/audit",
              "/records/manage", "/records/review", "/automations", "/admin/finance", "/work/team", "/matches/",
              "/deals/", "/imports/", "/admin/integrations", "/admin/templates", "/admin/outbox", "/reports/sources",
              "/reports/learning", "/team/availability", "/team/activity", "/team/worklog", "/dictionary"):
        r = admin.get(p)
        check(r.status_code == 200 and "Traceback" not in r.data.decode(), f"{p} -> {r.status_code}")
    team = admin.get("/team").data.decode()
    check(len(team) < 250_000, f"Team page size {len(team) // 1000} KB")

    # clean up
    from teamos.domain import purge
    a = db.one("SELECT * FROM users WHERE id = ?", (me,))
    ids = [r["id"] for r in db.all("SELECT o.id FROM opportunities o JOIN contacts c ON c.id = o.contact_id WHERE "
                                   "c.phone_normalized LIKE '+9193013%'")]
    with app.test_request_context():
        purge.delete_records(db, a, ids, "QA dummy data", remember=False)
    print(f"\n{CHECKS[0]} checks, {len(PROBLEMS)} problems")
    for p in PROBLEMS:
        print(" -", p)


if __name__ == "__main__":
    main()
