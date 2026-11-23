"""QA: load 100 synthetic sellers + 100 synthetic buyers THROUGH THE SCREENS, then exercise every module.

Usage:  python -I scripts/qa/load_and_sweep.py <repo root> <path/to/qa-copy.db> [--keep]
Works on a copy of the demo database (never the live one). All people, phones and salons are made up.
Reports: crashes (with the line that failed), actions refused with an error message, slow pages, final counts.
"""
import io
import json
import random
import re
import shutil
import sys
import time
import traceback
from collections import Counter, defaultdict

root, copy = sys.argv[1], sys.argv[2]
sys.path.insert(0, root)
import os  # noqa: E402

os.chdir(root)
sys.stdout.reconfigure(encoding="utf-8")
from manage import _load_dotenv  # noqa: E402

_load_dotenv()
import logging  # noqa: E402

logging.disable(logging.CRITICAL)
shutil.copy("instance/teamos-demo.db", copy)
from teamos import create_app  # noqa: E402
from teamos.db import migrate, open_standalone  # noqa: E402
from teamos.security import hash_password  # noqa: E402

app = create_app({"DATABASE_URL": "sqlite:///" + copy, "COOKIE_SECURE": False, "TESTING": True})
app.config["PROPAGATE_EXCEPTIONS"] = True
db = open_standalone("sqlite:///" + copy)
migrate(db)
rnd = random.Random(42)
PW = "Qa-Sweep-Password-1"
ROLES = {
    "admin": "SELECT email FROM users WHERE role='super_admin' AND is_active=1 ORDER BY id",
    "lead": "SELECT email FROM users WHERE role='team_lead' AND is_active=1 ORDER BY id",
    "seller": "SELECT email FROM users WHERE role='team_member' AND team='seller' AND is_active=1 ORDER BY id",
    "buyer": "SELECT email FROM users WHERE role='team_member' AND team='buyer' AND is_active=1 ORDER BY id",
    "fin": "SELECT email FROM users WHERE role='finance' AND is_active=1 ORDER BY id",
}
EMAIL = {k: db.scalar(q) for k, q in ROLES.items()}
for e in EMAIL.values():
    db.execute("UPDATE users SET password_hash = ?, must_change_password = 0, mfa_enabled_at = NULL WHERE email = ?",
               (hash_password(PW), e))
db.execute("DELETE FROM login_attempts")

crashes, refusals, slow, done = [], Counter(), [], Counter()


class C:
    def __init__(self, role):
        self.role = role
        self.c = app.test_client()
        r = self.c.post("/login", data={"email": EMAIL[role], "password": PW})
        assert r.status_code == 302, (role, r.status_code)
        self.tok = re.search(r'name="csrf_token" value="([^"]+)"', self.c.get("/").data.decode()).group(1)

    def _do(self, method, url, data=None, files=False, expect_ok=True, label=None):
        t = time.time()
        try:
            if method == "GET":
                r = self.c.get(url)
            else:
                d = dict(data or {})
                d["csrf_token"] = self.tok
                r = self.c.post(url, data=d, content_type="multipart/form-data" if files else None,
                                follow_redirects=True)
        except Exception as e:  # noqa: BLE001
            tb = traceback.extract_tb(e.__traceback__)
            where = next((f"{fr.filename.split('salon-shastra-teamos')[-1]}:{fr.lineno}" for fr in reversed(tb)
                          if "teamos" in fr.filename), "?")
            crashes.append((self.role, method, url.split("?")[0], f"{type(e).__name__}: {str(e)[:160]}", where))
            return None
        ms = (time.time() - t) * 1000
        if method == "GET" and ms > 1500:
            slow.append((round(ms), url))
        if r.status_code >= 500:
            crashes.append((self.role, method, url, f"HTTP {r.status_code}", "?"))
        body = r.data.decode(errors="replace")
        if method == "POST":
            errs = re.findall(r'class="flash error">([^<]{0,160})', body)
            if errs and expect_ok:
                refusals[(label or re.sub(r"/\d+", "/#", url.split("?")[0]), errs[0][:110])] += 1
            else:
                done[label or re.sub(r"/\d+", "/#", url.split("?")[0])] += 1
        return r

    def get(self, url, **kw):
        return self._do("GET", url, **kw)

    def post(self, url, data=None, **kw):
        return self._do("POST", url, data, **kw)


U = {k: C(k) for k in EMAIL}
FIRST = ["Ravi", "Sita", "Arjun", "Priya", "Kiran", "Lakshmi", "Suresh", "Anitha", "Vamsi", "Divya", "Rahul", "Meena",
         "Naveen", "Swathi", "Harsha", "Pooja", "Vijay", "Kavya", "Teja", "Bhavana"]
LAST = ["Reddy", "Rao", "Naidu", "Sharma", "Goud", "Varma", "Chowdary", "Yadav", "Kumar", "Patel"]
AREAS = {"Hyderabad": ["Kondapur", "Madhapur", "Gachibowli", "Kukatpally", "Miyapur", "Ameerpet", "Banjara Hills",
                       "Jubilee Hills", "Dilsukhnagar", "Kompally", "Manikonda", "Uppal"],
         "Vijayawada": ["Benz Circle", "Patamata"], "Warangal": ["Hanamkonda"], "Bengaluru": ["HSR Layout", "Indiranagar"]}
CITY_W = ["Hyderabad"] * 7 + ["Vijayawada", "Warangal", "Bengaluru"]
WORDS = ["Glow", "Style", "Mirror", "Shine", "Luxe", "Urban", "Crown", "Bliss", "Velvet", "Aura"]
KINDS = ["Studio", "Salon", "Unisex Salon", "Hair Lounge", "Beauty Parlour"]
used = set()


def phone():
    while True:
        p = "6" + "".join(str(rnd.randint(0, 9)) for _ in range(9))
        if p not in used:
            used.add(p)
            return p


def pick(*xs):
    return rnd.choice(xs)


def maybe(v, p=0.8):
    return v if rnd.random() < p else ""


def new_id(c, kind):
    return db.scalar(f"SELECT MAX(id) FROM opportunities WHERE kind = '{kind}'")


T0 = time.time()
# ---------------------------------------------------------------------------------------------------------------
# 1. 100 sellers through the screens
sellers = []
for i in range(100):
    city = rnd.choice(CITY_W)
    area = rnd.choice(AREAS[city])
    form = {"full_name": f"{pick(*FIRST)} {pick(*LAST)} QA{i:03d}", "phone": phone(), "email": maybe(f"qa{i}@example.com", .4),
            "salon_name": f"{pick(*WORDS)} {pick(*KINDS)} QA{i:03d}", "city": city, "locality": area,
            "source": pick("manual_call", "referral", "meta_lead", "landing_page", "whatsapp"),
            "consent_contact": "1" if rnd.random() < .85 else "", "operating_status": pick("operating", "operating", "non_operating"),
            "ownership_role": pick("owner", "owner", "partner", "manager"),
            "sale_purpose": pick("sell_entire", "sell_entire", "partnership", "franchise_transfer", "seek_help"),
            "asking_price": f"{rnd.randint(8, 80)}L", "sale_timeline": pick("immediate", "within_3m", "3_6m", "no_rush"),
            "owner_id": str(db.scalar("SELECT id FROM users WHERE email = ?", (EMAIL["seller"],)))}
    if i % 25 == 7:  # a duplicate on purpose
        form["phone"] = sellers[0]["phone"] if sellers else form["phone"]
        form["dedupe_decision"] = "new_flagged"
    U["seller"].post("/sellers/new", form, label="seller:create")
    sid = new_id(U["seller"], "seller")
    sellers.append({"id": sid, "phone": form["phone"], "city": city, "area": area})
    rent = rnd.randint(20, 150) * 1000
    sections = {
        "profile": {"operating_years": str(rnd.randint(1, 15)), "gender_format": pick("men", "women", "unisex"),
                    "business_model": pick("independent", "independent", "franchise"), "franchise_brand": "QA Brand",
                    "positioning": pick("luxury", "premium", "mid", "value"), "pin_code": f"500{rnd.randint(10, 99)}",
                    "started_year": str(2026 - rnd.randint(1, 15))},
        "premises": {"floor_level": pick("ground", "first", "upper", "mall"), "area_type": pick("main_road", "market", "mall", "residential"),
                     "frontage_ft": str(rnd.randint(8, 40)), "parking": pick("car_and_bike", "bike_only", "none"),
                     "signage_visible": pick("yes", "no"), "washrooms": str(rnd.randint(0, 3)), "ac_type": pick("central", "split", "none")},
        "asset": {"carpet_area_sqft": str(rnd.randint(300, 3000)), "chairs": str(rnd.randint(2, 16)),
                  "wash_stations": str(rnd.randint(1, 5)), "treatment_rooms": str(rnd.randint(0, 4)),
                  "equipment_condition": pick("excellent", "good", "fair", "poor"), "equipment_age_years": str(rnd.randint(0, 10))},
        "lease": {"monthly_rent": str(rent), "maintenance_monthly": maybe(str(rent // 10), .6), "rent_deposit": f"{rnd.randint(1, 10)}L",
                  "deposit_treatment": pick("included_in_asking", "buyer_pays_separately", "not_applicable"),
                  "lease_expiry": f"{rnd.randint(2026, 2034)}-{rnd.randint(1, 12):02d}-01", "lease_renewal": pick("yes", "no", "negotiable"),
                  "lease_transfer_status": pick("unchecked", "landlord_pending", "confirmed_transferable", "not_permitted"),
                  "gst_on_rent": pick("yes", "no")},
        "operations": {"avg_monthly_sales": f"{rnd.randint(1, 15)}L", "avg_monthly_expenses": f"{rnd.randint(1, 12)}L",
                       "electricity_monthly": str(rnd.randint(3, 30) * 1000), "monthly_salaries": str(rnd.randint(30, 300) * 1000)},
        "staff": {"staff_headcount": str(rnd.randint(2, 25)), "staff_continuity": pick("yes", "partial", "no"),
                  "key_staff_dependence": pick("high", "medium", "low"), "owner_involvement": pick("full_time", "part_time", "absentee")},
        "licences": {"trade_licence": pick("yes", "applied", "no"), "gst_registered": pick("yes", "no")},
        "liabilities": {"outstanding_loans": pick("0", "2L", ""), "litigation": pick("no", "no", "yes")},
        "reason": {"sale_reason": pick("relocation", "new_venture", "personal", "financial_pressure"), "transition_support": pick("yes", "no", "negotiable")},
        "commercial": {"negotiable": pick("yes", "no"), "sale_structure": pick("entity", "asset", "partial_stake"),
                       "stock_value_separate__state": "not_applicable", "expected_handover_weeks": str(rnd.randint(2, 12))},
        "customers": {"avg_monthly_bills": str(rnd.randint(100, 2000)), "avg_ticket": str(rnd.randint(200, 3000)),
                      "google_rating": f"{rnd.randint(35, 49) / 10} ({rnd.randint(10, 900)} reviews)"},
    }
    for sec, data in sections.items():
        if rnd.random() < 0.85:
            U["seller"].post(f"/sellers/{sid}/section/{sec}", data, label=f"seller:section:{sec}")
    for m in range(rnd.randint(0, 4)):
        U["seller"].post(f"/sellers/{sid}/financials", {"month": f"2026-{m + 6:02d}", "revenue": f"{rnd.randint(1, 12)}L",
                                                        "expenses": f"{rnd.randint(1, 9)}L", "drawings": "", "source": "staff_entry"},
                         label="seller:financials")
    if rnd.random() < .4:
        U["seller"].post(f"/sellers/{sid}/swot", {"kind": pick("strength", "weakness", "opportunity", "threat"),
                                                  "statement": "QA observation", "source": "staff_observation", "confidence": "medium"},
                         label="seller:swot")
    U["seller"].post(f"/sellers/{sid}/call", {"outcome": pick("connected", "no_answer", "busy", "callback_requested"),
                                              "note": "QA call", "next_title": "Follow up", "next_due": "2026-12-01T11:00"},
                     label="seller:call")
    if rnd.random() < .3:
        U["seller"].post(f"/sellers/{sid}/note", {"body": f"QA note for {sid}"}, label="seller:note")
print(f"sellers loaded in {time.time() - T0:.0f}s")

# ---------------------------------------------------------------------------------------------------------------
# 2. 100 buyers through the screens
buyers = []
for i in range(100):
    city = rnd.choice(CITY_W)
    form = {"full_name": f"{pick(*FIRST)} {pick(*LAST)} QB{i:03d}", "phone": phone(), "city": city,
            "locality": rnd.choice(AREAS[city]), "source": pick("referral", "meta_lead", "landing_page", "event"),
            "consent_contact": "1" if rnd.random() < .9 else "",
            "need_type": pick("acquire_operating", "acquire_operating", "franchise", "partnership", "passive_invest", "new_salon", "exploring"),
            "decision_maker": pick("yes", "shared", "no"), "all_in_max": f"{rnd.randint(10, 90)}L",
            "purchase_window": pick("30d", "90d", "180d", "later"),
            "owner_id": str(db.scalar("SELECT id FROM users WHERE email = ?", (EMAIL["buyer"],)))}
    U["buyer"].post("/buyers/new", form, label="buyer:create")
    bid = new_id(U["buyer"], "buyer")
    buyers.append({"id": bid, "phone": form["phone"], "city": city})
    secs = {
        "budget": {"budget_min": f"{rnd.randint(5, 20)}L", "budget_max": f"{rnd.randint(20, 70)}L",
                   "includes_deposit": pick("yes", "no"), "includes_stock": pick("yes", "no"),
                   "max_monthly_rent": str(rnd.randint(30, 200) * 1000), "max_monthly_rent__importance": pick("must", "prefer", "flexible")},
        "funding": {"funding_source": pick("self", "loan", "mixed"), "cash_ready": pick("now", "within_30d", "planned")},
        "geography": {"preferred_city": city, "relocate_willing": pick("yes", "no")},
        "salon": {"chairs_min": str(rnd.randint(2, 10)), "chairs_min__importance": pick("must", "prefer", "flexible"),
                  "parking_need": pick("car", "bike", "not_needed"), "parking_need__importance": pick("must", "prefer")},
        "business": {"min_monthly_sales": f"{rnd.randint(1, 8)}L", "min_monthly_sales__importance": pick("prefer", "flexible"),
                     "turnaround_ok": pick("yes", "no"), "min_lease_years_left": str(rnd.randint(1, 5))},
        "timing": {"involvement": pick("owner_operator", "partly", "absentee"), "visit_availability": pick("yes", "weekends")},
    }
    for sec, data in secs.items():
        if rnd.random() < .85:
            U["buyer"].post(f"/buyers/{bid}/section/{sec}", data, label=f"buyer:section:{sec}")
    U["buyer"].post(f"/buyers/{bid}/preferences", {"pref__gender_format__unisex": pick("must", "prefer", ""),
                                                   "pref__floor_level__ground": pick("prefer", ""),
                                                   "pref__area_type__main_road": pick("prefer", "flexible", "")}, label="buyer:prefs")
    for a in rnd.sample(AREAS[city], k=min(2, len(AREAS[city]))):
        U["buyer"].post(f"/buyers/{bid}/locality", {"kind": "preferred", "city": city, "locality": a}, label="buyer:locality")
    if rnd.random() < .7:
        U["lead"].post(f"/buyers/{bid}/funding", {"status": pick("verified", "staff_reviewed", "evidence_requested"), "note": "QA"},
                       label="buyer:funding")
    if rnd.random() < .7:
        U["lead"].post(f"/buyers/{bid}/confirm-criteria", {}, label="buyer:confirm", expect_ok=True)
    U["buyer"].post(f"/buyers/{bid}/call", {"outcome": pick("connected", "no_answer"), "note": "QA",
                                            "next_title": "Follow up", "next_due": "2026-12-02T11:00"}, label="buyer:call")
print(f"buyers loaded in {time.time() - T0:.0f}s")

# ---------------------------------------------------------------------------------------------------------------
# 3. Listing approvals for 40 sellers (checklist → review → decision)
from teamos.domain import seller_fields as SF  # noqa: E402

for s in sellers[:40]:
    for key, _ in SF.LISTING_CHECKLIST:
        U["lead"].post(f"/sellers/{s['id']}/checklist", {"item_key": key, "status": "reviewed"}, label="seller:checklist")
    U["seller"].post(f"/sellers/{s['id']}/request-review", {}, label="seller:request-review")
    U["admin"].post(f"/sellers/{s['id']}/decision", {"decision": "approved"}, label="seller:approve")
print(f"approvals done in {time.time() - T0:.0f}s")

# ---------------------------------------------------------------------------------------------------------------
# 4. Forms: personal detailed forms answered by "customers", then reviewed
public = app.test_client()
for kind, people, member in (("seller", sellers[40:50], "seller"), ("buyer", buyers[:10], "buyer")):
    for p in people:
        r = U[member].post(f"/forms/send/{p['id']}", {"template_key": f"{kind}_deep", "channel": "copy_link"},
                           label=f"form:send:{kind}")
        m = re.search(r"/f/i/([A-Za-z0-9_\-]+)", r.data.decode()) if r is not None else None
        if not m:
            continue
        tok = m.group(1)
        g = public.get(f"/f/i/{tok}")
        if g.status_code != 200:
            crashes.append(("public", "GET", "/f/i/<token>", f"HTTP {g.status_code}", "?"))
        ans = ({"monthly_rent": "60k", "maintenance_monthly": "6000", "floor_level": "ground", "parking": "bike_only",
                "chairs": "7", "action": "submit"} if kind == "seller" else
               {"all_in_max": "45L", "cash_ready": "now", "parking_need": "car", "pref__floor_level__ground": "must",
                "localities_preferred": "Kondapur\nMadhapur", "action": "submit"})
        try:
            pr = public.post(f"/f/i/{tok}", data=ans)
            if pr.status_code >= 500 or b"Thank you" not in pr.data:
                refusals[(f"form:submit:{kind}", re.sub(r"\s+", " ", pr.data.decode(errors='replace'))[-120:])] += 1
        except Exception as e:  # noqa: BLE001
            crashes.append(("public", "POST", "/f/i/<token>", f"{type(e).__name__}: {e}"[:160], "?"))
subs = db.all("SELECT id, answers FROM form_submissions WHERE status = 'submitted' ORDER BY id DESC LIMIT 20")
for sub in subs:
    keys = [k for k in json.loads(sub["answers"] or "{}") if not k.startswith("_")]
    U["lead"].get(f"/forms/submissions/{sub['id']}")
    U["lead"].post(f"/forms/submissions/{sub['id']}", {"accept": keys, "action": "apply"}, label="form:review-apply")
U["lead"].get("/forms/")

# consents for sharing (as staff would record after a call)
for s in sellers[:40]:
    cid = db.scalar("SELECT contact_id FROM opportunities WHERE id = ?", (s["id"],))
    db.insert("contact_consents", {"contact_id": cid, "purpose": "share_teaser", "channel": "phone", "granted": 1,
                                   "source": "qa", "captured_at": "2026-11-01T00:00:00Z"})
for b in buyers:
    cid = db.scalar("SELECT contact_id FROM opportunities WHERE id = ?", (b["id"],))
    db.insert("contact_consents", {"contact_id": cid, "purpose": "share_teaser", "channel": "phone", "granted": 1,
                                   "source": "qa", "captured_at": "2026-11-01T00:00:00Z"})

# ---------------------------------------------------------------------------------------------------------------
# 5. Matching: recompute, every queue, match pages, stages, split tasks, ask questions
t = time.time()
U["lead"].post("/matches/recompute", {"force": "1"}, label="match:recompute")
print(f"recompute took {time.time() - t:.1f}s; candidates={db.scalar('SELECT COUNT(*) FROM match_candidates')}")
from teamos.domain import matching as MM  # noqa: E402

for q in MM.QUEUES:
    U["lead"].get(f"/matches/?queue={q}")
ready = db.all("SELECT id FROM match_candidates WHERE readiness = 'ready_to_approach' AND suppressed = 0 LIMIT 8")
near = db.all("SELECT id FROM match_candidates WHERE outcome = 'near' AND suppressed = 0 LIMIT 6")
info = db.all("SELECT id FROM match_candidates WHERE readiness = 'info_required' LIMIT 6")
for c in ready + near + info:
    U["lead"].get(f"/matches/{c['id']}")
for c in near[:3]:
    U["lead"].post(f"/matches/{c['id']}/stage", {"to": "shortlisted", "note": "QA"}, label="match:shortlist")
for c in info[:3]:
    U["lead"].post(f"/matches/{c['id']}/tasks", {"buyer_task": "Ask budget", "seller_task": "Ask rent",
                                                 "due_at": "2026-12-03T10:00"}, label="match:split-tasks")
    U["lead"].post(f"/matches/{c['id']}/ask/buyer", {"channel": "copy_link"}, label="match:ask", expect_ok=False)
    U["lead"].post(f"/matches/{c['id']}/note", {"body": "QA note"}, label="match:note")
for c in info[3:5]:
    U["lead"].post(f"/matches/{c['id']}/stage", {"to": "not_suitable", "reason": "budget", "note": "QA"},
                   label="match:decline")
print(f"matching: ready={len(ready)} near={len(near)}")

# ---------------------------------------------------------------------------------------------------------------
# 6. Deals: approach → teaser → share → consents → introduction → deal room → close; fees
deals = []
for c in ready[:4]:
    cid = c["id"]
    mc = db.one("SELECT * FROM match_candidates WHERE id = ?", (cid,))
    U["lead"].post(f"/matches/{cid}/stage", {"to": "approved_to_approach"}, label="match:approve")
    U["lead"].post(f"/matches/{cid}/stage", {"to": "interest_confirmed", "note": "QA keen"}, label="match:interest")
    sid = mc["seller_opp_id"]
    if not db.scalar("SELECT id FROM teasers WHERE seller_opp_id = ? AND status = 'approved'", (sid,)):
        U["seller"].post(f"/deals/teasers/seller/{sid}", {"headline": "Well-run salon in a busy area",
                                                          "highlights": "Loyal clients",
                                                          "fields": ["area", "format", "size", "asking", "sales_band"]},
                         label="teaser:create")
        tid = db.scalar("SELECT MAX(id) FROM teasers WHERE seller_opp_id = ?", (sid,))
        U["seller"].post(f"/deals/teasers/{tid}/seller-approval", {"method": "call"}, label="teaser:seller-approval")
        U["admin"].post(f"/deals/teasers/{tid}/approve", {}, label="teaser:approve")
    r = U["buyer"].post(f"/deals/match/{cid}/share-teaser", {"channel": "copy_link"}, label="teaser:share")
    m = re.search(r"/t/([A-Za-z0-9_\-]+)", r.data.decode()) if r is not None else None
    if m:
        pg = public.get(f"/t/{m.group(1)}")
        if pg.status_code != 200:
            crashes.append(("public", "GET", "/t/<token>", f"HTTP {pg.status_code}", "?"))
    for side in ("buyer", "seller"):
        U["buyer"].post(f"/deals/match/{cid}/consent", {"side": side, "method": "call"}, label="intro:consent")
    U["buyer"].post(f"/deals/match/{cid}/introduce", {"note": "Both keen"}, label="intro:request")
    iid = db.scalar("SELECT id FROM introductions WHERE match_id = ?", (cid,))
    if iid:
        U["admin"].post(f"/deals/introductions/{iid}", {"decision": "approved"}, label="intro:approve")
    did = db.scalar("SELECT id FROM deals WHERE match_id = ?", (cid,))
    if did:
        deals.append(did)
for n, did in enumerate(deals):
    U["seller"].get(f"/deals/{did}")
    U["seller"].post(f"/deals/{did}/visit", {"when": "2026-12-05T11:00", "attendees": "Buyer, owner"}, label="deal:visit")
    vid = db.scalar("SELECT MAX(id) FROM deal_visits WHERE deal_id = ?", (did,))
    U["seller"].post(f"/deals/visits/{vid}", {"status": "completed", "outcome": "Good", "feedback": "Liked it"},
                     label="deal:visit-done")
    U["seller"].post(f"/deals/{did}/offer", {"from_party": "buyer", "amount": "20L", "valid_until": "2026-12-20"},
                     label="deal:offer")
    oid = db.scalar("SELECT MAX(id) FROM deal_offers WHERE deal_id = ?", (did,))
    U["seller"].post(f"/deals/offers/{oid}", {"status": "accepted"}, label="deal:accept")
    if n == len(deals) - 1 and n > 0:
        U["admin"].post(f"/deals/{did}/stage", {"to": "closed_lost", "reason": "buyer_withdrew",
                                                "lessons": "Check funding earlier"}, label="deal:lost")
        continue
    for key in ("ownership", "lease", "financials", "liabilities", "prepaid", "equipment", "employees", "tax_legal", "brand"):
        U["seller"].post(f"/deals/{did}/check", {"item_key": key, "status": "reviewed"}, label="deal:check")
    U["seller"].post(f"/deals/{did}/dates", {"agreement_date": "2026-12-20", "handover_date": "2027-01-05"}, label="deal:dates")
    U["fin"].post("/admin/finance/agreement", {"deal_id": did, "payer": "seller", "fee_type": "success", "basis": "percent",
                                               "rate_pct": "5", "earning_condition": "on_closing"}, label="fee:agreement")
    U["admin"].post(f"/deals/{did}/stage", {"to": "closed_won"}, label="deal:won")
    aid = db.scalar("SELECT MAX(id) FROM fee_agreements WHERE deal_id = ?", (did,))
    if aid:
        U["fin"].post("/admin/finance/invoice", {"agreement_id": aid, "amount": "1L", "issued_on": "2027-01-06",
                                                 "invoice_no": f"QA-{did}"}, label="fee:invoice")
        U["fin"].post("/admin/finance/receipt", {"agreement_id": aid, "amount": "40000", "received_on": "2027-01-10",
                                                 "mode": "UPI"}, label="fee:receipt")
U["fin"].post("/admin/finance/expense", {"category": "Marketing", "amount": "15000", "spent_on": "2026-11-20",
                                         "note": "QA ads"}, label="fee:expense")
for u in ("fin", "admin"):
    U[u].get("/admin/finance")
U["lead"].get("/deals/")
U["lead"].get("/deals/?view=all")
print(f"deals: {len(deals)} created")

# ---------------------------------------------------------------------------------------------------------------
# 7. WhatsApp inbox, quick messages, batches, STOP
for s in sellers[50:60]:
    U["seller"].post(f"/inbox/start/{s['id']}", {}, label="inbox:start")
    U["seller"].post(f"/inbox/quick/{s['id']}", {"sid": "callback_request", "outcome": "no_answer"}, label="inbox:quick",
                     expect_ok=False)
for b in buyers[20:30]:
    U["buyer"].post("/inbox/simulate", {"phone": b["phone"], "text": "Any salon in my budget?"}, label="inbox:simulate")
    conv = db.scalar("SELECT id FROM conversations WHERE phone = ?", ("+91" + b["phone"],))
    if conv:
        U["buyer"].get(f"/inbox/{conv}")
        U["buyer"].post(f"/inbox/{conv}/send", {"mode": "text", "text": "Yes — sharing options today."}, label="inbox:reply")
        U["buyer"].post(f"/inbox/{conv}/action", {"action": "assign", "user_id": db.scalar(
            "SELECT id FROM users WHERE email = ?", (EMAIL["buyer"],))}, label="inbox:assign")
U["buyer"].post("/inbox/simulate", {"phone": buyers[30]["phone"], "text": "STOP"}, label="inbox:stop")
U["buyer"].post("/inbox/simulate", {"phone": "6999999990", "text": "I want to sell my salon"}, label="inbox:unknown")
for q in ("mine", "unassigned", "unmatched", "open", "closed"):
    U["buyer"].get(f"/inbox/?q={q}")
U["buyer"].post("/inbox/batches", {"name": "QA follow-up", "template_key": "next_steps", "audience": "buyers_active"},
                label="batch:create")
bat = db.scalar("SELECT MAX(id) FROM batches")
U["buyer"].get(f"/inbox/batches/{bat}")
U["lead"].post(f"/inbox/batches/{bat}", {"action": "approve"}, label="batch:approve")
U["lead"].post(f"/inbox/batches/{bat}", {"action": "send"}, label="batch:send")

# ---------------------------------------------------------------------------------------------------------------
# 8. Tasks, My Day, team, search, reports, digest, admin, dictionary
for t_ in db.all("SELECT id FROM tasks WHERE status = 'open' AND assignee_id = (SELECT id FROM users WHERE email = ?) "
                 "ORDER BY due_at LIMIT 15", (EMAIL["seller"],)):
    U["seller"].post(f"/tasks/{t_['id']}/complete", {"outcome": "Done in QA", "next_title": "Next QA step",
                                                    "next_due": "2026-12-10T10:00"}, label="task:complete")
for u in U.values():
    u.get("/")
    u.get("/tasks/")
for q in ("QA0", "Kondapur", "60000", "SL-", "BY-", "Glow"):
    U["lead"].get(f"/search?q={q}")
for v in ("today", "week", "month"):
    U["admin"].get(f"/reports/?period={v}")
U["admin"].get("/reports/export?period=month") or U["admin"].get("/reports/?period=month&format=csv")
U["seller"].post("/me/digest", {"on": "1"}, label="digest:on")
U["seller"].get("/me/digest/preview")
for url in ("/team", "/dictionary", "/admin/users", "/admin/audit", "/admin/duplicates", "/admin/jobs",
            "/admin/integrations", "/admin/templates", "/admin/outbox", "/imports/", "/sellers/?status=all",
            "/buyers/?status=all", "/sellers/?q=Kondapur", "/buyers/?queue=funding", "/buyers/?queue=criteria"):
    U["admin"].get(url)
for s in rnd.sample(sellers, 15):
    for tab in ("", "?tab=intake", "?tab=financials", "?tab=assessment", "?tab=approval", "?tab=matches",
                "?tab=teaser", "?tab=history"):
        U["seller"].get(f"/sellers/{s['id']}{tab}")
for b in rnd.sample(buyers, 15):
    for tab in ("", "?tab=requirements", "?tab=qualification", "?tab=matches", "?tab=history"):
        U["buyer"].get(f"/buyers/{b['id']}{tab}")

# ---------------------------------------------------------------------------------------------------------------
# 9. Smart import of a generated workbook: 20 new people + 10 already in TeamOS
from openpyxl import Workbook  # noqa: E402

wb = Workbook()
ws = wb.active
ws.title = "Seller form responses"
ws.append(["Timestamp", "Full name of owner", "Mobile number", "City", "Area / locality of salon", "Name of salon",
           "Monthly rent amount (in ₹)", "Expected selling price", "Remarks"])
for i in range(20):
    ws.append(["01/11/2026 10:00:00", f"Import Owner {i}", phone(), "Hyderabad", "Kondapur", f"Imported Salon {i}",
               f"{rnd.randint(20, 90)}k", f"{rnd.randint(15, 60)} lakhs", "keen"])
for s in sellers[60:70]:
    ws.append(["01/11/2026 11:00:00", "Existing", s["phone"], s["city"], s["area"], "", "", "", "called again"])
buf = io.BytesIO()
wb.save(buf)
r = U["lead"].post("/imports/smart", {"file": (io.BytesIO(buf.getvalue()), "qa-import.xlsx")}, files=True,
                   label="import:upload")
imp = db.scalar("SELECT MAX(id) FROM sheet_imports")
if imp:
    U["lead"].get(f"/imports/smart/{imp}")
    U["lead"].post(f"/imports/smart/{imp}/commit", {}, label="import:commit")
    print("import counts:", db.scalar("SELECT counts FROM sheet_imports WHERE id = ?", (imp,)))

# ---------------------------------------------------------------------------------------------------------------
# 10. Background jobs + page speed with the load
from teamos import jobs  # noqa: E402

with app.app_context():
    try:
        jobs.schedule_periodic(db)
        print("jobs run:", jobs.run_once(db))
    except Exception as e:  # noqa: BLE001
        crashes.append(("worker", "-", "jobs.run_once", f"{type(e).__name__}: {e}"[:160], "?"))
failed_jobs = db.all("SELECT kind, last_error FROM jobs WHERE status = 'failed'")
for j in failed_jobs:
    crashes.append(("worker", "-", j["kind"], (j["last_error"] or "")[:160], "?"))
for url in ("/", "/sellers/", "/buyers/", "/matches/", "/matches/?q=info", "/matches/?q=all", "/tasks/?view=all",
            "/team", "/reports/?period=month", "/inbox/", "/deals/?view=all"):
    t = time.time()
    U["lead"].get(url)
    ms = (time.time() - t) * 1000
    print(f"   {url:28} {ms:7.0f} ms")

# ---------------------------------------------------------------------------------------------------------------
print("\n=== TOTALS ===")
for k, q in (("sellers", "SELECT COUNT(*) FROM opportunities WHERE kind='seller'"),
             ("buyers", "SELECT COUNT(*) FROM opportunities WHERE kind='buyer'"),
             ("approved listings", "SELECT COUNT(*) FROM seller_listings WHERE approval_state='approved'"),
             ("match candidates", "SELECT COUNT(*) FROM match_candidates"), ("deals", "SELECT COUNT(*) FROM deals"),
             ("deals won", "SELECT COUNT(*) FROM deals WHERE stage='closed_won'"),
             ("messages", "SELECT COUNT(*) FROM messages"), ("tasks open", "SELECT COUNT(*) FROM tasks WHERE status='open'"),
             ("form submissions", "SELECT COUNT(*) FROM form_submissions")):
    print(f"  {k:18} {db.scalar(q)}")
print(f"\nactions that worked: {sum(done.values())}")
print(f"CRASHES: {len(crashes)}")
for c in crashes[:40]:
    print("  ", c)
print(f"REFUSED WITH AN ERROR MESSAGE: {sum(refusals.values())}")
for (lbl, msg), n in refusals.most_common(40):
    print(f"   {n:4} {lbl:28} {msg}")
print("SLOW PAGES (>1.5 s):", sorted(slow, reverse=True)[:10])
print(f"total time {time.time() - T0:.0f}s")
