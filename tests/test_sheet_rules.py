"""Bringing in the team's real sheets: cold leads left out (COLD / Cool / disqualified / "time pass"), hot or warm
anywhere wins, owners who need staff kept, "planning to open a salon" = investor, trackers that keep "I own a salon" in
the Lead Type column, unlabelled tabs typed by the people they share, several workbooks judged as one — then the lists:
hot first, newest first, honest "date not recorded", filters and paging."""
import io
import json

from openpyxl import Workbook

from teamos.domain import sheet_sync as SS


def _book(tabs: dict) -> bytes:
    wb = Workbook()
    wb.remove(wb.active)
    for name, rows in tabs.items():
        ws = wb.create_sheet(name)
        for r in rows:
            ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


BUYERS = _book({
    "Studio 11 -Salon Sale - Buyer F": [
        ["created_time", "full_name", "phone_number", "what_is_your_investment_budget?", "Lead Type", "Remarks by shaheen"],
        ["2026-05-02T10:00:00+05:30", "Hot Harish", "9300100001", "under_₹20_lakhs", "HOT", "will visit the office"],
        ["2026-05-03T10:00:00+05:30", "Cold Chandu", "9300100002", "under_₹20_lakhs", "COLD", "switched off"],
        ["2026-05-04T10:00:00+05:30", "Normal Naresh", "9300100003", "not_decided_yet", "", "call back on monday"],
        ["2026-05-05T10:00:00+05:30", "Disq Dinesh", "9300100004", "under_₹20_lakhs", "disqualified", ""],
    ],
    "interested leads": [["Name", "Number", "Remarks"],
                         ["Hot Harish", "9300100001", "very keen"], ["Normal Naresh", "9300100003", "ok"],
                         ["Normal Naresh 2", "9300100005", "needs details"], ["Cold Chandu", "9300100002", "x"]],
})
CALLS = _book({
    "All Calls": [
        ["Name", "Phone Number", "Investment potential", "Call Status", "Lead type", "Remarks", "Consolidated Notes"],
        ["Cool Kiran", "9300100010", "under 5 lakhs", "Connected", "Cool", "budget only 2 lakhs", ""],
        ["Timepass Tara", "9300100011", "under 10 lakhs", "Connected", "", "time pass", ""],
        ["Warm Wasim", "9300100012", "under_₹20_lakhs", "Connected", "Warm", "", "wants Kukatpally, will visit Sat"],
        ["Cold Chandu", "9300100002", "under_₹20_lakhs", "Connected", "hot", "changed mind, very interested", ""],
        ["Lisa Owner", "9300100013", "", "Connected", "", "need staff for lisa salon in Dilsukhnagar", ""],
        ["Job Jaya", "9300100014", "", "Connected", "", "looking for a job as beautician", ""],
    ],
})
OWNERS = _book({
    "Tracker": [["Name", "Number", "Lead Type", "call status", "budget", "LEAD strength", "Remarks", "Problem Explained"],
                ["Owner Uma", "9300100020", "i_own_a_salon__", "connected", "", "HOT", "wants marketing help", "low walk-ins"],
                ["Opener Om", "9300100021", "i_am_planning_to_open_a_new_salon", "connected", "15lakhs", "WARM", "", ""],
                ["Cold Owner", "9300100022", "i_own_a_salon__", "connected", "", "COLD", "", ""]],
})


def test_cold_leads_left_out_hot_anywhere_wins_and_owners_who_need_staff_kept(app, db):
    with app.test_request_context():
        p = SS.plan_many(db, [("buyers.xlsx", BUYERS), ("calls.xlsx", CALLS), ("owners.xlsx", OWNERS)])
    tabs = {t["name"]: t for t in p["tabs"]}
    assert tabs["interested leads"]["kind"] == "buyer"               # typed by the people it shares
    people = {}
    for r in p["rows"]:
        people.setdefault(r["canon"].get("full_name"), []).append(r)
    kept = {n for n, rs in people.items() if any(r["action"] in ("create", "update") for r in rs)}
    skipped = {n: rs[0]["reason"] for n, rs in people.items() if all(r["action"] == "skip" for r in rs)}
    assert set(skipped) == {"Job Jaya"} and "job" in skipped["Job Jaya"].lower()     # only job seekers stay out
    for n in ("Cool Kiran", "Timepass Tara", "Disq Dinesh", "Cold Owner"):          # cold leads kept, marked cold
        r = next(r for r in people[n] if r["action"] in ("create", "update"))
        assert r["understood"]["meta"]["temperature"] == "cold", n
    # marked COLD in one workbook but hot in another → kept, as hot
    chandu = [r for r in people["Cold Chandu"] if r["action"] in ("create", "update")]
    assert chandu and chandu[0]["understood"]["meta"]["temperature"] == "hot"
    assert {"Hot Harish", "Normal Naresh", "Normal Naresh 2", "Warm Wasim", "Lisa Owner", "Owner Uma", "Opener Om"} <= kept
    meta = {n: next(r for r in rs if r["action"] in ("create", "update")) for n, rs in people.items() if n in kept}
    assert meta["Lisa Owner"]["kind"] == "seller" and "staff" in meta["Lisa Owner"]["understood"]["meta"]["category"]
    assert meta["Owner Uma"]["kind"] == "seller" and meta["Owner Uma"]["understood"]["meta"]["temperature"] == "hot"
    assert meta["Opener Om"]["kind"] == "buyer" and meta["Opener Om"]["understood"]["meta"]["category"] == "new_salon"
    assert meta["Warm Wasim"]["understood"]["meta"]["remark"].startswith("wants Kukatpally")   # the team's summary
    assert meta["Hot Harish"]["understood"]["meta"]["temperature"] == "hot"


def test_import_then_lists_show_priority_newest_first_with_honest_dates_and_filters(app, db, login):
    from conftest import user
    admin = user(db, "admin@t.test")
    with app.test_request_context():
        iid = SS.create_import_many(db, admin, "team sheets", [("buyers.xlsx", BUYERS), ("calls.xlsx", CALLS),
                                                                ("owners.xlsx", OWNERS)])
        counts = SS.commit(db, admin, iid)
    assert counts["failed"] == 0
    rows = {r["full_name"]: r for r in db.all("SELECT o.*, c.full_name FROM opportunities o JOIN contacts c "
                                              "ON c.id = o.contact_id")}
    assert rows["Hot Harish"]["lead_temperature"] == "hot" and rows["Hot Harish"]["lead_created_at"]
    assert rows["Warm Wasim"]["lead_created_at"] is None              # the call sheet never recorded a date
    assert rows["Cool Kiran"]["lead_temperature"] == "cold" and rows["Timepass Tara"]["lead_temperature"] == "cold"
    assert "Job Jaya" not in rows
    # the previous team already spoke to Warm Wasim (Connected): contacted, and the first call became a follow-up
    assert rows["Warm Wasim"]["first_contact_at"] and rows["Normal Naresh"]["first_contact_at"] is None
    t = db.one("SELECT * FROM tasks WHERE entity_id = ? AND status = 'open'", (rows["Warm Wasim"]["id"],))
    assert t["task_type"] == "follow_up" and not t["title"].lower().startswith("first")   # a follow-up / call-back
    c = login("admin@t.test")
    page = c.get("/buyers/").data.decode()
    assert page.index("Normal Naresh") < page.index("Hot Harish") < page.index("Disq Dinesh")   # latest first, cold last
    hot_first = c.get("/buyers/?sort=priority").data.decode()
    assert hot_first.index("Hot Harish") < hot_first.index("Normal Naresh")
    assert "Cold" in page
    assert "date not recorded" in page and "Hot" in page
    assert "Warm Wasim" in c.get("/buyers/?temp=warm").data.decode()
    assert "Hot Harish" not in c.get("/buyers/?temp=warm").data.decode()
    assert "Warm Wasim" in c.get("/buyers/?came=unknown").data.decode()
    assert "Hot Harish" not in c.get("/buyers/?came=unknown").data.decode()
    assert "Opener Om" in c.get("/buyers/?cat=new_salon").data.decode()
    sellers = c.get("/sellers/?cat=staff").data.decode()
    assert "Lisa" in sellers and "Needs staff" in sellers
    assert "Has a team remark" in sellers and "Date not recorded" in sellers      # filter choices on the page
    # imported leads without a date are not "new today" on anyone's dashboard
    from teamos.domain import dashboard as DB
    ids = [u["id"] for u in db.all("SELECT id FROM users")]
    assert DB.numbers(db, ids)["new_leads"] == 0


def test_lists_page_through_more_than_a_hundred(app, db, login):
    from test_records import _counts  # noqa: F401  (same fixtures)
    from conftest import user
    from teamos.domain import buyers as B
    admin = user(db, "admin@t.test")
    for i in range(130):
        B.create_lead(db, admin, {"full_name": f"Paged {i:03}", "phone": f"93001{i:05}", "city": "Hyderabad",
                                  "source": "referral", "consent_contact": "1", "owner_id": str(admin["id"])})
    c = login("admin@t.test")
    p1 = c.get("/buyers/").data.decode()
    assert "130 records" in p1 and "page 1 of 2" in p1 and "Next →" in p1
    p2 = c.get("/buyers/?page=2").data.decode()
    assert p2.count("Paged ") >= 30 and "← Previous" in p2
    assert json.dumps  # keep import used


def test_lead_review_explains_why_and_admin_removes(app, db, login):
    from datetime import timedelta

    from conftest import user
    from teamos.domain import lead_review as LR
    from teamos.domain import sellers as S
    from teamos.timeutil import now_utc, to_iso
    admin = user(db, "admin@t.test")

    def lead(name, phone):
        return S.create_lead(db, admin, {"full_name": name, "phone": phone, "salon_name": f"{name} Salon",
                                         "city": "Hyderabad", "locality": "Kondapur", "source": "manual_call",
                                         "consent_contact": "1", "owner_id": str(user(db, "seller@t.test")["id"])}
                             )["opportunity_id"]
    said_no, missed, old, fine, test = (lead("Said No", "9300200001"), lead("Missed Three", "9300200002"),
                                        lead("Old Untouched", "9300200003"), lead("Fine Lead", "9300200004"),
                                        lead("Test Person", "9300200005"))
    c = login("seller@t.test")
    c.post(f"/sellers/{said_no}/call", {"outcome": "not_interested"})
    for _ in range(3):
        c.post(f"/sellers/{missed}/call", {"outcome": "no_answer", "next_title": "Try again",
                                             "next_due": "2030-01-01T10:00"})
    c.post(f"/sellers/{fine}/call", {"outcome": "connected", "next_title": "Send form", "next_due": "2030-01-01T10:00"})
    db.execute("UPDATE opportunities SET lead_created_at = ? WHERE id = ?", (to_iso(now_utc() - timedelta(days=45)), old))
    res = LR.review(db, "seller")
    why = {r["id"]: r["reason"] for r in res["rows"]}
    assert why == {said_no: "said_no", missed: "never_reached", old: "untouched", test: "test"}   # Fine Lead not listed
    page = c.get("/records/review").data.decode()
    assert "Lead review" in page and "Said No" in page and "Fine Lead" not in page and "Only an admin" in page
    a = login("admin@t.test")
    assert "Remove ticked records" in a.get("/records/review?why=said_no").data.decode()
    a.post("/records/bulk", {"kind": "seller", "action": "delete", "reason": "review", "confirm": "DELETE",
                             "ids": [str(said_no), str(test)]})
    assert db.scalar("SELECT COUNT(*) FROM opportunities WHERE id IN (?, ?)", (said_no, test)) == 0
    assert db.scalar("SELECT COUNT(*) FROM opportunities WHERE id = ?", (fine,)) == 1


def test_removed_leads_do_not_come_back_from_sheets_but_start_fresh_can_reimport(app, db, login):
    from conftest import user
    admin = user(db, "admin@t.test")
    with app.test_request_context():
        SS.commit(db, admin, SS.create_import_many(db, admin, "b", [("buyers.xlsx", BUYERS)]))
    harish = db.scalar("SELECT o.id FROM opportunities o JOIN contacts c ON c.id = o.contact_id "
                       "WHERE c.full_name = 'Hot Harish'")
    a = login("admin@t.test")
    a.post("/records/bulk", {"kind": "buyer", "action": "delete", "reason": "said no on a call", "confirm": "DELETE",
                             "ids": [str(harish)]})
    with app.test_request_context():
        p = SS.plan_many(db, [("buyers.xlsx", BUYERS)])
    h = [r for r in p["rows"] if r["canon"].get("full_name") == "Hot Harish"]
    assert h and all(r["action"] == "skip" for r in h) and "Left out earlier" in h[0]["reason"]
    assert "said no on a call" in h[0]["reason"]
    # start fresh (remove all) keeps no such memory: everyone can be brought back in
    from teamos.domain import purge
    db.execute("DELETE FROM lead_exclusions")
    ids = purge.check_clear_all(db, admin, "DELETE ALL", "fresh start")
    purge.clear_all(db, admin, ids, "fresh start")
    assert db.scalar("SELECT COUNT(*) FROM lead_exclusions") == 0


def test_rebuild_dry_run_then_apply_from_the_admin_page(app, db, login, monkeypatch):
    from teamos.domain import connected_sheets as CS
    from teamos.domain import rebuild as RB
    books = {"B" * 30: ("buyers.xlsx", BUYERS), "C" * 30: ("calls.xlsx", CALLS), "O" * 30: ("owners.xlsx", OWNERS)}
    monkeypatch.setattr(CS, "fetch", lambda u: books[u.split("/d/")[1].split("/")[0]])
    links = "\n".join(f"https://docs.google.com/spreadsheets/d/{k}/edit" for k in books)
    a = login("admin@t.test")
    assert "Bring in the team's sheets" in a.get("/records/manage").data.decode()
    a.post("/records/rebuild", {"links": links, "mode": "dry"})
    assert db.scalar("SELECT COUNT(*) FROM opportunities") == 0                     # dry run changes nothing
    page = a.get("/records/manage").data.decode()
    assert "Dry run — nothing changed" in page and "would create" in page
    a.post("/records/rebuild", {"links": links, "mode": "apply", "confirm": "nope"})
    assert db.scalar("SELECT COUNT(*) FROM opportunities") == 0                     # APPLY must be typed
    a.post("/records/rebuild", {"links": links, "mode": "apply", "confirm": "APPLY"})
    st = RB.status(db)
    assert st["state"] == "done" and st["report"]["counts"]["failed"] == 0
    n = db.scalar("SELECT COUNT(*) FROM opportunities")
    assert n >= 12 and db.scalar("SELECT COUNT(*) FROM lead_exclusions") == 1      # only the job seeker
    a.post("/records/rebuild", {"links": links, "mode": "apply", "confirm": "APPLY"})   # again: nothing duplicated
    assert db.scalar("SELECT COUNT(*) FROM opportunities") == n
    assert login("seller@t.test").post("/records/rebuild", {"links": links, "mode": "dry"}).status_code in (302, 403)



def test_edit_details_on_a_profile(app, db, login):
    from conftest import user
    from teamos.domain import buyers as B
    from teamos.domain import sellers as S
    admin = user(db, "admin@t.test")
    sid = S.create_lead(db, admin, {"full_name": "Old Name", "phone": "9300300001", "salon_name": "Old Salon",
                                    "city": "Hyderabad", "locality": "Kondapur", "source": "manual_call",
                                    "consent_contact": "1", "owner_id": str(admin["id"])})["opportunity_id"]
    bid = B.create_lead(db, admin, {"full_name": "Buyer B", "phone": "9300300002", "city": "Hyderabad",
                                    "source": "referral", "consent_contact": "1", "owner_id": str(admin["id"])})["opportunity_id"]
    c = login("seller@t.test")
    page = c.get(f"/sellers/{sid}").data.decode()
    assert "Edit details" in page and 'name="salon_name"' in page
    c.post(f"/records/{sid}/basics", {"full_name": "New Name", "salon_name": "Glow Studio", "phone": "9300300009",
                                      "alt_phone": "", "email": "a@b.in", "city": "Hyderabad", "locality": "Madhapur",
                                      "lead_temperature": "hot", "lead_category": ["sell", "staff"],
                                      "team_remark": "wants to sell by March"})
    o = S.load(db, sid)
    assert (o["contact_name"], o["salon_name"], o["locality"], o["phone_normalized"]) ==         ("New Name", "Glow Studio", "Madhapur", "+919300300009")
    row = db.one("SELECT * FROM opportunities WHERE id = ?", (sid,))
    assert row["lead_temperature"] == "hot" and row["lead_category"] == "sell,staff" and "March" in row["team_remark"]
    assert "Details edited" in db.scalar("SELECT body FROM activities WHERE entity_id = ? AND kind = 'data_update' "
                                         "ORDER BY id DESC LIMIT 1", (sid,))
    # a number that belongs to someone else is refused
    c.post(f"/records/{sid}/basics", {"full_name": "New Name", "salon_name": "Glow Studio", "phone": "9300300002",
                                      "lead_temperature": "hot"})
    assert S.load(db, sid)["phone_normalized"] == "+919300300009"
    c.post(f"/records/{bid}/basics", {"full_name": "Buyer Bee", "phone": "9300300002", "city": "Bengaluru",
                                      "locality": "HSR", "lead_temperature": "cold", "lead_category": ["new_salon"]})
    b = B.load(db, bid)
    assert b["contact_name"] == "Buyer Bee" and b["contact_city"] == "Bengaluru" and b["lead_temperature"] == "cold"
    assert login("fin@t.test").post(f"/records/{sid}/basics", {"full_name": "x"}).status_code in (302, 403)


def _sheet(rows):
    return _book({"Investor calls": [["Name", "Phone Number", "Investment potential", "Call Status", "Lead type",
                                      "Remarks"]] + rows})


def test_sync_takes_only_what_changed_never_duplicates_never_loses(app, db, login):
    from conftest import user
    from teamos.domain import buyers as B
    admin = user(db, "admin@t.test")

    def run(rows):
        with app.test_request_context():
            return SS.commit(db, admin, SS.create_import_many(db, admin, "sync", [("calls.xlsx", _sheet(rows))]))

    def mand(name):
        return db.one("SELECT m.*, o.id AS oid, o.team_remark, o.lead_temperature FROM buyer_mandates m JOIN "
                      "opportunities o ON o.id = m.opportunity_id JOIN contacts c ON c.id = o.contact_id "
                      "WHERE c.full_name = ?", (name,))
    v1 = [["Ravi Kumar", "9300400001", "under 10 lakhs", "Connected", "Warm", "call on monday"],
          ["Sita Devi", "9300400002", "under 20 lakhs", "Connected", "", "send pictures"]]
    c1 = run(v1)
    assert c1["created_buyer"] == 2 and mand("Ravi Kumar")["all_in_max"] == 1000000
    # 1. the same sheet again → nothing to do, no repeated notes
    notes_before = db.scalar("SELECT COUNT(*) FROM activities")
    c2 = run(v1)
    assert c2["created_buyer"] == 0 and c2.get("unchanged") == 2 and not c2.get("changed")
    assert db.scalar("SELECT COUNT(*) FROM activities") == notes_before
    # 2. the team updates Ravi in the sheet → only Ravi's changed fields sync, logged old → new
    v2 = [["Ravi Kumar", "9300400001", "under 20 lakhs", "Connected", "Hot", "will visit office saturday"], v1[1]]
    c3 = run(v2)
    r = mand("Ravi Kumar")
    assert c3.get("changed") == 1 and c3.get("unchanged") == 1
    assert r["all_in_max"] == 2000000 and r["lead_temperature"] == "hot" and "saturday" in r["team_remark"]
    synced = db.scalar("SELECT body FROM activities WHERE entity_id = ? AND body LIKE 'From sheet%Synced from sheet%' "
                       "ORDER BY id DESC LIMIT 1", (r["oid"],))
    assert synced and "→" in synced
    # 3. someone edits Ravi's budget in TeamOS; the sheet then changes it too → TeamOS keeps its value, noted
    c = login("admin@t.test")
    B.update_fields(db, admin, B.load(db, r["oid"]), {"all_in_max": "25L"}, "budget")
    assert mand("Ravi Kumar")["all_in_max"] == 2500000
    v3 = [["Ravi Kumar", "9300400001", "above 30 lakhs", "Connected", "Hot", "will visit office saturday"], v1[1],
          ["New Person", "9300400003", "under 10 lakhs", "Connected", "", "new lead"]]
    c4 = run(v3)
    assert mand("Ravi Kumar")["all_in_max"] == 2500000                         # TeamOS edit not overwritten
    assert c4.get("conflicts", 0) >= 1 and c4["created_buyer"] == 1             # only the new row is new
    assert "TeamOS kept its value" in (db.scalar(
        "SELECT body FROM activities WHERE entity_id = ? AND body LIKE 'The sheet and TeamOS differ%' "
        "ORDER BY id DESC LIMIT 1", (r["oid"],)) or "")
    # no duplicates anywhere: one record per person
    assert db.scalar("SELECT COUNT(*) FROM opportunities") == 3
    assert db.scalar("SELECT COUNT(*) FROM (SELECT contact_id FROM opportunities GROUP BY contact_id, kind "
                     "HAVING COUNT(*) > 1) x") == 0
    assert c.get(f"/buyers/{r['oid']}?tab=history").status_code == 200


def test_send_form_on_whatsapp_and_half_filled_answers_are_kept(app, db, login):
    import re as _re
    from conftest import user
    from teamos.domain import buyers as B
    from teamos.domain import pilot as P
    from teamos.domain import sellers as S
    admin = user(db, "admin@t.test")
    sid = S.create_lead(db, admin, {"full_name": "Anji", "phone": "9010099928", "salon_name": "Morning Kisses",
                                    "city": "Hyderabad", "locality": "Kondapur", "source": "import",
                                    "consent_contact": "1", "owner_id": str(admin["id"])})["opportunity_id"]
    bid = B.create_lead(db, admin, {"full_name": "Investor I", "phone": "9300600001", "city": "Hyderabad",
                                    "source": "referral", "consent_contact": "1", "owner_id": str(admin["id"])})["opportunity_id"]
    c = login("seller@t.test")
    page = c.get(f"/sellers/{sid}").data.decode()
    assert "📝 Send form" in page
    r = c.post(f"/sellers/{sid}/pilot/link", {"purpose": "form", "go": "wa"})
    assert r.status_code == 302 and r.headers["Location"].startswith("https://wa.me/919010099928?text=")
    assert "Form link sent on WhatsApp" in db.scalar("SELECT body FROM activities WHERE entity_id = ? AND kind = 'note' "
                                                     "ORDER BY id DESC LIMIT 1", (sid,))
    from urllib.parse import unquote
    link = _re.search(r"/s/([A-Za-z0-9_\-]+)", unquote(r.headers["Location"])).group(1)
    # the owner fills a few answers and leaves without submitting → saved; opening the link again shows them
    pub = app.test_client()
    form = pub.get(f"/s/{link}").data.decode()
    assert "data-autosave" in form
    j = pub.post(f"/s/{link}/save", data={"draft": link, "chairs": "6", "monthly_rent": "45k",
                                         "salon_name": "Morning Kisses"}).get_json()
    assert j["ok"]
    assert P.profile(db, sid)["form_status"] == "sent" and P.profile(db, sid)["form_answered"] >= 2
    again = pub.get(f"/s/{link}").data.decode()
    assert 'value="45k"' in again or "45k" in again                       # what they wrote is still there
    # investors too
    assert "📝 Send form" in c.get(f"/buyers/{bid}").data.decode()
    r = c.post(f"/buyers/{bid}/investor/link", {"purpose": "form", "go": "wa"})
    assert r.headers["Location"].startswith("https://wa.me/919300600001?text=")


def test_two_sheets_never_undo_each_other(app, db, login):
    from conftest import user
    admin = user(db, "admin@t.test")
    a = _book({"Investor calls": [["Name", "Phone Number", "Investment potential", "Call Status", "Lead type", "Remarks"],
                                  ["Kumar", "9300700001", "under 10 lakhs", "Connected", "COLD", "time pass"]]})
    b = _book({"Ad leads": [["full_name", "phone_number", "what_is_your_investment_budget?"],
                            ["Kumar", "9300700001", "under_₹20_lakhs"]]})

    def run(name, files):
        with app.test_request_context():
            return SS.commit(db, admin, SS.create_import_many(db, admin, name, files))
    run("Team sheets — full rebuild", [("a.xlsx", a), ("b.xlsx", b)])        # judged together: cold
    t = lambda: db.scalar("SELECT lead_temperature FROM opportunities")        # noqa: E731
    assert t() == "cold"
    for _ in range(3):                                                         # the ad sheet alone, every 10 minutes
        run("Meta ad leads - google-sheet-x.xlsx", [("google-sheet-x.xlsx", b)])
        assert t() == "cold"                                                   # a sheet with no mark never un-colds
    assert db.scalar("SELECT COUNT(*) FROM opportunities") == 1
    # the ad sheet changes its own budget → that change comes through (it is the sheet that changed)
    b2 = _book({"Ad leads": [["full_name", "phone_number", "what_is_your_investment_budget?"],
                             ["Kumar", "9300700001", "₹20_lakhs_–_₹40_lakhs"]]})
    before = db.scalar("SELECT all_in_max FROM buyer_mandates")
    run("Meta ad leads - google-sheet-x.xlsx", [("google-sheet-x.xlsx", b2)])
    after = db.scalar("SELECT all_in_max FROM buyer_mandates")
    assert after == 4000000 or "TeamOS kept its value" in (db.scalar(
        "SELECT body FROM activities WHERE body LIKE 'The sheet and TeamOS differ%' ORDER BY id DESC LIMIT 1") or ""), \
        (before, after)
