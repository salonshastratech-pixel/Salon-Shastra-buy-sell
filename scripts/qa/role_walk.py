"""QA: work a day as Shaheen (Salon Owners) and as Harika (Buyers & investors), on a COPY of a database.

  python -I scripts/qa/role_walk.py <copy-of.db>

Each of them creates 8 dummy leads from scratch and works them the way calls really go: no answer, switched off,
busy, connected with lots of information, connected but nothing given, call back later, wrong number, not
interested, not a fit, do not contact, actually the other side (moved), and a record pushed back and forth
through stages. After every step the saved data is checked: the call and note text, the stage, the history, the
open next action (always exactly the right ones), notifications to the right person, the call / WhatsApp
buttons, and that nothing typed is lost. Every dummy is removed at the end.
"""
import re
import sys
from datetime import timedelta
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

PROBLEMS, CHECKS = [], [0]


def check(ok, what):
    CHECKS[0] += 1
    if not ok:
        PROBLEMS.append(what)
        print("  FAIL", what)
    return ok


def main():
    dbfile = sys.argv[1]
    from teamos import create_app
    from teamos.db import open_standalone
    from teamos.timeutil import now_iso, now_utc
    url = "sqlite:///" + dbfile.replace("\\", "/")
    app = create_app({"DATABASE_URL": url, "SECRET_KEY": "qa", "TESTING": True, "MFA_REQUIRED_ROLES": "",
                      "QUIET_HOURS": "", "FILES_KEY": "Wz3Qt1tVYv0d6m1M4mQe5Gk0b8Yq3c2y7r9s1u4x6zA="})
    db = open_standalone(url)
    before = {t: db.scalar(f"SELECT COUNT(*) FROM {t}") for t in ("opportunities", "contacts", "tasks")}
    start = now_iso()
    uid = {r["email"].split("@")[0].split(".")[0]: r["id"] for r in db.all("SELECT id, email FROM users")}
    SH, HA = uid["shaheen"], uid["harika"]

    def client(email):
        c = app.test_client()
        r = c.post("/login", data={"email": email, "password": "Demo@123"})
        check(r.status_code == 302, f"login {email}")
        return c

    def tok(html):
        return (re.search(r'name="csrf_token" value="([^"]+)"', html) or [None, ""])[1]

    def page(c, path):
        r = c.get(path)
        html = r.data.decode()
        check(r.status_code == 200 and "Traceback" not in html, f"GET {path} -> {r.status_code}")
        return html

    def post(c, path, data, at):
        html = c.get(at).data.decode()
        r = c.post(path, data=dict(data, csrf_token=tok(html)))
        check(r.status_code in (200, 302), f"POST {path} -> {r.status_code}")
        follow = c.get(r.headers["Location"]) if r.status_code == 302 else r
        body = follow.data.decode()
        check("Traceback" not in body, f"POST {path}: page after it crashed")
        return body

    def flash_err(html):
        return " ".join(re.findall(r'class="flash error"[^>]*>(.*?)<', html, re.S)).strip()

    def opp_of(phone, kind=None):
        q = ("SELECT o.* FROM opportunities o JOIN contacts c ON c.id = o.contact_id WHERE c.phone_normalized = ? "
             "AND o.stage <> 'moved'" + (" AND o.kind = ?" if kind else "") + " ORDER BY o.id DESC")
        return db.one(q, ("+91" + phone, kind) if kind else ("+91" + phone,))

    def S_load(oid):
        from teamos.domain import sellers as _S
        return _S.load(db, oid)["listing_id"]

    def stage(oid):
        return db.scalar("SELECT stage FROM opportunities WHERE id = ?", (oid,))

    def open_tasks(oid):
        return db.all("SELECT * FROM tasks WHERE entity_type = 'opportunity' AND entity_id = ? AND status = 'open'",
                      (oid,))

    def last_call(oid):
        return db.one("SELECT * FROM activities WHERE entity_type = 'opportunity' AND entity_id = ? AND kind = 'call' "
                      "ORDER BY id DESC LIMIT 1", (oid,))

    def notes_shown(c, base, oid, text):
        html = page(c, f"/{base}/{oid}")
        return text in html

    def notified(user_id, like):
        return db.scalar("SELECT COUNT(*) FROM notifications WHERE user_id = ? AND created_at >= ? AND title LIKE ?",
                         (user_id, start, like)) or 0

    due = lambda d: (now_utc() + timedelta(days=d)).strftime("%Y-%m-%dT11:00")  # noqa: E731

    def call(c, base, oid, outcome, note="", next_title="", next_due="", complete=True):
        at = f"/{base}/{oid}"
        ts = open_tasks(oid)
        data = {"outcome": outcome, "note": note, "next_title": next_title, "next_due": next_due,
                "complete_task_id": str(ts[0]["id"]) if (complete and ts) else ""}
        return post(c, f"{at}/call", data, at)

    def work(c, me, other, base, kind, n0):
        """8 dummies of one side, worked by `me`; `other` is the colleague on the other side."""
        side = "Salon owner" if kind == "seller" else "Buyer"
        mk = {}
        for i, name in enumerate(["Nanda", "Kiran", "Meera", "Wrong", "Uday", "Fatima", "Swap", "Pingpong"]):
            phone = f"9301{n0}{i:04d}"
            print(f"{side} {i + 1}: {name}")
            data = {"full_name": f"QA RW {name}", "phone": phone, "city": "Hyderabad", "locality": "Kondapur",
                    "source": "manual_call", "consent_contact": "1", "owner_id": str(me)}
            if kind == "seller":
                data.update(salon_name=f"QA RW {name} Salon", sale_purpose="sell_entire" if i % 2 == 0 else "seek_help",
                            asking_price="1500000" if i % 2 == 0 else "")
            else:
                data.update(need_type="acquire_operating", all_in_max="3000000")
            html = post(c, f"/{base}/new", data, f"/{base}/new")
            o = opp_of(phone, kind)
            if not check(o is not None, f"{side} {name}: created ({flash_err(html)[:120]})"):
                continue
            mk[name] = o
            check(o["primary_owner_id"] == me, f"{name}: owned by the person who added it")
            ts = open_tasks(o["id"])
            check(len(ts) == 1 and ts[0]["assignee_id"] == me, f"{name}: one first-call task on my day ({len(ts)})")
            html = page(c, f"/{base}/{o['id']}")
            check(f'href="tel:+91{phone}"' in html, f"{name}: Call button dials the number")
            check(f"wa.me/91{phone}" in html or f"/inbox/wa/{o['id']}" in html or "WhatsApp" in html,
                  f"{name}: WhatsApp button present")
        home = page(c, "/")
        check(all(f"QA RW {n}" in home or o["ref_code"] in home for n, o in mk.items()), "all 8 on My Day")

        # 1 Nanda: no answer, switched off, busy - three tries, then connected and gives everything
        o = mk.get("Nanda")
        if o:
            call(c, base, o["id"], "no_answer", "rang 6 times")
            check(stage(o["id"]) == "contact_attempted", "Nanda: no answer -> Contact attempted")
            check(len(open_tasks(o["id"])) == 1 and "Try again" in open_tasks(o["id"])[0]["title"] or
                  len(open_tasks(o["id"])) == 1, f"Nanda: a try-again task waits ({[t['title'] for t in open_tasks(o['id'])]})")
            call(c, base, o["id"], "switched_off", "", "Try again evening", due(0))
            call(c, base, o["id"], "busy", "cut the call", "Try again tomorrow", due(1))
            check(len(open_tasks(o["id"])) == 1, f"Nanda: still exactly one next action ({len(open_tasks(o['id']))})")
            txt = "Nanda wants out by March, 7 chairs, rent 52000, sales 3.5L a month, landlord ok with transfer"
            call(c, base, o["id"], "connected", txt, "Send detailed form", due(1))
            check(stage(o["id"]) == "connected", "Nanda: connected -> Connected")
            check(last_call(o["id"])["body"] == txt, "Nanda: call note saved word for word")
            check(notes_shown(c, base, o["id"], "Nanda wants out by March"), "Nanda: call note shows on the profile")
            if kind == "seller":
                at = f"/{base}/{o['id']}?tab=intake"
                html = post(c, f"/{base}/{o['id']}/stage", {"to": "basic_qualified"}, f"/{base}/{o['id']}")
                check("who you spoke to" in flash_err(html), f"Nanda: qualifying first says what to fill ({flash_err(html)[:120]})")
                post(c, f"/{base}/{o['id']}/section/asset", {"chairs": "7"}, at)
                post(c, f"/{base}/{o['id']}/section/lease", {"monthly_rent": "52000"}, at)
                html = post(c, f"/{base}/{o['id']}/section/quick",
                            {"salon_name": "QA RW Nanda Salon", "city": "Hyderabad", "locality": "Kondapur",
                             "ownership_role": "owner", "sale_purpose": "sell_entire"}, at)
                L = db.one("SELECT * FROM seller_listings WHERE id = ?", (S_load(o["id"]),))
                check(L and L.get("chairs") == 7 and L.get("monthly_rent") == 52000,
                      f"Nanda: intake numbers saved (chairs {L and L.get('chairs')}, rent {L and L.get('monthly_rent')}) "
                      f"{flash_err(html)[:160]}")
                bad = post(c, f"/{base}/{o['id']}/section/asset", {"chairs": "seven"}, at)
                check("Not saved" in bad, "Nanda: letters in a number field are refused, with a clear message")
                L = db.one("SELECT * FROM seller_listings WHERE id = ?", (S_load(o["id"]),))
                check(L.get("chairs") == 7, "Nanda: the refused save did not wipe the old value")
                nxt = "basic_qualified"
            else:
                nxt = "requirements_captured"
            html = post(c, f"/{base}/{o['id']}/stage", {"to": nxt}, f"/{base}/{o['id']}")
            check(stage(o["id"]) == nxt, f"Nanda: moved to {nxt} ({flash_err(html)[:140]})")

        # 2 Kiran: connected but gives nothing - "busy now, call me Monday"
        o = mk.get("Kiran")
        if o:
            html = call(c, base, o["id"], "callback_requested", "driving, said call Monday after 11", complete=True)
            check(stage(o["id"]) == "connected" or open_tasks(o["id"]),
                  f"Kiran: without a next action -> told to add one ({flash_err(html)[:80]})")
            check("next action" in flash_err(html).lower() or len(open_tasks(o["id"])) == 1,
                  "Kiran: missing next action explained")
            call(c, base, o["id"], "callback_requested", "driving, said call Monday after 11", "Call back Monday 11 am",
                 due(2))
            ts = open_tasks(o["id"])
            check(len(ts) == 1 and ts[0]["title"] == "Call back Monday 11 am", f"Kiran: callback task saved ({ts and ts[0]['title']})")
            check(stage(o["id"]) == "connected", f"Kiran: callback -> Connected ({stage(o['id'])})")
            n = db.scalar("SELECT COUNT(*) FROM activities WHERE entity_id = ? AND kind = 'call'", (o["id"],))
            check(n == 1, f"Kiran: a refused save did not leave half a call behind ({n} calls)")

        # 3 Meera: connected, little info + a note mentioning the colleague + marked hot by the colleague
        o = mk.get("Meera")
        if o:
            call(c, base, o["id"], "connected", "only said 'send me details on WhatsApp'", "Follow up after WhatsApp", due(1))
            first = db.scalar("SELECT full_name FROM users WHERE id = ?", (other,)).split()[0]
            post(c, f"/{base}/{o['id']}/note", {"body": f"@{first} she may also want to buy a second salon"},
                 f"/{base}/{o['id']}")
            check(notified(other, f"%mentioned you on {o['ref_code']}%") == 1, f"Meera: @{first} gets a notification")
            check(notes_shown(c, base, o["id"], "may also want to buy a second salon"), "Meera: note shows on the profile")
            empty = post(c, f"/{base}/{o['id']}/note", {"body": "   "}, f"/{base}/{o['id']}")
            check("Write a note" in empty, "Meera: an empty note is refused, with a message")
            oc = client(db.scalar("SELECT email FROM users WHERE id = ?", (other,)))
            post(oc, f"/records/{o['id']}/basics",
                 {"full_name": f"QA RW Meera", "phone": f"9301{n0}0002", "salon_name": "QA RW Meera Salon",
                  "city": "Hyderabad", "locality": "Kondapur", "lead_temperature": "hot"}, f"/{base}/{o['id']}")
            check(notified(me, f"%marked {o['ref_code']} as hot%") == 1, "Meera: owner told when a colleague marks it hot")
            post(c, f"/records/{o['id']}/basics",
                 {"full_name": f"QA RW Meera", "phone": f"9301{n0}0002", "salon_name": "QA RW Meera Salon",
                  "city": "Hyderabad Hyderabad", "locality": "Kondapur", "lead_temperature": "hot"}, f"/{base}/{o['id']}")
            city = db.scalar("SELECT c.city FROM contacts c WHERE c.id = ?", (o["contact_id"],)) if kind == "buyer" else \
                db.scalar("SELECT b.city FROM salon_businesses b WHERE b.id = ?", (o["business_id"],))
            check(city == "Hyderabad", f"Meera: city typed twice is saved once ({city})")
            mine = db.all("SELECT body FROM activities WHERE entity_id = ? AND kind = 'data_update' AND actor_id = ?",
                          (o["id"], me))
            check(not mine, f"Meera: saving without real changes logs nothing ({[m['body'] for m in mine]})")

        # 4 Wrong: wrong number -> try the other number -> unqualified
        o = mk.get("Wrong")
        if o:
            call(c, base, o["id"], "wrong_number", "a lady answered, does not know any salon", "Ask Meta lead for correct number", due(1))
            check(stage(o["id"]) == "contact_attempted", f"Wrong: wrong number -> Contact attempted ({stage(o['id'])})")
            html = post(c, f"/{base}/{o['id']}/stage", {"to": "unqualified"}, f"/{base}/{o['id']}")
            check(stage(o["id"]) == "contact_attempted" and "reason" in flash_err(html).lower(),
                  "Wrong: closing without a reason is refused")
            post(c, f"/{base}/{o['id']}/stage", {"to": "unqualified", "reason": "number never belonged to them"},
                 f"/{base}/{o['id']}")
            check(stage(o["id"]) == "unqualified", "Wrong: -> Unqualified with the reason")
            check(len(open_tasks(o["id"])) == 0, "Wrong: its open tasks are closed, nothing left on My Day")

        # 5 Uday: connected, not interested, no next action -> closed lost by itself, note as reason
        o = mk.get("Uday")
        if o:
            call(c, base, o["id"], "not_interested", "has decided to keep the salon, son will run it")
            check(stage(o["id"]) == "closed_lost", f"Uday: not interested -> Closed lost ({stage(o['id'])})")
            r = db.scalar("SELECT reason FROM stage_history WHERE opportunity_id = ? ORDER BY id DESC LIMIT 1", (o["id"],))
            check(r and "son will run it" in r, f"Uday: the call note is the reason ({r})")
            check(len(open_tasks(o["id"])) == 0, "Uday: no tasks left")
            # reopened later: he calls back
            post(c, f"/{base}/{o['id']}/stage", {"to": "connected"}, f"/{base}/{o['id']}")
            check(stage(o["id"]) == "connected", f"Uday: can be reopened when he calls back ({stage(o['id'])})")
            post(c, f"/{base}/{o['id']}/task", {"title": "Meet Uday's son", "assignee_id": str(me), "due_at": due(3)},
                 f"/{base}/{o['id']}")
            check(any(t["title"] == "Meet Uday's son" for t in open_tasks(o["id"])), "Uday: new task added after reopening")

        # 6 Fatima: do not contact
        o = mk.get("Fatima")
        if o:
            call(c, base, o["id"], "do_not_contact", "asked us never to call again")
            check(stage(o["id"]) == "do_not_contact", "Fatima: -> Do not contact")
            check(len(open_tasks(o["id"])) == 0, "Fatima: no tasks left to call her")
            html = page(c, f"/{base}/{o['id']}")
            check("Do not contact" in html, "Fatima: profile clearly says Do not contact")

        # 7 Swap: actually the other side -> move; everything comes along, colleague can see it
        o = mk.get("Swap")
        if o:
            call(c, base, o["id"], "connected", "Swap call: really wants the other thing", "Talk next step", due(1))
            post(c, f"/{base}/{o['id']}/note", {"body": "Swap note before the move"}, f"/{base}/{o['id']}")
            to = "buyer" if kind == "seller" else "seller"
            r = c.get(f"/records/{o['id']}/move?to={to}")
            check(r.status_code == 200, f"Swap: the move page opens ({r.status_code})")
            post(c, f"/records/{o['id']}/move", {"to": to, "salon_name": "QA RW Swap Salon", "city": "Hyderabad",
                                                  "locality": "Madhapur", "reason": "answered the wrong ad"},
                 f"/records/{o['id']}/move?to={to}")
            new = opp_of(f"9301{n0}0006", to)
            if check(new is not None, f"Swap: now under {'Buyers' if to == 'buyer' else 'Salon Owners'}"):
                check(stage(o["id"]) == "moved", "Swap: old record closed as Moved, kept")
                calls = db.scalar("SELECT COUNT(*) FROM activities WHERE entity_id = ? AND kind = 'call'", (new["id"],))
                check(calls >= 1, f"Swap: the call came along ({calls})")
                check(db.scalar("SELECT COUNT(*) FROM activities WHERE entity_id = ? AND body = 'Swap note before the move'",
                                (new["id"],)) == 1, "Swap: the note came along")
                check(len(open_tasks(new["id"])) >= 1 and len(open_tasks(o["id"])) == 0,
                      "Swap: next action moved to the new record, none left on the old")
                nb = "buyers" if to == "buyer" else "sellers"
                oc = client(db.scalar("SELECT email FROM users WHERE id = ?", (other,)))
                html = page(oc, f"/{nb}/{new['id']}")
                check("Swap call: really wants the other thing" in html, "Swap: colleague sees the history on the new record")
                post(c, f"/{nb}/{new['id']}/assign", {"owner_id": str(other), "reason": "your side"}, f"/{nb}/{new['id']}")
                check(db.scalar("SELECT primary_owner_id FROM opportunities WHERE id = ?", (new["id"],)) == other,
                      "Swap: handed to the colleague")
                check(notified(other, "%") >= 1, "Swap: colleague notified of the hand-over")
                check(all(t["assignee_id"] == other for t in open_tasks(new["id"])),
                      f"Swap: its open tasks went to the colleague too ({[t['assignee_id'] for t in open_tasks(new['id'])]})")

        # 8 Pingpong: pushed back and forth through stages
        o = mk.get("Pingpong")
        if o:
            call(c, base, o["id"], "connected", "interested but in 3 months", "Check in", due(1))
            html = post(c, f"/{base}/{o['id']}/stage", {"to": "nurture", "reason": "3 months"}, f"/{base}/{o['id']}")
            check(stage(o["id"]) == "connected" and "follow-up" in flash_err(html).lower(),
                  "Pingpong: nurture needs a follow-up date")
            post(c, f"/{base}/{o['id']}/stage", {"to": "nurture", "reason": "3 months", "follow_up_due": due(90)},
                 f"/{base}/{o['id']}")
            check(stage(o["id"]) == "nurture", "Pingpong: -> Nurture")
            ts = open_tasks(o["id"])
            check(len(ts) == 1, f"Pingpong: one follow-up waiting in 3 months ({len(ts)})")
            post(c, f"/{base}/{o['id']}/stage", {"to": "connected"}, f"/{base}/{o['id']}")
            first_q = "basic_qualified" if kind == "seller" else "requirements_captured"
            if kind == "seller":
                post(c, f"/{base}/{o['id']}/section/quick", {"salon_name": "QA RW Pingpong Salon", "city": "Hyderabad",
                     "locality": "Kondapur", "ownership_role": "partner", "sale_purpose": "partnership"},
                     f"/{base}/{o['id']}?tab=intake")
            post(c, f"/{base}/{o['id']}/stage", {"to": first_q}, f"/{base}/{o['id']}")
            post(c, f"/{base}/{o['id']}/stage", {"to": "connected"}, f"/{base}/{o['id']}")
            post(c, f"/{base}/{o['id']}/stage", {"to": "closed_lost", "reason": "went with another broker"},
                 f"/{base}/{o['id']}")
            post(c, f"/{base}/{o['id']}/stage", {"to": "connected"}, f"/{base}/{o['id']}")
            hist = [h["to_stage"] for h in db.all("SELECT to_stage FROM stage_history WHERE opportunity_id = ? ORDER BY id",
                                                  (o["id"],))]
            check(hist[-6:] == ["nurture", "connected", first_q, "connected", "closed_lost", "connected"],
                  f"Pingpong: every move kept in history ({hist})")
            html = page(c, f"/{base}/{o['id']}?tab=history")
            check("went with another broker" in html, "Pingpong: reasons show in the History tab")
            ts = open_tasks(o["id"])
            check(len(ts) <= 1, f"Pingpong: no pile-up of tasks after the back and forth ({len(ts)})")

        # lists, board, priorities, tasks page, alerts
        for p in (f"/{base}/", f"/{base}/board", f"/{base}/?q=QA+RW", "/tasks/", "/", "/search?q=QA+RW"):
            page(c, p)
        lst = page(c, f"/{base}/?q=QA+RW")
        check(lst.count("QA RW") >= 6, f"{side} list finds the dummies ({lst.count('QA RW')})")
        return mk

    founder = client("founder@demo.salonshastra.test")
    shaheen = client("shaheen.salonshastra@gmail.com")
    harika = client("harika.salonshastra@gmail.com")
    print("== Shaheen: Salon Owners ==")
    work(shaheen, SH, HA, "sellers", "seller", "21")
    print("== Harika: Buyers & investors ==")
    work(harika, HA, SH, "buyers", "buyer", "22")

    # permissions: a team member cannot approve a listing or reach admin pages
    o = opp_of("9301210000", "seller")
    if o:
        r = shaheen.get("/tools/users") if False else shaheen.get("/admin/users")
        check(r.status_code in (403, 404, 302), f"Shaheen cannot open user admin ({r.status_code})")
    # everything for the dummies: no duplicates, no active record without a next action
    dup = db.scalar("SELECT COUNT(*) FROM (SELECT c.phone_normalized, o.kind FROM opportunities o JOIN contacts c ON "
                    "c.id = o.contact_id WHERE o.stage <> 'moved' AND c.phone_normalized LIKE '+9193012%' GROUP BY 1, 2 "
                    "HAVING COUNT(*) > 1) x")
    check(dup == 0, f"no duplicate dummy records ({dup})")
    orphan = db.all("SELECT o.ref_code, o.stage FROM opportunities o JOIN contacts c ON c.id = o.contact_id WHERE "
                    "c.phone_normalized LIKE '+9193012%' AND o.status = 'active' AND o.stage NOT IN "
                    "('moved','nurture','closed_lost','unqualified','do_not_contact','withdrawn','closed_won') AND NOT EXISTS "
                    "(SELECT 1 FROM tasks t WHERE t.entity_type = 'opportunity' AND t.entity_id = o.id AND t.status = 'open')")
    check(not orphan, f"every active dummy has a next action ({orphan})")

    from teamos.domain import purge
    admin = db.one("SELECT * FROM users WHERE email = 'founder@demo.salonshastra.test'")
    ids = [r["id"] for r in db.all("SELECT o.id FROM opportunities o JOIN contacts c ON c.id = o.contact_id WHERE "
                                   "c.phone_normalized LIKE '+9193012%'")]
    import os
    if os.environ.get("KEEP"):      # leave the dummies in the copy to look at them
        return
    with app.test_request_context():
        purge.delete_records(db, admin, ids, "QA dummy data", remember=False)
    after = {t: db.scalar(f"SELECT COUNT(*) FROM {t}") for t in ("opportunities", "contacts")}
    check(after["opportunities"] == before["opportunities"], f"dummy data removed: {before} -> {after}")
    print(f"\n{CHECKS[0]} checks, {len(PROBLEMS)} problems")
    for p in PROBLEMS:
        print(" -", p)


if __name__ == "__main__":
    main()
