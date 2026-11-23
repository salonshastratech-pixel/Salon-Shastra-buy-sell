"""QA: forged-request probe. Each role POSTs straight to privileged endpoints (with a valid CSRF token of its own
session, as a malicious insider could). Runs on a COPY of the demo database. Expects denial and no state change."""
import os
import re
import sqlite3
import sys

sys.stdout.reconfigure(encoding="utf-8")
root, copy = sys.argv[1], sys.argv[2]
sys.path.insert(0, root)
os.chdir(root)
src = sqlite3.connect("instance/teamos-demo.db")
dst = sqlite3.connect(copy)
src.backup(dst)
dst.close()

from teamos import create_app  # noqa: E402

app = create_app({"DATABASE_URL": "sqlite:///" + copy, "SECRET_KEY": "qa", "TESTING": True, "MFA_REQUIRED_ROLES": ""})
db = sqlite3.connect(copy)
q = lambda sql, *a: db.execute(sql, a).fetchone()  # noqa: E731

intro = q("SELECT id FROM introductions LIMIT 1")
# M7: a fresh draft batch drafted by Harika, to probe approval rights
db.execute("INSERT INTO batches (name, template_key, audience, status, created_by, created_at, updated_at) VALUES "
           "('QA probe', 'inactive_followup', '{\"key\": \"buyers_active\", \"variables\": {}}', 'draft', "
           "(SELECT id FROM users WHERE email LIKE 'harika@%'), '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')")
db.commit()
qa_batch = q("SELECT MAX(id) FROM batches")[0]
seller_doc = q("SELECT d.id FROM documents d JOIN opportunities o ON o.id = d.opportunity_id WHERE o.kind='seller'")
buyer_doc = q("SELECT d.id FROM documents d JOIN opportunities o ON o.id = d.opportunity_id WHERE o.kind='buyer'")
PROBES = [  # (label, method, url, data, privileged roles that MAY do it, state check sql)
    ("approve listing", "POST", "/sellers/2/decision", {"decision": "approved"}, {"founder"},
     "SELECT approval_state FROM seller_listings WHERE opportunity_id = 2"),
    ("record funding review", "POST", "/buyers/10/funding", {"status": "verified", "note": "forged"}, {"founder", "lead"},
     "SELECT funding_status FROM buyer_mandates WHERE opportunity_id = 10"),
    ("approve match to approach", "POST", "/matches/11/stage", {"to": "approved_to_approach"}, {"founder", "lead"},
     "SELECT stage FROM match_candidates WHERE id = 11"),
    ("close deal as won", "POST", "/deals/1/stage", {"to": "closed_won"}, {"founder"},
     "SELECT stage FROM deals WHERE id = 1"),
    ("record fee agreement", "POST", "/admin/finance/agreement",
     {"deal_id": "1", "payer": "buyer", "fee_type": "other", "basis": "fixed", "fixed_amount": "1",
      "earning_condition": "manual"}, {"founder", "accounts"}, "SELECT COUNT(*) FROM fee_agreements"),
    ("create admin user", "POST", "/admin/users",
     {"email": "evil@x.test", "full_name": "Evil", "role": "super_admin", "password": "Abcdefghij1!"}, {"founder"},
     "SELECT COUNT(*) FROM users WHERE email = 'evil@x.test'"),
    ("grant capability", "POST", "/admin/grants",
     {"user_id": "2", "capability": "finance.view", "reason": "forged"}, {"founder"},
     "SELECT COUNT(*) FROM user_grants"),
    ("approve teaser", "POST", "/deals/teasers/1/approve", {}, {"founder"}, "SELECT status FROM teasers WHERE id = 1"),
    # Milestone 7
    ("approve message batch", "POST", f"/inbox/batches/{qa_batch}", {"action": "approve"}, {"founder", "lead"},
     f"SELECT status FROM batches WHERE id = {qa_batch}"),
    ("send WhatsApp reply", "POST", "/inbox/1/send", {"mode": "text", "text": "forged"},
     {"founder", "lead", "shaheen", "harika"}, "SELECT COUNT(*) FROM messages"),
    ("reassign conversation", "POST", "/inbox/1/action", {"action": "assign", "user_id": "1"},
     {"founder", "lead", "shaheen", "harika"}, "SELECT assigned_to FROM conversations WHERE id = 1"),
    ("edit message template", "POST", "/admin/templates",
     {"key": "inactive_followup", "name": "x", "category": "marketing", "status": "approved_local",
      "body": "Buy now! Reply STOP to opt out."}, {"founder"},
     "SELECT body || category FROM message_templates WHERE key = 'inactive_followup'"),
    # Milestone 7.2 / 7.3
    ("reset someone's password", "POST", "/admin/users/1/reset-password", {}, {"founder"},
     "SELECT password_hash FROM users WHERE id = 1"),
    ("save integration settings", "POST", "/admin/integrations",
     {"action": "settings", "WHATSAPP_ACCESS_TOKEN": "forged", "PUBLIC_BASE_URL": "https://evil.example"}, {"founder"},
     "SELECT COUNT(*) FROM integration_settings"),
    ("switch WhatsApp live", "POST", "/admin/integrations", {"action": "go_live", "confirm": "GO LIVE"}, {"founder"},
     "SELECT COUNT(*) FROM integration_settings"),
    ("check templates with Meta", "POST", "/admin/templates", {"action": "meta_check"}, {"founder"},
     "SELECT COUNT(*) FROM audit_events WHERE action = 'templates.meta_cross_check'"),
]
if intro:
    PROBES.append(("approve introduction", "POST", f"/deals/introductions/{intro[0]}", {"decision": "approved"},
                   {"founder"}, f"SELECT status FROM introductions WHERE id = {intro[0]}"))
GETS = [("open Admin Finance", "/admin/finance", {"founder", "accounts"}),
        ("open audit log", "/admin/audit", {"founder"}),
        ("open user admin", "/admin/users", {"founder"}),
        ("open WhatsApp inbox", "/inbox/", {"founder", "lead", "shaheen", "harika"}),
        ("open integrations", "/admin/integrations", {"founder"}),
        ("open e-mail outbox", "/admin/outbox", {"founder"})]
if seller_doc:
    GETS.append(("download seller document", f"/forms/documents/{seller_doc[0]}", {"founder", "shaheen"}))
if buyer_doc:
    GETS.append(("download buyer funding proof", f"/forms/documents/{buyer_doc[0]}",
                 {"founder", "lead", "shaheen", "harika"}))

# Which real account plays each role in this copy (staff may have real logins); give them a QA-only password here.
QA_PW = "Qa-Probe-Password-1"
EMAILS = {
    "shaheen": q("SELECT email FROM users WHERE role = 'team_member' AND team = 'seller' AND is_active = 1 ORDER BY id")[0],
    "harika": q("SELECT email FROM users WHERE role = 'team_member' AND team = 'buyer' AND is_active = 1 ORDER BY id")[0],
    "lead": q("SELECT email FROM users WHERE role = 'team_lead' AND is_active = 1 ORDER BY id")[0],
    "accounts": q("SELECT email FROM users WHERE role = 'finance' AND is_active = 1 ORDER BY id")[0],
}
from teamos.security import hash_password  # noqa: E402
db.executemany("UPDATE users SET password_hash = ?, must_change_password = 0 WHERE email = ?",
               [(hash_password(QA_PW), e) for e in EMAILS.values()])
db.commit()

problems = 0
for role in ("shaheen", "harika", "lead", "accounts"):
    c = app.test_client()
    c.post("/login", data={"email": EMAILS[role], "password": QA_PW})
    page = c.get("/").data.decode()
    tok = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
    for label, method, url, data, allowed, check in PROBES:
        if role in allowed:
            continue
        before = q(check)
        r = c.post(url, data={**data, "csrf_token": tok})
        after = q(check)
        ok = before == after
        problems += 0 if ok else 1
        print(f"{'ok  ' if ok else 'FAIL'} {role:8} {label:28} -> HTTP {r.status_code}, state {'unchanged' if ok else f'{before} -> {after}'}")
    for label, url, allowed in GETS:
        if role in allowed:
            continue
        r = c.get(url)
        ok = r.status_code in (302, 403, 404)
        problems += 0 if ok else 1
        print(f"{'ok  ' if ok else 'FAIL'} {role:8} {label:28} -> HTTP {r.status_code}")
anon = app.test_client()
for url in ("/", "/sellers/1", "/buyers/9", "/matches/", "/deals/1", "/admin/finance", "/forms/"):
    r = anon.get(url)
    ok = r.status_code == 302 and "/login" in r.headers.get("Location", "")
    problems += 0 if ok else 1
    print(f"{'ok  ' if ok else 'FAIL'} anonymous {url:22} -> HTTP {r.status_code}")
r = app.test_client().post("/sellers/1/note", data={"body": "csrf-less"})
print(f"{'ok  ' if r.status_code in (302, 400) else 'FAIL'} anonymous POST without token -> HTTP {r.status_code}")
print("PROBLEMS:", problems)
