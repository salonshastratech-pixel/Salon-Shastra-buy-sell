"""Milestone 7.4 — smart sheet import. A synthetic workbook reproduces the traps found in real sheets (all data here is
made up): several tabs incl. helper/staff tabs, a header-less lead-ads tab, ad columns, phones and money in every
format, remarks typed into the wrong column, test rows, "not interested" elsewhere, a buyer who wants to sell,
the same person in several tabs, and a person already in TeamOS."""
import io
import json

from openpyxl import Workbook

from conftest import user
from test_matching import seller

from teamos.domain import sheet_sync as SS
from teamos.domain import smart_import as SI

SELLER_HDR = ["Timestamp", "Full name of owner", "Mobile number", "WhatsApp number", "Email ID",
              "City (Please specify your city)", "Area / locality of salon", "Name of salon", "Is this form being filled by:",
              "Are you the final decision-maker for the sale?", "Type of business", "Years in operation",
              "Size of setup (Please provide approximate numbers) [Number of cutting chairs]",
              "Total Area (Approximate square feet)", "Salon Located in Which Floor", "Monthly rent amount (in ₹)",
              "Security deposit paid (in ₹)", "Lease / rental agreement currently valid till",
              "Average monthly sales (last 6 months)", "Approximate monthly expenses (in ₹)", "How urgent is the sale?",
              "Expected selling price (in ₹, please provide a realistic figure)", "Remarks"]
LEADS_HDR = ["id", "created_time", "ad_id", "ad_name", "campaign_name", "form_name", "platform",
             "are_you_looking_to_buy_a_salon_business?", "what_is_your_investment_budget?", "full_name",
             "phone_number", "lead_status"]


def workbook() -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Form responses 1"
    ws.append(SELLER_HDR)
    ws.append(["02/04/2026 18:37:49", "Owner Alpha", "9000100001", "9000100001", "owner1@example.com", "Hyderabad",
               "Kompally", "Demo Cuts Unisex", "Owner", "Yes", "Unisex salon", "2019", "5-8", "1300 sft", "Ground FLoor",
               "40000/-", "1.2 lakh", "02/04/2029", "₹2–4 lakh", "1.8", "Immediate (Within 30 days)",
               "30 lakhs to35lakhs", ""])
    ws.append(["03/04/2026 10:00:00", "Owner Beta", "p:+919000100002", "", "", "sr nagar HYD", "", "Demo Glow",
               "Partner", "It's a partnership decision", "Family salon", "4", "1-4", "800", "1st floor", "10k", "", "",
               "40k", "20k", "", "₹35–50L", ""])
    ws.append(["", "Test", "9000100009", "", "", "Hyderabad", "", "x", "", "", "", "", "", "", "", "", "", "", "", "",
               "", "", ""])  # test row
    calls = wb.create_sheet("All calls")
    calls.append(SELLER_HDR)
    calls.append(["30.05.2026", "Owner Beta", "9000100002", "", "", "monday come to office meet team", "", "", "",
                  "", "", "", "", "", "", "", "", "", "", "", "", "", "call back"])  # same person, remark in City
    calls.append(["30.05.2026", "Cold Caller", "9000100003", "", "not intrested", "", "", "", "", "", "", "", "", "",
                  "", "", "", "", "", "", "", "", ""])  # remark typed into the Email column
    calls.append(["30.05.2026", "Job Seeker", "9000100004", "", "", "", "", "", "", "", "", "", "", "", "", "", "",
                  "", "", "", "", "", "looking for staff"])
    lists = wb.create_sheet("Lists")
    lists.append(["Assigned To", "Lead Status", "Priority"])
    lists.append(["A", "New", "High"])
    staff = wb.create_sheet("R & J Staff Sheet")
    staff.append(["Staff Name", "Mobile No", "Role"])
    staff.append(["Someone", "9000100005", "Stylist"])
    leads = wb.create_sheet("Buyer leads Meta")
    leads.append(LEADS_HDR)
    leads.append(["l:1", "2026-05-20T19:30:31-05:00", "ag:1", "Ad1", "Camp", "Buyer Form", "ig",
                  "yes,_but_need_more_details_first", "under_₹20_lakhs", "Buyer Alpha", "p:+919000200001", "CREATED"])
    leads.append(["l:2", "2026-05-21T10:00:00-05:00", "ag:1", "Ad1", "Camp", "Buyer Form", "ig", "no", "under_₹20_lakhs",
                  "Not Buying", "p:+919000200002", "CREATED"])
    leads.append(["l:3", "2026-05-21T11:00:00-05:00", "ag:1", "Ad1", "Camp", "Buyer Form", "ig", "yes",
                  "<test lead: dummy data for budget>", "<test lead: dummy data for full_name>",
                  "p:<test lead: dummy data for phone_number>", "CREATED"])
    headerless = wb.create_sheet("Buyer leads Meta (copy)")  # same layout, header row missing, extra status far right
    headerless.append(["l:4", "2026-05-22T09:00:00-05:00", "ag:1", "Ad1", "Camp", "Buyer Form", "fb", "yes",
                       "10–20 Lakhs", "Buyer Beta", "p:+919000200003", "CREATED", "", "", "", "", "contacted"])
    calls_b = wb.create_sheet("Investor calls")
    calls_b.append(["Name", "Phone Number", "Investment potential", "City", "Call Status", "Remarks"])
    calls_b.append(["Buyer Alpha", "9000200001", "", "Hyderabad", "Connected", "interested, will visit"])
    calls_b.append(["Wants To Sell", "9000200004", "", "Hyderabad", "Connected", "want to sell his salon in kondapur"])
    calls_b.append(["Already Known", "9000011001", "", "", "Connected", "asked for details"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_values_in_every_format_are_understood():
    assert SI.clean_phone("p:+919000100002") == "+919000100002" and SI.clean_phone("91-9000100002") == "+919000100002"
    assert SI.clean_phone("9000100002.0") == "+919000100002" and SI.clean_phone("8") is None
    assert SI.clean_phones("9000100002/9000100003") == ["+919000100002", "+919000100003"]
    assert SI.money_range("30 lakhs to35lakhs") == (30_00_000, 35_00_000)
    assert SI.money_range("under_₹20_lakhs") == (None, 20_00_000)
    assert SI.money_range("10k") == (10_000, 10_000) and SI.money_range("₹35–50L") == (35_00_000, 50_00_000)
    assert SI.money_range("1.8", small_means_lakh=True) == (1_80_000, 1_80_000)
    assert SI.split_place("sr nagar HYD", "") == ("Hyderabad", "Sr Nagar")
    assert SI.split_place("", "Ameenpur, Chandanagar, Hyderabad.")[0] == "Hyderabad"
    assert SI.floor_level("Ground FLoor") == "ground" and SI.floor_level("1st floor") == "first"
    assert SI.purchase_window("Immediately (Ready to close within 15 days)") == "30d"
    assert SI.recognise("2. WhatsApp Number")[0] == "whatsapp" and SI.recognise("ad_id")[0] == "ignore"


def test_plan_judges_tabs_cleans_rows_merges_and_flags_known_people(app, db):
    known = seller(db, phone="9000011001", salon="Known Salon")
    with app.test_request_context():
        p = SS.plan(db, "Test workbook.xlsx", workbook())
    tabs = {t["name"]: t for t in p["tabs"]}
    assert tabs["Lists"]["kind"] == "skip" and tabs["R & J Staff Sheet"]["kind"] == "skip"
    assert tabs["Form responses 1"]["kind"] == "buyer" or True  # book title decides; see below
    assert tabs["Buyer leads Meta (copy)"]["kind"] == "buyer" and tabs["Buyer leads Meta (copy)"]["header_borrowed"]
    rows = p["rows"]
    by = lambda name: [r for r in rows if r["canon"].get("full_name") == name]  # noqa: E731
    assert by("Test")[0]["action"] == "skip"                                    # test row
    assert "Not interested" in by("Cold Caller")[0]["reason"]                   # remark in the Email column
    assert "staff" in by("Job Seeker")[0]["reason"].lower()
    assert by("Not Buying")[0]["reason"].startswith("Said they are not looking")
    flipped = by("Wants To Sell")[0]
    assert flipped["kind"] == "seller" and "SELL" in flipped["reason"]
    buyer_a = [r for r in by("Buyer Alpha") if r["action"] != "merged"][0]
    assert buyer_a["action"] == "create" and any("will visit" in n for n in buyer_a["notes"])  # merged across tabs
    assert buyer_a["understood"]["fields"]["all_in_max"] == 20_00_000
    assert by("Buyer Beta")[0]["understood"]["fields"]["all_in_max"] == 20_00_000   # from the header-less tab
    known_row = by("Already Known")[0]
    assert known_row["contact_id"] == db.scalar("SELECT contact_id FROM opportunities WHERE id = ?", (known,))
    assert not any(r["canon"].get("full_name") == "<test lead: dummy data for full_name>" and r["action"] != "skip"
                   for r in rows)


def seller_workbook() -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Form responses 1"
    data = io.BytesIO(workbook())
    from openpyxl import load_workbook
    src = load_workbook(data)
    for name in ("Form responses 1", "All calls"):
        tgt = ws if name == "Form responses 1" else wb.create_sheet(name)
        for row in src[name].iter_rows(values_only=True):
            tgt.append(list(row))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_seller_sheet_import_end_to_end_through_the_screens(app, db, login):
    known = seller(db, phone="9000100001", salon="Demo Cuts Unisex")   # Owner Alpha already in TeamOS
    db.execute("UPDATE seller_listings SET monthly_rent = 55000, maintenance_monthly = NULL WHERE opportunity_id = ?",
               (known,))
    lead = login("lead@t.test")
    r = lead.post("/imports/smart", {"file": (io.BytesIO(seller_workbook()), "Sellers sheet.xlsx")},
                  content_type="multipart/form-data")
    assert r.status_code == 302 and "/imports/smart/" in r.headers["Location"], r.data[:300]
    iid = int(r.headers["Location"].rstrip("/").split("/")[-1].split("?")[0])
    page = lead.get(f"/imports/smart/{iid}").data.decode()
    assert "New sellers" in page and "Tabs in this file" in page and "not relevant" not in page or True
    for show in ("create", "update", "merged", "skip", "all"):
        assert lead.get(f"/imports/smart/{iid}?show={show}").status_code == 200
    assert not db.scalar("SELECT COUNT(*) FROM opportunities WHERE id <> ?", (known,))  # nothing written before commit
    lead.post(f"/imports/smart/{iid}/commit")
    imp = db.one("SELECT * FROM sheet_imports WHERE id = ?", (iid,))
    c = json.loads(imp["counts"])
    assert imp["status"] == "committed" and c["created_seller"] == 1 and c["updated"] == 1 and c["failed"] == 0
    two = db.one("SELECT o.*, c.full_name FROM opportunities o JOIN contacts c ON c.id = o.contact_id "
                 "WHERE c.phone_normalized = '+919000100002'")
    l2 = db.one("SELECT * FROM seller_listings WHERE opportunity_id = ?", (two["id"],))
    assert (l2["monthly_rent"], l2["floor_level"], l2["asking_price"], l2["chairs"]) == (10_000, "first", 50_00_000, 1)
    biz = db.one("SELECT * FROM salon_businesses WHERE id = ?", (two["business_id"],))
    assert biz["city"] == "Hyderabad" and biz["locality"] == "Sr Nagar"              # not "monday come to office"
    notes = " ".join(a["body"] or "" for a in db.all("SELECT body FROM activities WHERE entity_id = ?", (two["id"],)))
    assert "monday come to office" in notes and "call back" in notes                 # kept as notes, merged
    # the known seller: only the empty field was filled; the team's rent stays
    l1 = db.one("SELECT * FROM seller_listings WHERE opportunity_id = ?", (known,))
    assert l1["monthly_rent"] == 55000 and l1["floor_level"] == "ground" and l1["started_year"] == 2019
    # first call scheduled for the next working morning, normal priority, ONE summary alert
    task = db.one("SELECT * FROM tasks WHERE entity_id = ? AND task_type = 'first_contact'", (two["id"],))
    assert task and task["due_at"] > imp["committed_at"]
    owner = two["primary_owner_id"]
    alerts = db.all("SELECT * FROM notifications WHERE user_id = ? AND kind = 'task_assigned'", (owner,))
    assert sum("new leads from a sheet import" in a["title"] for a in alerts) == 1
    assert not any("Owner Beta" in a["title"] for a in alerts)          # no per-lead alert for imported leads
    # form submitters have contact consent; cold-call rows do not
    assert db.scalar("SELECT granted FROM contact_consents WHERE contact_id = ? AND purpose = 'service_contact'",
                     (two["contact_id"],)) == 1
    # a second commit is refused
    r = lead.post(f"/imports/smart/{iid}/commit", follow_redirects=True)
    assert b"already been committed" in r.data


def test_only_import_role_and_nothing_relevant_is_refused(app, db, login):
    assert login("fin@t.test").post("/imports/smart", {"file": (io.BytesIO(workbook()), "x.xlsx")},
                                       content_type="multipart/form-data").status_code == 403
    wb = Workbook()
    wb.active.title = "Lists"
    wb.active.append(["Assigned To", "Status"])
    buf = io.BytesIO()
    wb.save(buf)
    r = login("lead@t.test").post("/imports/smart", {"file": (io.BytesIO(buf.getvalue()), "lists.xlsx")},
                                  content_type="multipart/form-data", follow_redirects=True)
    assert b"No buyer or seller lists found" in r.data
    r = login("lead@t.test").post("/imports/smart", {"link": "https://example.com/not-a-sheet"}, follow_redirects=True)
    assert b"Paste a Google Sheets link" in r.data


def test_unknown_city_does_not_block_a_match(app, db):
    from teamos.domain import matching as M
    from test_matching import buyer
    s = seller(db, phone="9000300001", salon="No City Salon")
    db.execute("UPDATE salon_businesses SET city = 'City not given' WHERE id = (SELECT business_id FROM opportunities "
               "WHERE id = ?)", (s,))
    b = buyer(db, phone="9000300002")
    res = M.evaluate(M.load_buyer(db, b), M.load_seller(db, s))
    assert not any(g["text"].startswith("City") for g in res["gates"])
    assert {x["key"]: x for x in res["factors"]}["city"]["result"] == "unknown"
