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
    assert ">Users</a>" not in member  # D-85: team = everything except Finance
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
    other = user(db, "buyer@t.test")
    assert login("fin@t.test").get(f"/me/availability?user={other['id']}").status_code in (302, 403)
    assert login("lead@t.test").get(f"/me/availability?user={other['id']}").status_code == 200


def test_leave_stops_new_leads(db):
    me = user(db, "seller@t.test")
    today = now_utc().astimezone(IST).date().isoformat()
    assert PR.pick_owner(db, "seller") == me["id"]
    AV.add_time_off(db, me, me["id"], today, today, "leave", None)
    assert PR.pick_owner(db, "seller") == user(db, "lead@t.test")["id"]  # on leave → goes to a "Both" person
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


def test_rows_and_names_open_their_record(db, login):
    opp = _seller(db)
    c = login("seller@t.test")
    lst = c.get("/sellers/").data.decode()
    assert 'class="row-link"' in lst and lst.count(f'href="/sellers/{opp}"') >= 3      # ref, salon and contact name
    team = login("lead@t.test").get("/team").data.decode()
    assert "data-href=\"/sellers/?owner=" in team and "/tasks/?who=" in team
    me = user(db, "seller@t.test")
    assert c.get(f"/tasks/?who={me['id']}").status_code == 200
    js = c.get("/static/app.js").data.decode()
    assert "tr.row-link, li.task, .click-card" in js



def test_crm_timeline_lead_time_and_speed_to_first_contact(db, login):
    from teamos.domain import timeline as TL
    assert TL.lead_time("2026-11-22T01:15:43-05:00") == "2026-11-22T06:15:43Z"            # Meta ad time → UTC
    assert TL.lead_time("22/11/2026 10:30:00") == "2026-11-22T05:00:00Z"                  # Google Form, Indian time
    assert TL.lead_time("nonsense") is None
    opp = _seller(db)
    db.execute("UPDATE opportunities SET lead_created_at = ? WHERE id = ?", ("2026-11-20T04:30:00Z", opp))
    c = login("seller@t.test")
    page = c.get(f"/sellers/{opp}").data.decode()
    assert "Timeline" in page and "Lead came in" in page and "Not contacted yet" in page
    assert "not contacted" in c.get("/sellers/").data.decode()
    c.post(f"/sellers/{opp}/call", {"outcome": "connected", "note": "Spoke about the pilot",
                                     "next_title": "Send form", "next_due": "2030-01-01T10:00"})
    o = db.one("SELECT * FROM opportunities WHERE id = ?", (opp,))
    assert o["first_contact_at"] and o["first_contact_at"] == o["last_contact_at"]
    page = c.get(f"/sellers/{opp}").data.decode()
    assert "First contact after" in page and "Call — spoke" in page
    first = o["first_contact_at"]
    c.post(f"/sellers/{opp}/call", {"outcome": "no_answer", "next_title": "Try again", "next_due": "2030-01-02T10:00"})
    assert db.scalar("SELECT first_contact_at FROM opportunities WHERE id = ?", (opp,)) == first   # first stays



def test_work_log_hours_breaks_and_output(db, login, app):
    from datetime import timedelta

    from teamos.domain import worklog as WL
    from teamos.timeutil import IST, now_utc, to_iso
    c = login("seller@t.test")                                  # signing in starts the day
    me = user(db, "seller@t.test")
    assert me["work_status"] == "available"
    assert db.scalar("SELECT COUNT(*) FROM work_log WHERE user_id = ? AND event = 'login'", (me["id"],)) == 1
    # a day: 10:00 available, 13:00 break, 13:30 available, 15:00 salon visit, 17:00 off
    day = now_utc().astimezone(IST).date() - timedelta(days=1)
    from datetime import datetime, time
    t = lambda h, m=0: to_iso(datetime.combine(day, time(h, m), tzinfo=IST))   # noqa: E731
    db.execute("DELETE FROM work_log")
    for at, st in ((t(10), "available"), (t(13), "on_break"), (t(13, 30), "available"), (t(15), "in_field"),
                   (t(17), "offline")):
        db.insert("work_log", {"user_id": me["id"], "event": "status", "status": st, "at": at})
    db.insert("work_log", {"user_id": me["id"], "event": "login", "at": t(9, 58)})
    db.insert("activities", {"entity_type": "opportunity", "entity_id": 1, "kind": "call", "call_outcome": "connected",
                             "actor_id": me["id"], "at": t(16, 50)})
    s = WL.day_summary(db, me, day)
    assert s["work"] == "6 h 30 min" and s["break"] == "30 min" and s["breaks"] == 1 and s["field"] == "2 h 00 min"
    assert WL.output(db, me["id"], day)["calls"] == 1
    page = login("admin@t.test").get(f"/team/worklog?day={day.isoformat()}&user={me['id']}").data.decode()
    assert "Work log" in page and "6 h 30 min" in page and "last 7 days" in page and "On a break" in page
    assert c.get("/team/worklog").status_code in (302, 403)      # admins only
    c.post("/logout")
    assert user(db, "seller@t.test")["work_status"] == "offline"



def test_founder_desk_price_guide_and_takeover_matching(db, login):
    from teamos.domain import advisor
    g = advisor.price_guide({"avg_monthly_sales": 300000, "avg_monthly_expenses": 220000, "rent_deposit": 300000,
                             "asking_price": 3000000})
    assert (g["low"], g["high"], g["verdict"]) == (80000 * 12 + 300000, 80000 * 24 + 300000, "high")
    assert advisor.price_guide({"avg_monthly_sales": 100000, "avg_monthly_expenses": 120000})["verdict"] == "loss"
    assert advisor.price_guide({"avg_monthly_sales": None}) is None
    page = login("admin@t.test").get("/desk").data.decode()
    assert "Founder desk" in page and "Calls with Vikas" in page and "Money to collect" in page
    assert login("seller@t.test").get("/desk").status_code in (302, 403)
    # takeover: a new-salon investor is matched only after saying yes
    from test_matching import buyer, cand, seller
    from teamos.domain import matching as M
    s = seller(db)
    b = buyer(db, need_type="new_salon")
    assert cand(db, b, s) is None
    db.execute("UPDATE buyer_mandates SET consider_takeover = 'yes', data_version = data_version + 1")
    M.refresh(db, force=True)
    assert cand(db, b, s, refresh=False) is not None



def test_daily_targets_set_by_admin_and_shown_to_the_person(db, login):
    me = user(db, "seller@t.test")
    admin = login("admin@t.test")
    r = admin.post(f"/team/targets/{me['id']}", {"target_calls": "2", "target_connects": "1", "target_first_contacts": "",
                                                "target_forms": "x"})
    assert user(db, "seller@t.test")["target_calls"] is None                       # bad number: nothing saved
    admin.post(f"/team/targets/{me['id']}", {"target_calls": "2", "target_connects": "1"})
    assert user(db, "seller@t.test")["target_calls"] == 2
    opp = _seller(db)
    c = login("seller@t.test")
    c.post(f"/sellers/{opp}/call", {"outcome": "connected", "next_title": "x", "next_due": "2030-01-01T10:00"})
    page = c.get("/").data.decode()
    assert "Today's targets" in page and "Calls</strong> 1 / 2" in page and "Conversations</strong> 1 / 1 ✓" in page
    assert login("seller@t.test").post(f"/team/targets/{me['id']}", {"target_calls": "1"}).status_code in (302, 403)
    assert "daily targets" in admin.get(f"/team/worklog?user={me['id']}").data.decode()



def test_new_leads_go_round_robin_skipping_people_on_leave(db, app):
    from test_open_forms import _token
    from test_pilot import FULL

    from teamos.domain import progress as PR
    seller_user, lead_user = user(db, "seller@t.test"), user(db, "lead@t.test")   # seller team + "Both"
    got = [PR.round_robin(db, "seller") for _ in range(4)]
    assert got == [seller_user["id"], lead_user["id"], seller_user["id"], lead_user["id"]]
    AV.add_time_off(db, seller_user, seller_user["id"], now_utc().astimezone(IST).date().isoformat(), None, "leave", None)
    assert PR.round_robin(db, "seller") == lead_user["id"] and PR.round_robin(db, "seller") == lead_user["id"]
    # buyer side keeps its own turn: buyer team + "Both"
    assert {PR.round_robin(db, "buyer") for _ in range(2)} == {user(db, "buyer@t.test")["id"], lead_user["id"]}
    # public forms use it too
    pub = app.test_client()
    for i in range(2):
        page = pub.get("/f/pilot").data.decode()
        pub.post("/f/pilot", data={**FULL, "contact_number": f"900001110{i}", "form_token": _token(page),
                                   "consent_contact": "1"})
    owners = [r["primary_owner_id"] for r in db.all("SELECT primary_owner_id FROM opportunities ORDER BY id")]
    assert owners and all(o == lead_user["id"] for o in owners)      # the only one not on leave


def test_team_members_see_only_what_they_need(db, login):
    member = login("seller@t.test")
    home = member.get("/").data.decode()
    nav = home.split("<main")[0]
    assert ">Team<" not in nav and "Work log" not in nav
    tools = member.get("/tools").data.decode()
    assert "Spreadsheet import" in tools and "Duplicate review" in tools and "Install on your phone" in tools
    for hidden in ("Automations", "Audit log", "Match learning", "Field dictionary", "Lead sources"):
        assert hidden not in tools, hidden
    admin_nav = login("admin@t.test").get("/").data.decode().split("<main")[0]
    assert ">Team<" in admin_nav
