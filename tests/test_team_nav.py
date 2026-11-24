"""M12: navigation (Back, breadcrumbs, module tabs, connected records) and team availability / activity."""
import re
from datetime import timedelta

from conftest import user

from teamos.domain import availability as AV
from teamos.domain import progress as PR
from teamos.domain import sellers as S
from teamos.timeutil import IST, now_utc


def _seller(db, phone="9000011001"):
    admin = user(db, "admin@t.test")
    return S.create_lead(db, admin, {"full_name": "Kavya Reddy", "phone": phone, "salon_name": "Glow Studio",
                                     "city": "Hyderabad", "locality": "Kondapur", "source": "manual_call",
                                     "consent_contact": "1", "owner_id": str(user(db, "seller@t.test")["id"])}
                         )["opportunity_id"]


def test_every_staff_page_has_back_breadcrumb_and_module_tabs(db, login):
    opp = _seller(db)
    c = login("admin@t.test")
    page = c.get(f"/sellers/{opp}", headers={"Referer": "http://localhost/pilot"}).data.decode()
    assert 'class="pagebar"' in page and 'class="back" href="/pilot"' in page  # back to where you came from
    assert '<a href="/sellers/">Salon Owners</a>' in page and "Glow Studio" in page  # breadcrumb
    assert ">Pilot program</a>" in page and ">Vikas&#39;s free times</a>" in page  # module tabs
    no_ref = c.get(f"/sellers/{opp}").data.decode()
    assert 'class="back" href="/sellers/"' in no_ref  # no previous page → up to the list
    for url in ["/", "/tasks/", "/team", "/team/availability", "/team/activity", "/me/availability", "/sellers/",
                "/pilot", "/buyers/", "/matches/", "/deals/", "/inbox/", "/forms/", "/reports/", "/tools",
                "/admin/users", "/calendar", "/automations"]:
        r = c.get(url)
        assert r.status_code == 200, url
        html = r.data.decode()
        assert 'class="nav"' in html and 'class="pagebar"' in html, url
    my_day = c.get("/").data.decode()
    assert 'class="back"' not in my_day  # nowhere to go back to from home


def test_module_tabs_respect_permissions(db, login):
    member = login("seller@t.test").get("/tools").data.decode()
    assert ">Users</a>" not in member and ">Audit log</a>" not in member
    admin = login("admin@t.test").get("/tools").data.decode()
    assert ">Users</a>" in admin and ">Audit log</a>" in admin


def test_record_shows_connected_modules(db, login):
    opp = _seller(db)
    page = login("seller@t.test").get(f"/sellers/{opp}").data.decode()
    assert 'class="related"' in page and "Open tasks" in page


def test_status_hours_and_leave(db, login):
    c = login("seller@t.test")
    me = user(db, "seller@t.test")
    c.post("/me/status", {"status": "in_field", "note": "Kondapur visits", "next": "/tasks/"})
    u = user(db, "seller@t.test")
    assert u["work_status"] == "in_field" and u["work_status_note"] == "Kondapur visits"
    hours = {f"d{d}_start": "09:00" for d in range(7)}
    hours.update({f"d{d}_end": "18:00" for d in range(7)})
    hours["d6_off"] = "1"
    c.post("/me/availability", {**hours, "action": "hours"})
    assert AV.hours(db, me["id"])[0]["start_time"] == "09:00" and AV.hours(db, me["id"])[6]["is_off"]
    bad = dict(hours, d0_end="08:00")
    c.post("/me/availability", {**bad, "action": "hours"})
    assert AV.hours(db, me["id"])[0]["end_time"] == "18:00"  # refused: end before start
    today = now_utc().astimezone(IST).date()
    c.post("/me/availability", {"action": "time_off", "kind": "leave", "start": today.isoformat(),
                                "end": (today + timedelta(days=2)).isoformat(), "note": "Family function"})
    assert me["id"] in AV.away_ids(db)
    week = c.get("/team/availability").data.decode()
    assert "Leave" in week and "Family function" in week
    # nobody can edit someone else's availability unless they are a team lead
    other = user(db, "buyer@t.test")
    assert c.get(f"/me/availability?user={other['id']}").status_code == 403
    assert login("lead@t.test").get(f"/me/availability?user={other['id']}").status_code == 200


def test_leave_stops_new_leads(db):
    me = user(db, "seller@t.test")
    today = now_utc().astimezone(IST).date().isoformat()
    assert PR.pick_owner(db, "seller") == me["id"]
    AV.add_time_off(db, me, me["id"], today, today, "leave", None)
    assert PR.pick_owner(db, "seller") is None  # the only seller-team person is on leave
    w = {r["id"]: r for r in PR.workload(db)}
    assert w[me["id"]]["on_leave"] and w[me["id"]]["away"]


def test_field_day_is_not_leave(db):
    me = user(db, "seller@t.test")
    today = now_utc().astimezone(IST).date().isoformat()
    AV.add_time_off(db, me, me["id"], today, today, "field", "Salon visits")
    assert me["id"] not in AV.away_ids(db)


def test_activity_counts_calls_and_tasks(db, login):
    opp = _seller(db)
    c = login("seller@t.test")
    o = S.load(db, opp)
    S.log_call(db, user(db, "seller@t.test"), o, "connected", "Spoke to owner", None, None, None)
    a = AV.activity(db, "today")
    row = next(r for r in a["rows"] if r["id"] == user(db, "seller@t.test")["id"])
    assert row["calls"] == 1 and row["connected"] == 1 and row["first_at"]
    page = c.get("/team/activity?period=week").data.decode()
    assert "Team activity" in page and "Shaheen Seller" in page
    assert re.search(r"Calls logged · \d+ connected", page)
