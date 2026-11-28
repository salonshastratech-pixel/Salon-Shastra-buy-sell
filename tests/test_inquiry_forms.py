"""Academy and Recruitment (job) online forms: the same flow as the Pilot / Investor forms - saved while typing, the
same person never a second record, an expired page keeps every answer, the team gets it in turn with an alert, and
staff can share the links or send the form to one person on WhatsApp."""
import re
from urllib.parse import unquote

from conftest import user

from teamos.domain import inquiries as INQ


def _token(c, path):
    return re.search(r'name="form_token" value="([^"]+)"', c.get(path).data.decode()).group(1)


def test_both_forms_open_with_the_right_questions(app):
    c = app.test_client()
    for path, words in (("/f/academy", "Courses you are interested in"), ("/f/job", "Role(s) you want"),
                        ("/f/recruitment", "Role(s) you want")):
        r = c.get(path)
        page = r.data.decode()
        assert r.status_code == 200 and words in page and 'name="phone"' in page and "data-autosave" in page, path
        assert "they" not in page.split("<form", 1)[1].split("</form>", 1)[0].lower().replace("they'", ""), path


def test_saved_while_typing_then_submitted_is_one_inquiry_given_to_the_team(app, db):
    c = app.test_client()
    tok = _token(c, "/f/academy")
    base = {"form_token": tok, "full_name": "Priya", "phone": "9300550001", "courses": ["Makeup artistry"]}
    j = c.post("/f/academy/save", data=base).json
    assert j["ok"] and j["draft"]
    j2 = c.post("/f/academy/save", data=dict(base, draft=j["draft"], level="Complete beginner")).json
    for _ in range(2):                                  # saves whose answer never reached the page (switched apps)
        c.post("/f/academy/save", data=dict(base, join_when="This month"))
    r = c.post("/f/academy", data=dict(base, draft=j2["draft"], budget="₹25,000–50,000", consent_contact="1",
                                        fee_approach="Real learning — flexible on fee"))
    assert r.status_code == 302 and r.headers["Location"].endswith("/f/academy/done")
    rows = db.all("SELECT * FROM inquiries WHERE phone_normalized = '+919300550001'")
    assert len(rows) == 1
    inq = INQ.load(db, rows[0]["id"])
    assert inq["kind"] == "academy" and inq["source"] == "online_form" and inq["owner_id"]
    assert inq["a"]["level"] == "Complete beginner" and inq["a"]["courses"] == ["Makeup artistry"]
    assert inq["a"]["fee_approach"] == "Real learning — flexible on fee" and inq["a"]["join_when"] == "This month"
    assert db.scalar("SELECT COUNT(*) FROM tasks WHERE entity_type = 'inquiry' AND entity_id = ? AND status = 'open'",
                     (inq["id"],)) == 1
    assert db.scalar("SELECT COUNT(*) FROM notifications WHERE user_id = ? AND title LIKE 'New academy enquiry%'",
                     (inq["owner_id"],)) == 1
    assert "Thank you" in c.get("/f/academy/done").data.decode()


def test_same_mobile_adds_to_the_teams_inquiry_and_overwrites_nothing(app, db):
    admin = user(db, "admin@t.test")
    iid = INQ.create(db, admin, "job", {"full_name": "Ravi Stylist", "phone": "9300550002", "source": "incoming_call",
                                         "expected_salary": "20000", "owner_id": str(user(db, "seller@t.test")["id"])})
    c = app.test_client()
    r = c.post("/f/job", data={"form_token": _token(c, "/f/job"), "full_name": "Ravi K", "phone": "9300550002",
                               "expected_salary": "35000", "experience": "3–5 years", "consent_contact": "1"})
    assert r.status_code == 302
    assert db.scalar("SELECT COUNT(*) FROM inquiries WHERE kind = 'job'") == 1
    inq = INQ.load(db, iid)
    assert inq["full_name"] == "Ravi Stylist" and inq["a"]["expected_salary"] == 20000    # the team's answer stays
    assert inq["a"]["experience"] == "3–5 years"                                        # a blank one is filled
    assert "added to this inquiry" in db.scalar("SELECT body FROM activities WHERE entity_type = 'inquiry' AND "
                                                "entity_id = ? ORDER BY id DESC", (iid,))


def test_expired_page_keeps_answers_and_bad_mobile_is_explained(app, db):
    c = app.test_client()
    r = c.post("/f/job", data={"form_token": "old", "full_name": "Sana", "phone": "9300550003",
                               "work_areas": "Kukatpally, KPHB"})
    page = r.data.decode()
    assert r.status_code == 200 and "Your answers are kept" in page and "Kukatpally, KPHB" in page and "Sana" in page
    r = c.post("/f/job", data={"form_token": _token(c, "/f/job"), "full_name": "Sana", "phone": "12345",
                               "consent_contact": "1"})
    assert r.status_code == 200 and db.scalar("SELECT COUNT(*) FROM inquiries") == 0


def test_staff_share_links_and_send_the_form_to_one_person(db, login):
    admin = user(db, "admin@t.test")
    iid = INQ.create(db, admin, "academy", {"full_name": "Meena", "phone": "9300550004", "source": "incoming_call"})
    c = login("seller@t.test")
    page = c.get("/others/").data.decode()
    assert "/f/academy" in page and "/f/job" in page and "Forms to share" in page
    page = c.get(f"/others/{iid}").data.decode()
    assert f'action="/others/{iid}/send-form"' in page and "/f/academy</code>" in page
    r = c.post(f"/others/{iid}/send-form", {})
    loc = unquote(r.headers["Location"])
    assert r.status_code == 302 and loc.startswith("https://wa.me/919300550004?text=Hello Meena") and "/f/academy" in loc
    assert "Sent the Academy form on WhatsApp" in c.get(f"/others/{iid}").data.decode()
    assert "/f/job" in c.get("/forms/").data.decode()
