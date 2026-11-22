"""Milestone 3 — buyer / investor management.

Named after the PRD UAT scenarios assigned to M3 (UAT-02, 05, 09, 31) plus the business rules of
§7.1–7.3 / §7.2A: hard all-in ceiling, ranked localities and exclusions, blank vs "not important",
reviewer-only funding and criteria review, the seriousness score and the buyer funnel gates.
"""
import re

from conftest import PW, SELLER_FORM, user

from teamos.domain import buyers as B

BUYER_FORM = {"full_name": "Arjun Investor", "phone": "91234 56789", "city": "Hyderabad", "locality": "Kondapur",
              "source": "referral", "consent_contact": "1", "need_type": "acquire_operating",
              "decision_maker": "yes", "all_in_max": "35L", "purchase_window": "90d"}


def make_buyer(login, email="buyer@t.test", **over):
    c = login(email)
    r = c.post("/buyers/new", {**BUYER_FORM, **over})
    assert r.status_code == 302, r.data[:500]
    return c, int(r.headers["Location"].rstrip("/").split("/")[-1])


def opp(db, opp_id):
    return B.load(db, opp_id)


def mandate(db, opp_id):
    return db.one("SELECT m.* FROM buyer_mandates m WHERE m.opportunity_id = ?", (opp_id,))


def fill_qualifying_data(c, opp_id, cash_ready="now"):
    assert c.post(f"/buyers/{opp_id}/section/budget", {
        "all_in_max": "35L", "budget_min": "15L", "budget_max": "25L", "includes_deposit": "yes",
        "includes_stock": "yes", "includes_working_capital": "no", "finance_dependence": "none"}).status_code == 302
    assert c.post(f"/buyers/{opp_id}/section/funding", {"funding_source": "self", "cash_ready": cash_ready}
                  ).status_code == 302
    assert c.post(f"/buyers/{opp_id}/section/authority", {"payment_approver": "Self"}).status_code == 302


# -- UAT-02 ----------------------------------------------------------------------------
def test_uat02_buyer_submission_captured_in_review_queue_with_no_internal_approval(app, db, login):
    with app.test_request_context():
        opp_id = B.submit_respondent_form(db, {
            "full_name": "Meera Form", "phone": "9876500001", "city": "Hyderabad", "locality": "Gachibowli",
            "need_type": "acquire_operating", "all_in_max": "40L", "purchase_window": "30d", "chairs_min": "6",
            "consent_contact": "1"})
    o, m = opp(db, opp_id), mandate(db, opp_id)
    assert o["kind"] == "buyer" and o["stage"] == "new" and o["ref_code"].startswith("BY-")
    assert m["all_in_max"] == 40_00_000 and m["chairs_min"] == 6
    # buyer-declared, not reviewed, nothing approved
    prov = B.provenance(db, m["id"])
    assert prov["all_in_max"]["evidence_status"] == "buyer_declared" and prov["all_in_max"]["source"] == "public_form"
    assert m["funding_status"] == "not_reviewed" and m["criteria_reviewed_version"] is None
    # routed to the buyer team with a review task
    assert o["primary_owner_id"] == user(db, "buyer@t.test")["id"]
    task = db.one("SELECT * FROM tasks WHERE entity_id = ? AND status = 'open'", (opp_id,))
    assert task["task_type"] == "review" and task["assignee_id"] == o["primary_owner_id"]
    # appears in the criteria review queue
    assert b"Meera Form" in login("admin@t.test").get("/buyers/?queue=criteria").data


# -- UAT-09 ----------------------------------------------------------------------------
def test_uat09_buyer_cannot_self_approve_funding_or_qualification(app, db, login):
    with app.test_request_context():
        opp_id = B.submit_respondent_form(db, {
            "full_name": "Sneaky Buyer", "phone": "9876500002", "city": "Hyderabad", "need_type": "franchise",
            "all_in_max": "20L", "funding_status": "verified", "funding_verified": "yes",
            "qualified_by_salon_shastra": "yes", "introduction_authorised": "1", "stage": "qualified_shortlist",
            "criteria_reviewed_version": "1", "owner_id": "1"})
    o, m = opp(db, opp_id), mandate(db, opp_id)
    assert m["funding_status"] == "not_reviewed" and m["criteria_reviewed_version"] is None
    assert o["stage"] == "new"
    row = db.one("SELECT * FROM audit_events WHERE action = 'buyer.privileged_field_ignored' AND entity_id = ?",
                 (opp_id,))
    assert row and row["outcome"] == "denied"
    for f in ("funding_status", "funding_verified", "qualified_by_salon_shastra", "introduction_authorised", "stage"):
        assert f in row["detail"]


def test_uat09_staff_data_entry_and_crafted_posts_cannot_set_funding(db, login):
    c, opp_id = make_buyer(login)
    c.post(f"/buyers/{opp_id}/section/funding", {"funding_source": "self", "funding_status": "verified"})
    assert mandate(db, opp_id)["funding_status"] == "not_reviewed"
    assert mandate(db, opp_id)["funding_source"] == "self"  # the legitimate field still saved
    # crafted reviewer action by a team member
    r = c.post(f"/buyers/{opp_id}/funding", {"status": "verified", "note": "trust me"})
    assert r.status_code == 403
    assert mandate(db, opp_id)["funding_status"] == "not_reviewed"
    assert db.scalar("SELECT COUNT(*) FROM audit_events WHERE action = 'buyer.funding_review' AND outcome = 'denied'")


# -- UAT-05 ----------------------------------------------------------------------------
def test_uat05_one_contact_with_seller_and_buyer_needs_keeps_separate_opportunities(db, login):
    s = login("seller@t.test")
    assert s.post("/sellers/new", SELLER_FORM).status_code == 302
    seller_id = db.scalar("SELECT id FROM opportunities WHERE kind = 'seller'")
    contact_id = db.scalar("SELECT contact_id FROM opportunities WHERE id = ?", (seller_id,))
    b = login("buyer@t.test")
    r = b.post("/buyers/new", {**BUYER_FORM, "full_name": "Test Owner", "phone": SELLER_FORM["phone"]})
    assert r.status_code == 200 and b"Possible duplicate" in r.data  # nothing saved yet
    assert db.scalar("SELECT COUNT(*) FROM opportunities WHERE kind = 'buyer'") == 0
    r = b.post("/buyers/new", {**BUYER_FORM, "full_name": "Test Owner", "phone": SELLER_FORM["phone"],
                               "dedupe_decision": f"link:{contact_id}"})
    assert r.status_code == 302
    buyer_id = db.scalar("SELECT id FROM opportunities WHERE kind = 'buyer'")
    assert db.scalar("SELECT contact_id FROM opportunities WHERE id = ?", (buyer_id,)) == contact_id
    # a second, independent acquisition mandate for the same person
    r = b.post("/buyers/new", {**BUYER_FORM, "full_name": "Test Owner", "phone": SELLER_FORM["phone"],
                               "need_type": "new_salon", "dedupe_decision": f"link:{contact_id}"})
    second_id = int(r.headers["Location"].split("/")[-1])
    # moving the buyer does not touch the seller or the other mandate
    b.post(f"/buyers/{buyer_id}/call", {"outcome": "connected", "next_title": "Send form", "next_due": "2030-01-01T10:00",
                                       "complete_task_id": db.scalar("SELECT id FROM tasks WHERE entity_id = ?", (buyer_id,))})
    stages = {r["id"]: r["stage"] for r in db.all("SELECT id, stage FROM opportunities")}
    assert stages[buyer_id] == "connected" and stages[seller_id] == "new" and stages[second_id] == "new"
    # contact details were not overwritten; each opportunity has its own history
    assert db.scalar("SELECT full_name FROM contacts WHERE id = ?", (contact_id,)) == "Test Owner"
    page = b.get(f"/buyers/{buyer_id}").data.decode()
    assert f"/sellers/{seller_id}" in page and f"/buyers/{second_id}" in page


# -- UAT-31 ----------------------------------------------------------------------------
def test_uat31_seller_executive_sees_buyer_record_but_not_private_funding_evidence(db, login):
    b, opp_id = make_buyer(login)
    fill_qualifying_data(b, opp_id)
    b.post(f"/buyers/{opp_id}/preferences", {"pref__gender_format__unisex": "must"})
    lead = login("lead@t.test")
    secret = "Bank statement 10 Nov shows 32L; loan letter pending"
    assert lead.post(f"/buyers/{opp_id}/funding", {"status": "staff_reviewed", "note": secret}).status_code == 302
    seller_exec = login("seller@t.test")
    for tab in ("overview", "requirements", "qualification", "matches", "history"):
        r = seller_exec.get(f"/buyers/{opp_id}?tab={tab}")
        assert r.status_code == 200, tab
        assert secret.encode() not in r.data, tab
    qual = seller_exec.get(f"/buyers/{opp_id}?tab=qualification").data
    assert b"Staff-reviewed" in qual and b"Seriousness" in qual  # status + qualification are visible
    assert b"Must have" in seller_exec.get(f"/buyers/{opp_id}?tab=requirements").data
    assert b"No match history yet" in seller_exec.get(f"/buyers/{opp_id}?tab=matches").data
    # the reviewer sees the evidence; the finance role sees no buyer CRM at all
    assert secret.encode() in lead.get(f"/buyers/{opp_id}?tab=qualification").data
    fin = login("fin@t.test")
    assert fin.get("/buyers/").status_code == 403 and fin.get(f"/buyers/{opp_id}").status_code == 403


def test_funding_evidence_can_be_granted_for_one_case(db, login):
    b, opp_id = make_buyer(login)
    login("admin@t.test").post(f"/buyers/{opp_id}/funding", {"status": "verified", "note": "CA letter seen"})
    seller_exec = login("seller@t.test")
    assert b"CA letter seen" not in seller_exec.get(f"/buyers/{opp_id}?tab=qualification").data
    admin_id, seller_id = user(db, "admin@t.test")["id"], user(db, "seller@t.test")["id"]
    db.insert("user_grants", {"user_id": seller_id, "capability": "buyer.view_funding", "scope_type": "opportunity",
                              "scope_id": opp_id, "granted_by": admin_id, "reason": "covering", "created_at": "2026-01-01"})
    assert b"CA letter seen" in seller_exec.get(f"/buyers/{opp_id}?tab=qualification").data


# -- Mandate model ---------------------------------------------------------------------
def test_blank_preference_is_not_the_same_as_not_important(db, login):
    c, opp_id = make_buyer(login)
    c.post(f"/buyers/{opp_id}/preferences", {"pref__gender_format__men": "avoid", "pref__gender_format__unisex": "must",
                                             "pref__business_model__franchise": "not_important",
                                             "pref__gender_format__women": ""})
    prefs = B.preferences(db, mandate(db, opp_id)["id"])
    assert prefs[("gender_format", "men")] == "avoid" and prefs[("gender_format", "unisex")] == "must"
    assert prefs[("business_model", "franchise")] == "not_important"
    assert ("gender_format", "women") not in prefs and ("positioning", "luxury") not in prefs  # blank stays blank
    # scalar criterion importance
    c.post(f"/buyers/{opp_id}/section/salon", {"chairs_min": "6", "chairs_min__importance": "flexible"})
    m = mandate(db, opp_id)
    assert m["chairs_min"] == 6 and B.preferences(db, m["id"])[("chairs_min", "")] == "flexible"
    c.post(f"/buyers/{opp_id}/preferences", {"pref__gender_format__men": ""})
    assert ("gender_format", "men") not in B.preferences(db, m["id"])


def test_unknown_is_not_zero_and_ceiling_is_parsed(db, login):
    c, opp_id = make_buyer(login)
    c.post(f"/buyers/{opp_id}/section/business", {"max_monthly_rent": "", "max_monthly_rent__state": "unknown"})
    m = mandate(db, opp_id)
    assert m["max_monthly_rent"] is None
    assert B.provenance(db, m["id"])["max_monthly_rent"]["answer_state"] == "unknown"
    assert m["all_in_max"] == 35_00_000


def test_ranked_localities_and_exclusions(db, login):
    c, opp_id = make_buyer(login)  # Kondapur is #1 from the quick screen
    mid = mandate(db, opp_id)["id"]
    v0 = mandate(db, opp_id)["data_version"]
    for loc in ("Gachibowli", "Madhapur"):
        c.post(f"/buyers/{opp_id}/locality", {"action": "add", "kind": "preferred", "city": "Hyderabad", "locality": loc})
    c.post(f"/buyers/{opp_id}/locality", {"action": "add", "kind": "excluded", "city": "Hyderabad", "locality": "Old City"})
    r = c.post(f"/buyers/{opp_id}/locality", {"action": "add", "kind": "excluded", "city": "Hyderabad",
                                              "locality": "kondapur"}, follow_redirects=True)
    assert b"already listed as preferred" in r.data
    pref = B.localities(db, mid)["preferred"]
    assert [l["locality"] for l in pref] == ["Kondapur", "Gachibowli", "Madhapur"]
    madhapur = pref[2]["id"]
    c.post(f"/buyers/{opp_id}/locality", {"action": "up", "locality_id": madhapur})
    assert [l["locality"] for l in B.localities(db, mid)["preferred"]] == ["Kondapur", "Madhapur", "Gachibowli"]
    c.post(f"/buyers/{opp_id}/locality", {"action": "remove", "locality_id": pref[0]["id"]})
    locs = B.localities(db, mid)
    assert [(l["locality"], l["rank_order"]) for l in locs["preferred"]] == [("Madhapur", 1), ("Gachibowli", 2)]
    assert [l["locality"] for l in locs["excluded"]] == ["Old City"]
    assert mandate(db, opp_id)["data_version"] > v0


# -- Reviewer actions & funnel gates ---------------------------------------------------
def test_criteria_confirmation_goes_stale_when_requirements_change(db, login):
    c, opp_id = make_buyer(login)
    lead = login("lead@t.test")
    assert c.post(f"/buyers/{opp_id}/confirm-criteria").status_code == 403  # team member is not a reviewer
    lead.post(f"/buyers/{opp_id}/confirm-criteria")
    m = mandate(db, opp_id)
    assert B.criteria_review_state(m) == "current"
    c.post(f"/buyers/{opp_id}/section/budget", {"all_in_max": "40L"})
    assert B.criteria_review_state(mandate(db, opp_id)) == "stale"
    assert b"Changed since review" in c.get("/buyers/").data


def test_qualified_to_shortlist_needs_reviewed_funding_confirmed_criteria_and_a_reviewer(db, login):
    c, opp_id = make_buyer(login)
    fill_qualifying_data(c, opp_id)
    lead = login("lead@t.test")

    def why(u, to="qualified_shortlist"):
        o = opp(db, opp_id)
        m = mandate(db, opp_id)
        return B.check_transition(db, user(db, u), o, m, B.provenance(db, m["id"]), to)

    assert why("buyer@t.test") == (False, "Only a reviewer can qualify a buyer to shortlist")
    assert why("lead@t.test")[1] == "Funding must be staff-reviewed or verified"
    lead.post(f"/buyers/{opp_id}/funding", {"status": "staff_reviewed", "note": "Statement seen"})
    assert "confirmed by a reviewer" in why("lead@t.test")[1]
    lead.post(f"/buyers/{opp_id}/confirm-criteria")
    assert why("lead@t.test") == (True, None)
    r = lead.post(f"/buyers/{opp_id}/stage", {"to": "qualified_shortlist"})
    assert r.status_code == 302 and opp(db, opp_id)["stage"] == "qualified_shortlist"
    # stages that need Match Desk / Deals are blocked honestly, not simulated
    for to in ("shortlist_shared", "viewing_offer", "transaction", "closed_won"):
        ok, reason = why("admin@t.test", to)
        assert not ok and "Milestone" in reason


def test_new_salon_need_is_never_qualified_as_a_resale_buyer(db, login):
    c, opp_id = make_buyer(login, need_type="new_salon")
    fill_qualifying_data(c, opp_id)
    lead = login("lead@t.test")
    lead.post(f"/buyers/{opp_id}/funding", {"status": "verified", "note": "seen"})
    lead.post(f"/buyers/{opp_id}/confirm-criteria")
    r = lead.post(f"/buyers/{opp_id}/stage", {"to": "qualified_shortlist"}, follow_redirects=True)
    assert b"not resale buyers" in r.data and opp(db, opp_id)["stage"] == "new"


def test_basic_qualification_requires_consent_and_a_real_need(db, login):
    c, opp_id = make_buyer(login, consent_contact="", need_type="exploring")
    r = c.post(f"/buyers/{opp_id}/stage", {"to": "basic_qualified"}, follow_redirects=True)
    assert b"only exploring" in r.data
    c.post(f"/buyers/{opp_id}/section/intent", {"need_type": "acquire_operating"})
    r = c.post(f"/buyers/{opp_id}/stage", {"to": "basic_qualified"}, follow_redirects=True)
    assert b"consent" in r.data and opp(db, opp_id)["stage"] == "new"
    # holding stages need a reason and a follow-up date
    r = c.post(f"/buyers/{opp_id}/stage", {"to": "exploring_only", "reason": "Just looking"}, follow_redirects=True)
    assert b"follow-up date" in r.data
    c.post(f"/buyers/{opp_id}/stage", {"to": "exploring_only", "reason": "Just looking",
                                       "follow_up_due": "2030-02-01T10:00"})
    o = opp(db, opp_id)
    assert o["stage"] == "exploring_only" and o["status"] == "holding"
    assert db.scalar("SELECT COUNT(*) FROM tasks WHERE entity_id = ? AND status = 'open'", (opp_id,)) == 1


# -- Seriousness score (§7.3) ----------------------------------------------------------
def test_seriousness_missing_answers_earn_nothing_and_evidence_points_need_a_reviewer(db, login):
    c, opp_id = make_buyer(login, decision_maker="", all_in_max="", purchase_window="", need_type="")

    def score():
        o, m = opp(db, opp_id), mandate(db, opp_id)
        mid = m["id"]
        return B.seriousness_score(db, o, m, B.provenance(db, mid), B.localities(db, mid), B.preferences(db, mid))

    assert score()["score"] == 0 and score()["band"] == "Nurture / needs information"
    c.post(f"/buyers/{opp_id}/section/intent", {"need_type": "acquire_operating", "decision_maker": "yes"})
    c.post(f"/buyers/{opp_id}/section/timing", {"purchase_window": "30d", "visit_availability": "yes"})
    fill_qualifying_data(c, opp_id)
    c.post(f"/buyers/{opp_id}/preferences", {"pref__gender_format__unisex": "must"})
    tid = db.scalar("SELECT id FROM tasks WHERE entity_id = ? AND status = 'open'", (opp_id,))
    c.post(f"/buyers/{opp_id}/call", {"outcome": "connected", "complete_task_id": tid, "next_title": "Form",
                                      "next_due": "2030-01-01T10:00"})
    before = score()
    funding = next(f for f in before["factors"] if f["name"] == "Funding position documented")
    assert funding["points"] == 10  # declared only — no evidence points without a reviewer
    lead = login("lead@t.test")
    lead.post(f"/buyers/{opp_id}/funding", {"status": "verified", "note": "CA letter"})
    lead.post(f"/buyers/{opp_id}/confirm-criteria")
    after = score()
    assert after["score"] - before["score"] == 20  # +15 verified funding, +5 criteria reviewed
    assert after["band"] == "High priority" and after["reviewer"] == "Ravi Lead"
    assert all("reason" in f and f["max"] for f in after["factors"])
    assert sum(f["max"] for f in after["factors"]) == 100


# -- Pages -----------------------------------------------------------------------------
def test_buyer_pages_render_with_real_csrf_tokens_and_shared_screens_link_buyers(app, db, login):
    c, opp_id = make_buyer(login)
    browser = app.test_client()
    browser.post("/login", data={"email": "admin@t.test", "password": PW})
    for url in ["/buyers/", "/buyers/new", "/buyers/?queue=funding"] + \
               [f"/buyers/{opp_id}?tab={t}" for t in ("overview", "requirements", "qualification", "matches", "history")]:
        html = browser.get(url).data.decode()
        assert "Traceback" not in html, url
        forms = re.findall(r'<form[^>]*method="post"[^>]*>(.*?)</form>', html, re.S)
        for f in forms:
            m = re.search(r'name="csrf_token" value="([^"]*)"', f)
            assert m and m.group(1), f"form without a real CSRF token on {url}"
    # My Day / Team / Search / Tasks link to the buyer screen, not the seller screen
    for url in ("/team", f"/search?q={BUYER_FORM['full_name'].split()[0]}", "/tasks/?view=all"):
        assert f"/buyers/{opp_id}" in browser.get(url).data.decode(), url
    assert b"Buyer / investor mandate" in browser.get("/dictionary").data
    assert browser.get("/planned/buyers").headers["Location"].endswith("/buyers/")
