"""Reading the team's remarks (English / Telugu / Telugu in English letters) and acting on them during sheet imports.
All data here is made up."""
import io
import json
from datetime import datetime

from openpyxl import Workbook

from conftest import user

from teamos.domain import pilot as P
from teamos.domain import remarks as R
from teamos.timeutil import IST

NOW = datetime(2026, 11, 24, 11, 0, tzinfo=IST)  # Tuesday 11 am


def r(text, db=None):
    return R.read(text, db, NOW)


def test_tenglish_telugu_and_english_remarks():
    x = r("busy ga unnaru, repu evening call cheyandi")
    assert x["outcome"] == "busy" and x["callback_at"] == "2026-11-25T12:30:00Z"
    assert x["language"] == "Telugu in English letters"
    assert r("lift cheyaledu")["outcome"] == "no_answer"
    assert r("interest ledu")["outcome"] == "not_interested"
    assert r("not intrested")["outcome"] == "not_interested"
    s = r("salon ammali anukuntunnaru 15L ki")
    assert "wants_sell" in s["meanings"] and s["amount"] == 1_500_000
    n = r("staff problem take vikas anna in conference")
    assert set(n["needs"]) >= {"need_staff", "talk_vikas"}
    t = r("రేపు సాయంత్రం కాల్ చేయండి")
    assert t["language"] == "Telugu" and t["outcome"] == "callback" and t["callback_at"] == "2026-11-25T12:30:00Z"
    assert r("ఆసక్తి లేదు")["outcome"] == "not_interested"
    m = r("monday office ki vastanu annaru")
    assert "office_visit" in m["meanings"] and m["callback_at"] == "2026-11-30T04:30:00Z"  # Monday 10:00 IST
    assert r("busy in meetings he will call after 5pm")["callback_at"] == "2026-11-24T11:30:00Z"
    assert r("he will call in 10mins")["outcome"] == "they_will_call"
    assert r("wrong number")["outcome"] == "wrong_number"
    assert not r("asdf qwerty")["understood"]


def test_not_interested_beats_interested():
    x = r("interested ani cheppi, later not interested annaru")
    assert x["outcome"] == "not_interested" and "interested" not in x["meanings"] and x["interest"] == "cold"


def test_team_teaches_a_phrase(db, login):
    assert not r("nenu chusi chepta", db)["understood"]
    lead = login("lead@t.test")
    lead.post("/imports/remarks", {"phrase": "nenu chusi chepta", "meaning": "thinking"})
    assert r("Nenu chusi chepta annaru", db)["meanings"].get("thinking")
    page = lead.get("/imports/remarks?t=nenu+chusi+chepta").data.decode()
    assert "Still thinking" in page and "nenu chusi chepta" in page
    assert login("seller@t.test").post("/imports/remarks", {"phrase": "abc def", "meaning": "busy"}).status_code == 403


PILOT_HDR = [q["label"] for q in P.QUESTIONS if not q["extra"]] + ["Remarks", "Column 1 2", "software ",
                                                                    "business Consulting ", "marketing ", "staff"]


def _pilot_row(name, phone, remark, col12="", software="", consulting="", marketing="", staff=""):
    a = {"salon_name": f"{name} Salon", "owner_name": name, "location": "Kondapur", "salon_type": "Unisex Salon",
         "contact_number": phone, "email": f"{name.lower()}@example.com", "area_sft": "900", "floor": "1st floor",
         "chairs": "6.0", "beds": "1.0", "pedicure_stations": "1.0", "hair_dressers": "3.0", "beauticians": "2.0",
         "manager": "No", "housekeeping": "Yes", "monthly_revenue": "1.8L", "monthly_expenses": "1.5L",
         "monthly_rent": "45k", "price_women_cut": "500", "price_men_cut": "200", "digital_marketing_spend": "No",
         "billing_software": "No", "stand_out": "Highly skilled staff, Unique services/treatments",
         "service_rating": "4.0", "customers_love": "Service", "training_program": "No",
         "marketing_worked": "Word of mouth", "staff_struggle": "Yes",
         "op_challenges": "Staff turnover & inconsistent service, Financial issues (pricing, cash flow, profitability)",
         "online_presence": "3.0", "track_financials": "No", "complaints": "Waiting time",
         "growth_opps": "Improving digital marketing & social media presence", "extra_revenue": "No",
         "digital_ads": "No", "loyalty": "No", "growth_strategies": "Marketing",
         "threats": "New competitors in the market", "price_competition": "Yes", "revenue_fluctuation": "Yes",
         "external_factors": "Competition", "want_report": "Yes", "pilot_interest": "Yes", "willing_invest": "Yes",
         "comments": "Need help"}
    return [a[q["key"]] for q in P.QUESTIONS if not q["extra"]] + [remark, col12, software, consulting, marketing,
                                                                     staff]


def test_pilot_sheet_import_reads_remarks_ticks_and_answers(app, db, login):
    wb = Workbook()
    ws = wb.active
    ws.title = "Raw Responses"
    ws.append(PILOT_HDR)
    ws.append(_pilot_row("Ravi", "9000300001", "busy ga unnaru, repu evening call cheyandi", software="yes"))
    ws.append(_pilot_row("Sita", "9000300002", "staff problem take vikas anna in conference", staff="✓",
                         marketing="yes"))
    ws.append(_pilot_row("Gopal", "9000300003", "interest ledu"))
    buf = io.BytesIO()
    wb.save(buf)
    lead = login("lead@t.test")
    r_ = lead.post("/imports/smart", {"file": (io.BytesIO(buf.getvalue()), "Pilot Program (Responses).xlsx")},
                   content_type="multipart/form-data")
    assert r_.status_code == 302, r_.data[:300]
    iid = int(r_.headers["Location"].rstrip("/").split("/")[-1].split("?")[0])
    preview = lead.get(f"/imports/smart/{iid}?show=all").data.decode()
    assert "Telugu in English letters" in preview and "Busy — call again" in preview
    lead.post(f"/imports/smart/{iid}/commit")
    counts = json.loads(db.scalar("SELECT counts FROM sheet_imports WHERE id = ?", (iid,)))
    assert counts["created_seller"] == 3 and counts.get("understood", 0) >= 3

    def opp(phone):
        return db.one("SELECT o.* FROM opportunities o JOIN contacts c ON c.id = o.contact_id "
                      "WHERE c.phone_normalized = ?", ("+91" + phone,))
    ravi, sita, gopal = opp("9000300001"), opp("9000300002"), opp("9000300003")
    # every pilot answer imported → form filled + SWOT draft
    for o in (ravi, sita, gopal):
        assert P.profile(db, o["id"])["form_status"] == "filled"
        assert P.latest_report(db, o["id"])["status"] == "draft"
    assert len(P.latest_response(db, ravi["id"])["answers"]) == 45
    # tick columns + remarks → help areas
    assert "chairpe" in P.profile(db, ravi["id"])["help_areas"]
    assert set(P.profile(db, sita["id"])["help_areas"]) >= {"staff_hiring", "digital_marketing", "vikas_consult"}
    # the next call follows the remark
    t = db.one("SELECT * FROM tasks WHERE entity_id = ? AND status = 'open' ORDER BY due_at LIMIT 1", (ravi["id"],))
    assert t["title"].startswith("Call back — they asked (tomorrow 6:00 pm)") and t["priority"] == "high"
    tg = db.one("SELECT * FROM tasks WHERE entity_id = ? AND status = 'open' ORDER BY due_at LIMIT 1", (gopal["id"],))
    assert "not interested" in tg["title"] and tg["priority"] == "low"
    note = db.scalar("SELECT body FROM activities WHERE entity_id = ? AND body LIKE 'TeamOS read the remarks%'",
                     (sita["id"],))
    assert "Needs staff" in note and "Wants to talk to Vikas" in note and "staff problem" in note
    assert db.scalar("SELECT COUNT(*) FROM notifications WHERE kind = 'pilot_form'") == 0  # imports don't spam alerts


def test_team_status_words_and_spellings():
    """Phrasing seen in the team's own sheets (made-up rows, real wording patterns)."""
    assert r("Not Connected | shared details on watsapp")["summary"] == "Didn't answer; Details shared with them"
    assert r("Connected | disconnecting my call")["outcome"] == "hung_up"
    assert r("call not respondig")["outcome"] == "no_answer" and r("coll noo respomdig")["outcome"] == "no_answer"
    assert r("Not Connected | number out of service")["outcome"] == "switched_off"
    assert r("Connected | very less budget")["meanings"].get("low_budget")
    assert "need_staff" in r("Connected | owner of a brand salon wants staff")["needs"]
    assert "need_staff" in r("ikkada evaro okaru undali okkarine vadhilesthe ibbandhi")["needs"]
    cold = r("CREATED | COLD | want to purchase in konaseema | COLD")
    assert cold["interest"] == "cold" and "wants_buy" in cold["meanings"]
    assert r("CREATED | HOT | HOT")["interest"] == "hot"
    assert r("WARM | Connected | WARM")["interest"] == "warm"
    assert r("disqualified")["outcome"] == "not_interested"
    assert r("has hotel business no want to invest in salon")["outcome"] == "not_interested"


def test_reread_keeps_existing_call_times(db):
    from teamos.domain import sellers as S
    from teamos.domain import sheet_sync as SS
    admin = user(db, "admin@t.test")
    oid = S.create_lead(db, admin, {"full_name": "Old Lead", "phone": "9000300099", "salon_name": "Old Salon",
                                    "city": "Hyderabad", "locality": "Ameerpet", "source": "import",
                                    "consent_contact": "1"})["opportunity_id"]
    before = db.one("SELECT title, due_at FROM tasks WHERE entity_id = ? AND status = 'open'", (oid,))
    done = SS.apply_understanding(db, admin, "seller", oid, {}, ["Remarks: repu evening call cheyandi, staff problem"],
                                  {}, plan_calls=False)
    after = db.one("SELECT title, due_at FROM tasks WHERE entity_id = ? AND status = 'open'", (oid,))
    assert after == before and any("help areas" in d for d in done)
