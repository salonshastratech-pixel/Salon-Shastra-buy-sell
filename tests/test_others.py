"""Others: job seekers (HR screening) and academy students (counsellor intake) who call the team."""
from datetime import timedelta

from conftest import user

from teamos.domain import inquiries as Q
from teamos.timeutil import now_utc


def _soon():
    return (now_utc() + timedelta(days=1)).strftime("%Y-%m-%dT11:00")


def test_a_job_seeker_call_is_screened_and_can_be_filtered(db, login):
    c = login("seller@t.test")
    page = c.get("/others/new?kind=job").data.decode()
    for q in ("Role(s) wanted", "Years of experience", "Expected salary", "Where they are from", "Needs accommodation",
              "Area where they live now", "Remarks"):
        assert q in page, q
    r = c.post("/others/new", {"kind": "job", "full_name": "ravi kumar", "phone": "98480 22334", "locality": "Kukatpally",
                               "city": "Hyderabad", "hometown": "Vijayawada", "roles": ["Men's barber / stylist", "Not a role"],
                               "experience": "3–5 years", "skills": ["Men's cuts & beard"], "expected_salary": "₹22,000",
                               "current_salary": "18000", "join_when": "Within 15 days", "stay_needed": "Yes",
                               "temperature": "hot", "notes": "Good with fades", "follow_up_due": _soon()})
    iid = db.scalar("SELECT id FROM inquiries")
    assert r.status_code == 302 and r.headers["Location"].endswith(f"/others/{iid}")
    inq = Q.load(db, iid)
    assert inq["ref_code"] == f"JB-{iid:05d}" and inq["phone_normalized"] == "+919848022334"
    assert inq["a"]["roles"] == ["Men's barber / stylist"]                   # unknown choices are dropped
    assert inq["a"]["expected_salary"] == 22000 and inq["budget_or_salary"] == "₹22,000"
    assert (inq["interest"], inq["experience"], inq["join_when"]) == ("Men's barber / stylist", "3–5 years", "Within 15 days")
    assert db.scalar("SELECT COUNT(*) FROM tasks WHERE entity_type = 'inquiry' AND entity_id = ? AND status = 'open'",
                     (iid,)) == 1
    detail = c.get(f"/others/{iid}").data.decode()
    assert "Ravi Kumar" in detail and "Vijayawada" in detail and "Needs accommodation?" in detail and "Hot" in detail
    lst = c.get("/others/?kind=job&role=Men%27s+barber+%2F+stylist").data.decode()
    assert "JB-" in lst and "Ravi Kumar" in lst and "from Vijayawada" in lst
    assert "Ravi Kumar" not in c.get("/others/?kind=job&role=Makeup+artist").data.decode()
    assert "Ravi Kumar" not in c.get("/others/?kind=academy").data.decode()
    # the same number again is caught, unless the team insists
    r = c.post("/others/new", {"kind": "job", "full_name": "Ravi", "phone": "9848022334"})
    assert r.status_code == 200 and "already has a job inquiry" in r.data.decode()
    c.post("/others/new", {"kind": "job", "full_name": "Ravi", "phone": "9848022334", "allow_duplicate": "1"})
    assert db.scalar("SELECT COUNT(*) FROM inquiries") == 2


def test_an_academy_call_records_course_timing_and_fee_approach(db, login):
    c = login("buyer@t.test")
    page = c.get("/others/new?kind=academy").data.decode()
    for q in ("Men&#39;s barbering", "Cosmetology diploma", "Nail art", "Bridal &amp; HD makeup", "Willing to join",
              "Lowest fee possible", "Real learning — flexible on fee", "EMI / instalments"):
        assert q in page, q
    c.post("/others/new", {"kind": "academy", "full_name": "Sneha", "phone": "9000011122", "hometown": "Nellore",
                           "courses": ["Makeup artistry", "Bridal & HD makeup"], "level": "Complete beginner",
                           "goal": ["Start own salon / studio"], "join_when": "Next month",
                           "fee_approach": "Real learning — flexible on fee", "budget": "₹50,000–1 lakh",
                           "support": ["EMI / instalments"]})
    inq = Q.load(db, db.scalar("SELECT id FROM inquiries"))
    assert inq["kind"] == "academy" and inq["ref_code"].startswith("AC-")
    assert inq["interest"] == "Makeup artistry, Bridal & HD makeup"
    assert inq["budget_or_salary"] == "Real learning — flexible on fee" and inq["join_when"] == "Next month"
    lst = c.get("/others/?kind=academy&fees=Real+learning+%E2%80%94+flexible+on+fee").data.decode()
    assert "Sneha" in lst
    assert "Sneha" not in c.get("/others/?kind=academy&fees=Lowest+fee+possible").data.decode()
    assert "Sneha" in c.get("/others/?kind=academy&q=nellore").data.decode()


def test_calls_status_and_follow_ups(db, login):
    admin = user(db, "admin@t.test")
    iid = Q.create(db, admin, "academy", {"full_name": "Kavya", "phone": "9000011133", "follow_up_due": _soon()})
    c = login("admin@t.test")
    assert c.post(f"/others/{iid}", {"note": "", "status": "new"}).status_code == 200   # nothing to save: explained
    c.post(f"/others/{iid}", {"note": "Explained fees", "status": "demo", "temperature": "warm", "was_call": "1"})
    inq = Q.load(db, iid)
    assert inq["status"] == "demo" and inq["temperature"] == "warm"
    assert "Explained fees" in Q.history(db, iid)[0]["body"]
    c.post(f"/others/{iid}", {"note": "Joined the weekend batch", "status": "enrolled"})
    assert Q.open_tasks(db, iid) == []                                           # finished: reminders closed
    assert "Kavya" not in c.get("/others/").data.decode()                      # open list
    assert "Kavya" in c.get("/others/?status=all").data.decode()
    # editing keeps everything and updates answers
    c.post(f"/others/{iid}", {"action": "edit", "full_name": "Kavya R", "phone": "9000011133", "courses": ["Nail art & extensions"]})
    assert Q.load(db, iid)["interest"] == "Nail art & extensions" and Q.load(db, iid)["full_name"] == "Kavya R"


def test_others_is_in_the_menu_and_finance_cannot_open_it(db, login):
    page = login("seller@t.test").get("/others/?kind=academy").data.decode()
    assert "Others" in page and "Academy inquiries" in page and "Job inquiries" in page
    assert login("fin@t.test").get("/others/").status_code == 403
