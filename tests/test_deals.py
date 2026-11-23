"""Milestone 6 — teasers, introductions, deal room, admin finance.

UAT-15 (approved introduction shares exactly permitted details, consent logged), UAT-17 (visit + versioned offer),
UAT-18 (₹20 L deal, 5% fee → ₹1 L, not ₹20 L revenue), UAT-19 (₹40,000 of ₹1 L collected → ₹60,000 due),
UAT-47 (finance-only user edits fee; ordinary users can't see amounts), UAT-50 (only approved teaser fields shared).
"""
import json
import re

from test_matching import buyer, cand, seller, set_seller

from conftest import user


def to_interest(db, login, s, b):
    c = cand(db, b, s)
    assert c["readiness"] == "ready_to_approach", c["readiness"]
    lead = login("lead@t.test")
    lead.post(f"/matches/{c['id']}/stage", {"to": "approved_to_approach"})
    lead.post(f"/matches/{c['id']}/stage", {"to": "interest_confirmed", "note": "Buyer keen"})
    assert db.scalar("SELECT stage FROM match_candidates WHERE id = ?", (c["id"],)) == "interest_confirmed"
    return c["id"]


def approved_teaser(db, login, s, headline="Premium unisex salon in West Hyderabad"):
    member, admin = login("seller@t.test"), login("admin@t.test")
    member.post(f"/deals/teasers/seller/{s}", {"headline": headline, "highlights": "Loyal clientele",
                                               "fields": ["area", "format", "size", "asking", "sales_band"]})
    tid = db.scalar("SELECT id FROM teasers WHERE seller_opp_id = ?", (s,))
    member.post(f"/deals/teasers/{tid}/seller-approval", {"method": "call"})
    admin.post(f"/deals/teasers/{tid}/approve")
    assert db.scalar("SELECT status FROM teasers WHERE id = ?", (tid,)) == "approved"
    return tid


def share(login, cid):
    r = login("buyer@t.test").post(f"/deals/match/{cid}/share-teaser", {"channel": "copy_link"})
    return re.search(r"/t/([A-Za-z0-9_\-]+)", r.data.decode()).group(1)


def introduce(db, login, cid):
    m = login("buyer@t.test")
    for side in ("buyer", "seller"):
        m.post(f"/deals/match/{cid}/consent", {"side": side, "method": "call"})
    m.post(f"/deals/match/{cid}/introduce", {"note": "Both keen"})
    iid = db.scalar("SELECT id FROM introductions WHERE match_id = ?", (cid,))
    r = login("admin@t.test").post(f"/deals/introductions/{iid}", {"decision": "approved"})
    assert r.status_code == 302 and "/deals/" in r.headers["Location"], r.data[:300]
    return db.scalar("SELECT id FROM deals WHERE match_id = ?", (cid,))


def full_deal(db, login):
    s = seller(db, asking_price="20L", address_line="Plot 7, Test Road")
    b = buyer(db)
    cid = to_interest(db, login, s, b)
    approved_teaser(db, login, s)
    share(login, cid)
    return s, b, cid, introduce(db, login, cid)


# -- UAT-50 -----------------------------------------------------------------------------------
def test_uat50_only_approved_anonymous_teaser_fields_are_shared(app, db, login):
    s = seller(db, salon="Glow Secret Salon", asking_price="20L", address_line="Plot 7, Test Road",
               avg_monthly_sales="10.5L", franchise_brand="", negotiable="yes", negotiable_min_price="17L")
    b = buyer(db)
    cid = to_interest(db, login, s, b)
    member = login("seller@t.test")
    r = member.post(f"/deals/teasers/seller/{s}", {"headline": "Glow Secret Salon for sale", "fields": ["area"]},
                    follow_redirects=True)
    assert b"must stay anonymous" in r.data  # identity can't sneak in via the headline
    r = member.post(f"/deals/match/{cid}/share-teaser", {"channel": "copy_link"}, follow_redirects=True)
    assert b"no approved teaser" in r.data  # nothing can be shared before seller + reviewer approval
    approved_teaser(db, login, s)
    set_seller(db, s, chairs="12")  # listing edits after approval must not leak into the approved teaser
    token = share(login, cid)
    page = app.test_client().get(f"/t/{token}")
    html = page.data.decode()
    assert page.status_code == 200 and "noindex" in page.headers["X-Robots-Tag"]
    assert "Premium unisex salon" in html and "₹20 L" in html and "₹10 L – ₹12 L" in html  # banded sales
    for secret in ("Glow Secret Salon", "Owner Glow", "9000011001", "Plot 7", "17", "12 chairs"):
        assert secret not in html, secret
    assert db.scalar("SELECT opened_at FROM teaser_shares") is not None
    assert app.test_client().get("/t/forged-token").status_code == 404


# -- UAT-15 -----------------------------------------------------------------------------------
def test_uat15_introduction_needs_consent_and_approver_and_logs_exactly_what_was_shared(db, login):
    s = seller(db, asking_price="20L", address_line="Plot 7, Test Road")
    b = buyer(db)
    cid = to_interest(db, login, s, b)
    approved_teaser(db, login, s)
    share(login, cid)
    m = login("buyer@t.test")
    m.post(f"/deals/match/{cid}/introduce", {})
    iid = db.scalar("SELECT id FROM introductions")
    admin = login("admin@t.test")
    r = admin.post(f"/deals/introductions/{iid}", {"decision": "approved"}, follow_redirects=True)
    assert b"no buyer consent to share identity" in r.data and b"no seller consent" in r.data
    assert db.scalar("SELECT COUNT(*) FROM deals") == 0
    for side in ("buyer", "seller"):
        m.post(f"/deals/match/{cid}/consent", {"side": side, "method": "call"})
    assert m.post(f"/deals/introductions/{iid}", {"decision": "approved"}).status_code == 403  # approver only
    admin.post(f"/deals/introductions/{iid}", {"decision": "approved"})
    intro = db.one("SELECT * FROM introductions WHERE id = ?", (iid,))
    shared, consents = json.loads(intro["details_shared"]), json.loads(intro["consent_snapshot"])
    assert intro["status"] == "approved" and intro["decided_by"] == user(db, "admin@t.test")["id"]
    assert shared["to_buyer"]["Address"] == "Plot 7, Test Road" and "Buyer phone" in shared["to_seller"]
    assert set(shared["to_seller"]) == {"Buyer contact", "Buyer phone"}  # nothing about the buyer's finances
    assert consents["buyer"]["text_version"] == "identity-share-v0.1" and consents["seller"]["granted"] == 1
    assert db.scalar("SELECT COUNT(*) FROM deals") == 1
    assert db.scalar("SELECT COUNT(*) FROM audit_events WHERE action = 'intro.approved'") == 1


# -- UAT-17 -----------------------------------------------------------------------------------
def test_uat17_visit_and_versioned_offers_with_timeline_and_tasks(db, login):
    s, b, cid, deal = full_deal(db, login)
    m = login("seller@t.test")
    m.post(f"/deals/{deal}/visit", {"when": "2030-01-05T11:00", "attendees": "Buyer + Shaheen"})
    assert db.scalar("SELECT stage FROM deals") == "visit_scheduled"
    assert db.scalar("SELECT COUNT(*) FROM tasks WHERE entity_type = 'deal' AND task_type = 'visit' "
                     "AND title LIKE 'Site visit%'") == 1
    vid = db.scalar("SELECT id FROM deal_visits")
    r = m.post(f"/deals/visits/{vid}", {"status": "completed"}, follow_redirects=True)
    assert b"Note what happened" in r.data
    m.post(f"/deals/visits/{vid}", {"status": "completed", "outcome": "Liked the location", "feedback": "Rent high"})
    assert db.scalar("SELECT stage FROM deals") == "visit_completed"
    m.post(f"/deals/{deal}/offer", {"from_party": "buyer", "amount": "18L", "valid_until": "2030-01-20"})
    m.post(f"/deals/{deal}/offer", {"from_party": "seller", "amount": "20L", "financing": "Own funds"})
    offers = db.all("SELECT version, amount, status FROM deal_offers ORDER BY version")
    assert [(o["version"], o["amount"], o["status"]) for o in offers] == [(1, 18_00_000, "countered"),
                                                                          (2, 20_00_000, "open")]
    assert db.scalar("SELECT stage FROM deals") == "negotiation"
    m.post(f"/deals/offers/{db.scalar('SELECT id FROM deal_offers WHERE version = 2')}", {"status": "accepted"})
    d = db.one("SELECT * FROM deals")
    assert d["agreed_consideration"] == 20_00_000 and d["stage"] == "due_diligence"
    assert db.scalar("SELECT COUNT(*) FROM tasks WHERE entity_type = 'deal' AND status = 'open' "
                     "AND title LIKE 'Complete due diligence%'") == 1  # the deal always has a next step
    # no stale to-dos: scheduling closed "arrange a visit"; the counter-offer closed "respond to v1"
    open_titles = [r["title"] for r in db.all("SELECT title FROM tasks WHERE entity_type = 'deal' AND status = 'open'")]
    assert not any(t.startswith("Arrange a site visit") for t in open_titles)
    assert not any(t.startswith("Respond to offer v1") for t in open_titles)
    # offer validity dates become proper task deadlines; every page that lists those tasks still renders
    assert db.scalar("SELECT due_at FROM tasks WHERE title LIKE 'Respond to offer v1%'").endswith("Z")
    for email, url in (("seller@t.test", f"/deals/{deal}"), ("buyer@t.test", "/"), ("seller@t.test", "/"),
                       ("admin@t.test", "/team"), ("admin@t.test", "/tasks/?view=all")):
        assert login(email).get(url).status_code == 200, url
    events = [e["body"] or "" for e in db.all("SELECT body FROM deal_events WHERE deal_id = ?", (deal,))]
    assert any("Offer v1" in e for e in events) and any("Visit completed" in e for e in events)


def close_won(db, login, deal):
    m = login("seller@t.test")
    m.post(f"/deals/{deal}/offer", {"from_party": "buyer", "amount": "20L"})
    m.post(f"/deals/offers/{db.scalar('SELECT id FROM deal_offers WHERE deal_id = ? ORDER BY version DESC', (deal,))}",
           {"status": "accepted"})
    r = login("admin@t.test").post(f"/deals/{deal}/stage", {"to": "closed_won"}, follow_redirects=True)
    assert b"Due diligence still open" in r.data
    for key in ("ownership", "lease", "financials", "liabilities", "prepaid", "equipment", "employees", "tax_legal", "brand"):
        m.post(f"/deals/{deal}/check", {"item_key": key, "status": "reviewed"})
    r = m.post(f"/deals/{deal}/stage", {"to": "closed_won"}, follow_redirects=True)
    assert b"Only an approver" in r.data or b"handover date" in r.data
    m.post(f"/deals/{deal}/dates", {"agreement_date": "2030-02-01", "handover_date": "2030-02-15"})
    login("admin@t.test").post(f"/deals/{deal}/stage", {"to": "closed_won"})
    assert db.scalar("SELECT stage FROM deals WHERE id = ?", (deal,)) == "closed_won"


def test_closing_needs_diligence_handover_and_an_approver_and_closes_both_records(db, login):
    s, b, cid, deal = full_deal(db, login)
    close_won(db, login, deal)
    for oid in (s, b):
        assert db.one("SELECT stage, status FROM opportunities WHERE id = ?", (oid,)) == {"stage": "closed_won",
                                                                                           "status": "closed"}


def test_closed_lost_requires_reason_and_lesson(db, login):
    s, b, cid, deal = full_deal(db, login)
    m = login("seller@t.test")
    r = m.post(f"/deals/{deal}/stage", {"to": "closed_lost"}, follow_redirects=True)
    assert b"needs a reason" in r.data
    m.post(f"/deals/{deal}/stage", {"to": "closed_lost", "reason": "financing", "lessons": "Check funding earlier"})
    d = db.one("SELECT * FROM deals")
    assert d["stage"] == "closed_lost" and d["closed_reason"] == "Buyer financing" and d["lessons"]


# -- UAT-18 / UAT-19 / UAT-47 -----------------------------------------------------------------
def test_uat18_uat19_fee_is_revenue_not_the_sale_price_and_due_reconciles(db, login):
    s, b, cid, deal = full_deal(db, login)
    fin = login("fin@t.test")  # finance specialist edits fees (UAT-47)
    fin.post("/admin/finance/agreement", {"deal_id": deal, "payer": "seller", "fee_type": "success",
                                          "basis": "percent", "rate_pct": "5", "earning_condition": "on_closing"})
    from teamos.domain import finance as FN
    L = FN.ledger(db, user(db, "fin@t.test"))
    assert L["rows"][0]["calc"]["earned"] == 0  # not earned until the deal closes
    close_won(db, login, deal)
    aid = db.scalar("SELECT id FROM fee_agreements")
    fin.post("/admin/finance/receipt", {"agreement_id": aid, "amount": "40000", "received_on": "2030-02-20",
                                        "mode": "UPI"})
    L = FN.ledger(db, user(db, "fin@t.test"))
    row = L["rows"][0]["calc"]
    assert row["contractual"] == 1_00_000 and row["earned"] == 1_00_000  # 5% of ₹20 L
    assert row["received"] == 40_000 and row["due"] == 60_000
    assert L["totals"]["earned"] == 1_00_000 and L["totals"]["consideration_closed"] == 20_00_000
    page = fin.get("/admin/finance").data.decode()
    assert "₹1 L" in page and "₹60,000" in page and "never</strong> Salon Shastra revenue" in page


def test_uat47_finance_edits_fees_ordinary_staff_cannot_see_or_change_amounts(db, login):
    s, b, cid, deal = full_deal(db, login)
    fin = login("fin@t.test")
    fin.post("/admin/finance/agreement", {"deal_id": deal, "payer": "buyer", "fee_type": "success", "basis": "fixed",
                                          "fixed_amount": "75000", "earning_condition": "on_closing"})
    fin.post("/admin/finance/agreement", {"deal_id": deal, "payer": "buyer", "fee_type": "success", "basis": "fixed",
                                          "fixed_amount": "80000", "earning_condition": "on_closing"})  # new terms
    rows = db.all("SELECT version, status, fixed_amount FROM fee_agreements ORDER BY version")
    assert [(r["version"], r["status"]) for r in rows] == [(1, "superseded"), (2, "active")]
    member = login("seller@t.test")
    assert member.get("/admin/finance").status_code == 403
    assert member.post("/admin/finance/agreement", {"deal_id": deal, "payer": "buyer", "fee_type": "other",
                                                     "basis": "fixed", "fixed_amount": "1",
                                                     "earning_condition": "manual"}).status_code == 403
    html = member.get(f"/deals/{deal}").data.decode()
    assert "Commercial terms recorded" in html and "80,000" not in html and "75,000" not in html
    assert db.scalar("SELECT COUNT(*) FROM audit_events WHERE outcome = 'denied' AND detail LIKE '%finance%'") >= 1
    assert fin.get(f"/deals/{deal}").status_code == 403  # finance sees deal references only, not the CRM deal room


# -- Pages -----------------------------------------------------------------------------------
def test_m6_pages_render_with_real_csrf_tokens(app, db, login):
    s, b, cid, deal = full_deal(db, login)
    from conftest import PW
    br = app.test_client()
    br.post("/login", data={"email": "admin@t.test", "password": PW})
    for url in ("/deals/", f"/deals/{deal}", f"/matches/{cid}", f"/sellers/{s}?tab=teaser", "/admin/finance"):
        html = br.get(url).data.decode()
        assert "Traceback" not in html, url
        for f in re.findall(r'<form[^>]*method="post"[^>]*>(.*?)</form>', html, re.S):
            m = re.search(r'name="csrf_token" value="([^"]*)"', f)
            assert m and m.group(1), url
    assert br.get("/planned/deals").headers["Location"].endswith("/deals/")


def test_teaser_tab_preview_shows_every_available_item(db, login):
    s = seller(db, avg_monthly_sales="10.5L")
    html = login("seller@t.test").get(f"/sellers/{s}?tab=teaser").data.decode()
    assert "₹10 L – ₹12 L" in html  # a non-default item is previewed with its real value, not "(no data yet)"


def test_finance_user_cannot_see_match_or_deal_tasks(db, login):
    """QA finding: match/deal tasks carry buyer and salon names, so they need CRM access like seller tasks."""
    s, b, cid, deal = full_deal(db, login)
    login("lead@t.test").post(f"/matches/{cid}/tasks", {"buyer_task": "Check funds", "seller_task": "Check lease"})
    fin = login("fin@t.test")
    html = fin.get("/tasks/?view=all").data.decode()
    assert "Resolve match" not in html and "Site visit" not in html and "Test Salon" not in html
    for tid in [r["id"] for r in db.all("SELECT id FROM tasks WHERE entity_type IN ('match','deal')")]:
        assert fin.get(f"/tasks/{tid}").status_code == 403
