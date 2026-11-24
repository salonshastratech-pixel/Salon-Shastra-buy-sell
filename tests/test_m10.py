"""Milestone 10: automations you can see and pause, saved searches, customer progress links, lead sources, phone app."""
import json
from datetime import datetime, timedelta

from conftest import user
from test_matching import buyer, seller

from teamos import i18n, jobs
from teamos.domain import automations as A
from teamos.domain import reports as R
from teamos.domain import saved_searches as SS
from teamos.domain import status_links as SL
from teamos.timeutil import IST, now_utc, to_iso


# -- automations ------------------------------------------------------------------------------------------------
def test_everyone_sees_automations_only_admins_change_them(db, login):
    page = login("seller@t.test").get("/automations").data.decode()
    assert "Overdue alerts" in page and "Not-moving alerts" in page and "Pause everything" not in page
    assert login("seller@t.test").post("/automations", {"key": "sla_sweep", "on": "0"}).status_code == 403
    assert A.is_on(db, "sla_sweep")
    admin = login("admin@t.test")
    assert "Pause everything" in admin.get("/automations").data.decode()
    admin.post("/automations", {"key": "sla_sweep", "on": "0", "note": "Team offsite"})
    assert not A.is_on(db, "sla_sweep")
    assert db.scalar("SELECT COUNT(*) FROM audit_events WHERE action = 'automation.paused'") == 1


def test_paused_automation_is_skipped_and_others_still_run(db):
    admin = user(db, "admin@t.test")
    A.set_enabled(db, admin, "stuck_sweep", False)
    jobs.enqueue(db, "stuck_sweep", dedupe_key="t-stuck")
    jobs.enqueue(db, "sla_sweep", dedupe_key="t-sla")
    jobs.run_once(db)
    out = {r["kind"]: r for r in db.all("SELECT j.kind, jr.outcome, jr.message FROM job_runs jr JOIN jobs j "
                                        "ON j.id = jr.job_id")}
    assert out["stuck_sweep"]["outcome"] == "skipped" and out["sla_sweep"]["outcome"] == "done"


def test_pause_everything_holds_lead_ads_until_resumed(db):
    admin = user(db, "admin@t.test")
    A.set_enabled(db, admin, "_all", False)
    jid = jobs.enqueue(db, "lead_ads", {"ids": []}, dedupe_key="t-la")
    jobs.run_once(db)
    j = db.one("SELECT * FROM jobs WHERE id = ?", (jid,))
    assert j["status"] == "queued" and j["run_after"] > to_iso(now_utc())  # waiting, not lost
    assert A.paused(db, "sla_sweep") and not A.paused(db, "webhook_retry")  # retrying provider events never stops
    A.set_enabled(db, admin, "_all", True)
    db.execute("UPDATE jobs SET run_after = ? WHERE id = ?", (to_iso(now_utc() - timedelta(minutes=1)), jid))
    jobs.run_once(db)
    assert db.scalar("SELECT status FROM jobs WHERE id = ?", (jid,)) == "done"


def test_webhook_retry_cannot_be_paused(db):
    import pytest
    from teamos.domain.errors import ValidationError
    with pytest.raises(ValidationError):
        A.set_enabled(db, user(db, "admin@t.test"), "webhook_retry", False)


def test_quiet_hours_window(db):
    cfg = {"QUIET_HOURS": "21-8"}
    at = lambda h: datetime(2026, 11, 24, h, 30, tzinfo=IST)  # noqa: E731
    assert A.quiet_now(db, cfg, at(22)) and A.quiet_now(db, cfg, at(3)) and A.quiet_now(db, cfg, at(7))
    assert not A.quiet_now(db, cfg, at(8)) and not A.quiet_now(db, cfg, at(14)) and not A.quiet_now(db, cfg, at(20))
    assert not A.quiet_now(db, {"QUIET_HOURS": ""}, at(23))
    A.set_enabled(db, user(db, "admin@t.test"), "quiet_hours", False)
    assert not A.quiet_now(db, cfg, at(23))
    assert A.quiet_label(cfg) == "9 pm – 8 am"


def test_bulk_send_held_while_everything_is_paused(app, db, login):
    buyer(db, name="Alpha Buyer")
    member, lead = login("buyer@t.test"), login("lead@t.test")
    r = member.post("/inbox/batches", {"name": "Callbacks", "template_key": "callback_request", "audience": "buyers_active"})
    bid = int(r.headers["Location"].rstrip("/").split("/")[-1])
    lead.post(f"/inbox/batches/{bid}", {"action": "approve"})
    A.set_enabled(db, user(db, "admin@t.test"), "_all", False)
    page = lead.post(f"/inbox/batches/{bid}", {"action": "send"}, follow_redirects=True).data.decode()
    assert "paused" in page and db.scalar("SELECT status FROM batches WHERE id = ?", (bid,)) == "approved"
    assert db.scalar("SELECT COUNT(*) FROM messages WHERE batch_id = ?", (bid,)) == 0


# -- saved searches ---------------------------------------------------------------------------------------------
def test_save_a_search_and_see_new_records_since_last_look(db, login):
    seller(db, phone="9000011001", salon="Old Salon")
    c = login("seller@t.test")
    c.post("/saved-searches", {"kind": "seller", "name": "Kondapur sellers", "query": "q=kondapur"})
    s = db.one("SELECT * FROM saved_searches")
    assert s["query"] == "q=kondapur" and s["user_id"] == user(db, "seller@t.test")["id"]
    db.execute("UPDATE saved_searches SET last_seen_at = ?", (to_iso(now_utc() - timedelta(hours=1)),))
    seller(db, phone="9000011002", salon="New Salon")
    db.execute("UPDATE opportunities SET created_at = ? WHERE id = (SELECT MAX(id) FROM opportunities)",
               (to_iso(now_utc()),))
    db.execute("UPDATE opportunities SET created_at = ? WHERE id = (SELECT MIN(id) FROM opportunities)",
               (to_iso(now_utc() - timedelta(hours=3)),))
    assert SS.mine(db, s["user_id"], "seller")[0]["new"] == 1
    assert "1 new" in c.get("/sellers/").data.decode()
    assert "Kondapur sellers" in c.get("/").data.decode()  # My Day chip
    r = c.get(f"/saved-searches/{s['id']}")
    assert r.status_code == 302 and "q=kondapur" in r.headers["Location"]
    assert SS.mine(db, s["user_id"], "seller")[0]["new"] == 0  # looked: count resets


def test_saved_search_needs_a_filter_and_is_private(db, login):
    c = login("seller@t.test")
    c.post("/saved-searches", {"kind": "seller", "name": "Everything", "query": ""})
    assert db.scalar("SELECT COUNT(*) FROM saved_searches") == 0
    c.post("/saved-searches", {"kind": "buyer", "name": "Mine", "query": "owner=me"})
    sid = db.scalar("SELECT id FROM saved_searches")
    other = login("buyer@t.test")
    assert other.get(f"/saved-searches/{sid}").status_code == 404
    other.post(f"/saved-searches/{sid}/delete")
    assert db.scalar("SELECT COUNT(*) FROM saved_searches") == 1


def test_list_filters_unchanged_by_refactor(db, login):
    seller(db, phone="9000011001", salon="Alpha Salon")
    seller(db, phone="9000011002", salon="Beta Salon", locality="Madhapur")
    page = login("seller@t.test").get("/sellers/?q=madhapur").data.decode()
    assert "Beta Salon" in page and "Alpha Salon" not in page
    b = login("buyer@t.test").get("/buyers/?q=kondapur&owner=me").data.decode()
    assert "No buyers" in b or "BY-" in b


# -- customer progress link -------------------------------------------------------------------------------------
def test_progress_link_shows_journey_without_private_details(db, login, app):
    s = seller(db, phone="9000011777", salon="Secret Name Salon")
    db.execute("UPDATE seller_listings SET monthly_rent = NULL WHERE opportunity_id = ?", (s,))
    c = login("seller@t.test")
    page = c.post(f"/records/{s}/progress-link").data.decode()
    assert "/p/" in page
    token = page.split("/p/")[1].split("<")[0].strip()
    anon = app.test_client()
    body = anon.get(f"/p/{token}").data.decode()
    assert "Hello Owner" in body and "SL-" in body and "Our review" in body
    assert "Monthly rent" in body or "rent" in body.lower()  # what we still need
    for secret in ("9000011777", "Secret Name Salon", "20,00,000", "Shaheen Seller"):
        assert secret not in body
    assert "Shaheen" in body  # contact person, first name only
    assert db.scalar("SELECT opens FROM status_links") == 1
    assert db.scalar("SELECT COUNT(*) FROM status_links WHERE token_hash = ?", (token,)) == 0  # hash only
    te = anon.get(f"/p/{token}?lang=te").data.decode()
    assert i18n.tr("Our review", "te") in te
    c.post(f"/records/{s}/progress-link/off")
    assert anon.get(f"/p/{token}").status_code == 404


def test_new_progress_link_replaces_the_old_one_and_closed_records_show_nothing(db, login, app):
    s = seller(db)
    c = login("seller@t.test")
    t1 = c.post(f"/records/{s}/progress-link").data.decode().split("/p/")[1].split("<")[0].strip()
    t2 = c.post(f"/records/{s}/progress-link").data.decode().split("/p/")[1].split("<")[0].strip()
    anon = app.test_client()
    assert anon.get(f"/p/{t1}").status_code == 404 and anon.get(f"/p/{t2}").status_code == 200
    db.execute("UPDATE contacts SET do_not_contact = 1")
    r = anon.get(f"/p/{t2}")
    assert r.status_code == 410 and "no longer active" in r.data.decode()


def test_progress_steps_follow_the_stage(db):
    s = seller(db)
    db.execute("UPDATE opportunities SET stage = 'form_sent' WHERE id = ?", (s,))
    v = SL.view(db, SL.load(db, s))
    states = [x["state"] for x in v["steps"]]
    assert states[:3] == ["done", "done", "now"] and states[3] == "next"
    db.execute("UPDATE opportunities SET stage = 'nurture' WHERE id = ?", (s,))
    assert SL.view(db, SL.load(db, s))["state"] == "paused"


def test_progress_page_strings_are_translated():
    strings = [label for label, _ in SL.SELLER_STEPS + SL.BUYER_STEPS] + list(SL.STEP_NOTES.values()) + list(SL.CUSTOMER_LABELS.values()) + [
        "Your progress", "What we still need from you", "Your contact", "Hello", "Now", "Photos of your salon"]
    assert i18n.missing(strings) == []


def test_record_page_has_progress_card_and_preview(db, login):
    s = seller(db)
    c = login("seller@t.test")
    assert "Customer progress link" in c.get(f"/sellers/{s}").data.decode()
    prev = c.get(f"/records/{s}/progress-preview").data.decode()
    assert "Staff preview" in prev and db.scalar("SELECT COUNT(*) FROM status_links") == 0


# -- lead sources -----------------------------------------------------------------------------------------------
def test_lead_sources_follow_leads_to_deals(db, login):
    a = seller(db, phone="9000011001")
    b = buyer(db, phone="9100011001")
    admin = user(db, "admin@t.test")
    db.insert("activities", {"entity_type": "opportunity", "entity_id": b, "kind": "call",
                             "call_outcome": "connected", "actor_id": admin["id"], "at": to_iso(now_utc())})
    r = R.sources(db, admin, R.period_bounds("today"))
    rows = {(x["kind"], x["channel"]): x for x in r["rows"]}
    assert rows[("seller", "Manual call")]["leads"] == 1
    assert rows[("buyer", "Referral")]["reached"] == 1 and rows[("buyer", "Referral")]["pct"]["reached"] == 100
    assert r["total"]["leads"] == 2 and r["fees"] is True
    assert "fees" not in R.sources(db, user(db, "seller@t.test"), R.period_bounds("today"))["total"]
    page = login("seller@t.test").get("/reports/sources?period=today").data.decode()
    assert "Where good leads come from" in page and "Referral" in page and "Fees earned" not in page
    assert a


def test_lead_ad_leads_are_counted_as_lead_ads(db):
    from teamos.domain import leadads as LA
    LA.simulate(db, "buyer", {"full_name": "Ad Person", "phone_number": "9876501234", "city": "Hyderabad"})
    o = db.one("SELECT source, source_detail FROM opportunities ORDER BY id DESC LIMIT 1")
    assert o["source"] == "meta_lead" and o["source_detail"].startswith("Lead ad:")


# -- phone app --------------------------------------------------------------------------------------------------
def test_phone_app_manifest_worker_and_offline_page(db, app, login):
    anon = app.test_client()
    m = json.loads(anon.get("/manifest.webmanifest").data)
    assert m["display"] == "standalone" and m["start_url"] == "/" and len(m["icons"]) >= 2
    for icon in m["icons"]:
        assert anon.get(icon["src"]).status_code == 200
    sw = anon.get("/sw.js")
    assert sw.status_code == 200 and sw.mimetype == "application/javascript"
    assert "never stored" in sw.data.decode()  # pages with customer data are not cached
    assert "offline" in anon.get("/offline").data.decode()
    assert 'rel="manifest"' in login("seller@t.test").get("/").data.decode()
    assert "Install on your phone" in login("seller@t.test").get("/tools").data.decode()


def test_failed_run_shows_plain_words_not_a_traceback(db, login):
    jid = jobs.enqueue(db, "stuck_sweep", dedupe_key="t-fail")
    db.insert("job_runs", {"job_id": jid, "started_at": to_iso(now_utc()), "finished_at": to_iso(now_utc()),
                           "outcome": "error", "message": "Traceback (most recent call last): boom"})
    page = login("admin@t.test").get("/automations").data.decode()
    assert "failed, will retry" in page and "Traceback" not in page


# -- PostgreSQL (Supabase) portability helpers ------------------------------------------------------------------
def test_postgres_query_text_and_numbers_are_converted():
    from decimal import Decimal

    from teamos.db import Database
    d = Database.__new__(Database)
    d.dialect = "postgres"
    assert d._sql("SELECT * FROM t WHERE a LIKE 'Lead ad%' AND b = ?") == \
        "SELECT * FROM t WHERE a LIKE 'Lead ad%%' AND b = %s"
    assert d._row({"total": Decimal("1500"), "avg": Decimal("2.5"), "name": "x"}) == {"total": 1500, "avg": 2.5,
                                                                                       "name": "x"}
    d.dialect = "sqlite"
    assert d._sql("a LIKE 'x%' AND b = ?") == "a LIKE 'x%' AND b = ?"


def test_copy_tool_orders_tables_so_links_exist_first(db):
    import importlib.util
    import sqlite3
    spec = importlib.util.spec_from_file_location("copy_to_postgres", "scripts/copy_to_postgres.py")
    cp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cp)
    src = sqlite3.connect(db.url[len("sqlite:///"):])
    order = cp.order_tables(src)
    assert order.index("users") < order.index("opportunities") < order.index("deals") < order.index("fee_invoices")
    assert cp.convert("42", "integer", "x") == 42 and cp.convert(7, "text", "x") == "7"
    import pytest
    with pytest.raises(SystemExit):
        cp.convert("20L", "integer", "seller_listings row 1 column asking_price")
