"""QA: build a test database with 20 salon owners and 20 investors in every realistic situation, on a COPY of the
demo database (never the live one). Every record is created through the same doors real leads use:

  salon owners — 6 full open forms, 3 half-filled then submitted, 3 abandoned half-way, 4 added by the team and the
                 form completed by the team on a call, 4 from the Meta ad sheet not yet called
  investors    — 6 full open forms (strong and weak), 3 half-filled then submitted, 3 abandoned, 4 added by the team,
                 4 from ads not yet called

Then the team works them: calls with every outcome, follow-ups (some already overdue), a stage move or two.

  python -I scripts/qa/seed_qa.py <demo.db> <out.db>
"""
import random
import re
import shutil
import sys
from datetime import timedelta
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from test_investor import STRONG  # noqa: E402
from test_pilot import FULL  # noqa: E402

random.seed(7)
PLACES = ["Kondapur, Hyderabad", "Madhapur, Hyderabad", "Kukatpally, Hyderabad", "Gachibowli, Hyderabad",
          "Banjara Hills, Hyderabad", "Indiranagar, Bengaluru", "Koramangala, Bengaluru", "HSR Layout, Bengaluru",
          "Anna Nagar, Chennai", "T Nagar, Chennai", "Velachery, Chennai", "Kakkanad, Kochi"]
OWNERS = ["Kavya Reddy", "Sneha Rao", "Priya Nair", "Lakshmi Iyer", "Ramesh Goud", "Anitha Kumar", "Divya Menon",
          "Suresh Babu", "Meena Pillai", "Farah Khan", "Rekha Shetty", "Naveen Varma", "Pooja Hegde", "Arif Shaikh",
          "Swathi Reddy", "Bhavana K", "Harish Chandra", "Ishita Das", "Gowtham S", "Nandini Rao"]
SALONS = ["Glow Studio", "Bliss Unisex", "Mirror Mirror", "Tresses", "Shear Magic", "Velvet Touch", "Nail Nook",
          "The Barber Room", "Aura Spa & Salon", "Curl Up", "Style Lounge", "Rouge", "Groom Gents", "Lush Locks",
          "Silk Route Salon", "Polish Bar", "Fade Factory", "Blossom", "Studio 9", "Mane Street"]
INVESTORS = ["Arjun Rao", "Kiran Kumar", "Sameer Ali", "Deepa Raj", "Vivek Sharma", "Ritu Agarwal", "Mohan Das",
             "Asha Thomas", "Rahul Jain", "Sunita Reddy", "Prakash N", "Zoya Mirza", "Venkat Raman", "Shalini G",
             "Ajay Menon", "Kavitha S", "Imran Baig", "Lavanya P", "Tarun Bose", "Geetha V"]
WEAK = {"goal": "Start a new salon", "total_budget": "8L", "money_ready": "Need a loan", "runway": "No",
        "days_per_week": "Weekends only", "keep_job": "Yes — I will keep my job", "salon_experience": "None"}


def token(page):
    return re.search(r'name="form_token" value="([^"]+)"', page).group(1)


def main():
    src, out = sys.argv[1], sys.argv[2]
    shutil.copy(src, out)
    from teamos import create_app
    app = create_app({"DATABASE_URL": f"sqlite:///{out}", "TESTING": True, "SECRET_KEY": "qa-seed", "COOKIE_SECURE": False,
                      "MFA_REQUIRED_ROLES": "", "QUIET_HOURS": "", "UPLOAD_DIR": str(Path(out).parent / "uploads-qa")})
    from teamos.db import open_standalone
    from teamos.domain import buyers as B
    from teamos.domain import investor as INV
    from teamos.domain import pilot as P
    from teamos.domain import sellers as S
    from teamos.timeutil import now_utc, to_iso
    db = open_standalone(f"sqlite:///{out}")
    db.execute("UPDATE users SET must_change_password = 0, mfa_enabled_at = NULL")
    db.commit() if hasattr(db, "commit") else None
    team = db.all("SELECT * FROM users WHERE is_active = 1 AND role IN ('team_member', 'team_lead') ORDER BY id")
    admin = db.one("SELECT * FROM users WHERE role = 'super_admin' ORDER BY id LIMIT 1")
    assert team, "the demo DB needs team members"
    made = {"seller": [], "buyer": []}
    phone = iter(range(9300000100, 9300009999))

    ips = iter(range(1, 250))

    def pub_form(kind, answers, partial=False, abandon=False):
        c = app.test_client()
        c.environ_base["REMOTE_ADDR"] = f"10.9.{next(ips)}.1"   # each customer on their own phone
        page = c.get(f"/f/{kind}").data.decode()
        tok = token(page)
        if partial or abandon:
            j = c.post(f"/f/{kind}/save", data={"form_token": tok, **answers}).get_json()
            assert j and j.get("ok"), (kind, j)
            if abandon:
                return
            r = c.post(f"/f/{kind}", data={"form_token": tok, "draft": j["draft"], **answers, "consent_contact": "1"})
        else:
            r = c.post(f"/f/{kind}", data={"form_token": tok, **answers, "consent_contact": "1"})
        assert r.status_code == 302 and "/thanks" in r.headers["Location"], (kind, r.status_code, r.data[:300])

    # ---- salon owners ----
    for i in range(20):
        name, salon, place = OWNERS[i], SALONS[i], PLACES[i % len(PLACES)]
        ph = str(next(phone))
        full = {**FULL, "owner_name": name, "salon_name": salon, "location": place, "contact_number": ph,
                "email": f"owner{i}@example.com", "chairs": str(random.choice([3, 4, 6, 8, 10])),
                "monthly_revenue": random.choice(["1.2L", "1.8L", "2.5L", "4L", "6L"]),
                "monthly_rent": random.choice(["25k", "40k", "60k", "90k"]),
                "years_running": str(random.choice([1, 2, 4, 7, 12]))}
        if i < 6:
            pub_form("pilot", full)
        elif i < 9:
            pub_form("pilot", {k: full[k] for k in ("owner_name", "contact_number", "salon_name", "location", "chairs",
                                                       "monthly_rent")}, partial=True)
        elif i < 12:
            pub_form("pilot", {k: full[k] for k in ("owner_name", "contact_number", "salon_name", "location")},
                     abandon=True)
        else:
            who = team[i % len(team)]
            src = "meta_lead" if i >= 16 else random.choice(["referral", "manual_call", "whatsapp"])
            city = place.split(", ")[1]
            r = S.create_lead(db, who, {"full_name": name, "phone": ph, "salon_name": salon, "city": city,
                                        "locality": place.split(", ")[0], "source": src, "consent_contact": "1",
                                        "owner_id": str(who["id"])})
            if i < 16:       # the team filled the form on a call
                opp = db.one("SELECT o.*, c.full_name AS contact_name, b.name AS salon_name FROM opportunities o JOIN "
                             "contacts c ON c.id = o.contact_id JOIN salon_businesses b ON b.id = o.business_id "
                             "WHERE o.id = ?", (r["opportunity_id"],))
                P.save_response(db, opp, full, "staff")
            else:            # from the ad sheet, came in hours/days ago and not called yet
                ago = to_iso(now_utc() - timedelta(hours=random.choice([2, 7, 30, 52])))
                db.execute("UPDATE opportunities SET lead_created_at = ? WHERE id = ?", (ago, r["opportunity_id"]))
    # ---- investors ----
    for i in range(20):
        name, place = INVESTORS[i], PLACES[(i + 3) % len(PLACES)]
        ph = str(next(phone))
        base = {**STRONG, "full_name": name, "phone": ph, "email": f"inv{i}@example.com", "city": place.split(", ")[1],
                "areas": place.split(", ")[0]}
        if i % 3 == 1:
            base.update(WEAK)
        if i < 6:
            pub_form("investor", base)
        elif i < 9:
            pub_form("investor", {k: base[k] for k in ("full_name", "phone", "city", "goal", "total_budget")},
                     partial=True)
        elif i < 12:
            pub_form("investor", {k: base[k] for k in ("full_name", "phone", "city")}, abandon=True)
        else:
            who = team[i % len(team)]
            src = "meta_lead" if i >= 16 else "referral"
            r = B.create_lead(db, who, {"full_name": name, "phone": ph, "city": base["city"], "source": src,
                                        "consent_contact": "1", "owner_id": str(who["id"]),
                                        "need_type": random.choice(["acquire_operating", "new_salon", "exploring"]),
                                        "decision_maker": "yes", "all_in_max": random.choice(["15L", "30L", "60L"]),
                                        "purchase_window": random.choice(["90d", "180d", "later"])})
            if i < 16:
                opp = db.one("SELECT * FROM opportunities WHERE id = ?", (r["opportunity_id"],))
                INV.save_response(db, opp, base, "staff") if hasattr(INV, "save_response") else None
            else:
                ago = to_iso(now_utc() - timedelta(hours=random.choice([1, 5, 26, 70])))
                db.execute("UPDATE opportunities SET lead_created_at = ? WHERE id = ?", (ago, r["opportunity_id"]))
    new = db.all("SELECT o.id, o.kind, o.primary_owner_id FROM opportunities o WHERE o.contact_id IN (SELECT id FROM "
                 "contacts WHERE phone_normalized LIKE '+9193000%' OR phone_normalized LIKE '93000%') ORDER BY o.id")
    for r in new:
        made[r["kind"]].append(r)
    print("created:", {k: len(v) for k, v in made.items()})

    # ---- the team works them: calls with every outcome, follow-ups, some overdue ----
    outcomes = ["connected", "no_answer", "callback_requested", "busy", "switched_off", "connected", "not_interested"]
    users = {u["id"]: u for u in db.all("SELECT * FROM users")}
    calls = 0
    for kind, rows in made.items():
        for n, r in enumerate(rows[:14]):
            owner = users.get(r["primary_owner_id"]) or admin
            c = app.test_client()
            if c.post("/login", data={"email": owner["email"], "password": "Demo@123"}).status_code != 302:
                owner = admin                      # a test member with another password: the founder logs it
                c = app.test_client()
                assert c.post("/login", data={"email": owner["email"], "password": "Demo@123"}).status_code == 302
            csrf = db.scalar("SELECT s.csrf_token FROM sessions s JOIN users u ON u.id = s.user_id WHERE u.email = ? "
                             "ORDER BY s.rowid DESC LIMIT 1", (owner["email"],))
            due = (now_utc() + timedelta(days=1)).astimezone().strftime("%Y-%m-%dT10:00")
            resp = c.post(f"/{'sellers' if kind == 'seller' else 'buyers'}/{r['id']}/call",
                          data={"csrf_token": csrf, "outcome": outcomes[n % len(outcomes)], "note": "QA call", "next_title": "Follow up",
                                "next_due": due})
            assert resp.status_code in (302, 303), (kind, r["id"], resp.status_code)
            calls += 1
    # a few follow-ups slipped (overdue) and a few are due later today
    open_tasks = db.all("SELECT id FROM tasks WHERE status = 'open' AND title = 'Follow up' ORDER BY id")
    for t in open_tasks[:6]:
        db.execute("UPDATE tasks SET due_at = ? WHERE id = ?", (to_iso(now_utc() - timedelta(hours=26)), t["id"]))
    for t in open_tasks[6:10]:
        db.execute("UPDATE tasks SET due_at = ? WHERE id = ?", (to_iso(now_utc() + timedelta(hours=2)), t["id"]))
    if hasattr(db, "commit"):
        db.commit()
    db.close()
    print(f"calls logged: {calls}; overdue follow-ups: {min(6, len(open_tasks))}; written to {out}")


if __name__ == "__main__":
    main()
