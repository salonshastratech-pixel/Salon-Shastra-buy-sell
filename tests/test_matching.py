"""Milestone 5 — matching engine + Match Desk.

Covers PRD §8.7 concrete examples 1–10 and UAT-10, 11, 12, 13, 14, 35, 36, 37, 38, 39, 49.
"""
import re

from conftest import PW, user

from teamos.domain import buyer_fields as BFD
from teamos.domain import buyers as B
from teamos.domain import matching as M
from teamos.domain import sellers as S
from teamos.domain import tasks as T
from teamos.timeutil import now_iso


# -- builders (domain-level, so each test states exactly the facts that matter) -------------
def seller(db, phone="9000011001", salon="Test Salon", city="Hyderabad", locality="Kondapur", owner="seller@t.test",
           approved=True, share=True, **facts):
    admin = user(db, "admin@t.test")
    base = {"full_name": f"Owner {salon}", "phone": phone, "salon_name": salon, "city": city, "locality": locality,
            "source": "manual_call", "consent_contact": "1", "owner_id": str(user(db, owner)["id"]),
            "operating_status": "operating", "ownership_role": "owner", "sale_purpose": "sell_entire",
            "asking_price": "20L", "sale_timeline": "within_3m"}
    opp_id = S.create_lead(db, admin, {**base, **{k: v for k, v in facts.items() if k in base}})["opportunity_id"]
    defaults = {"transfer_authority": "yes", "gender_format": "unisex", "business_model": "independent",
                "positioning": "premium", "chairs": "8", "carpet_area_sqft": "1200", "monthly_rent": "80000",
                "rent_deposit": "5L", "deposit_treatment": "included_in_asking", "lease_expiry": "2030-01-01",
                "lease_renewal": "yes", "lease_transfer_status": "confirmed_transferable",
                "stock_value_separate__state": "not_applicable", "avg_monthly_sales": "10L",
                "avg_monthly_expenses": "7L", "staff_continuity": "yes", "transition_support": "yes",
                "operating_years": "5", "equipment_condition": "good", "negotiable": "no", "outstanding_loans": "0",
                "litigation": "no", "sale_structure": "entity"}
    set_seller(db, opp_id, **{**defaults, **{k: v for k, v in facts.items() if k not in base}})
    if approved:
        db.execute("UPDATE seller_listings SET approval_state = 'approved' WHERE opportunity_id = ?", (opp_id,))
    if share:
        consent(db, opp_id, "share_teaser")
    return opp_id


def set_seller(db, opp_id, **kv):
    admin = user(db, "admin@t.test")
    o = S.load(db, opp_id)
    keys = sorted({k.replace("__state", "") for k in kv})
    values, errors = S._parse_fields(kv, keys)
    assert not errors, errors
    S._apply_values(db, admin, o["listing_id"], values, source="test")


def buyer(db, phone="9100011001", name="Test Buyer", owner="buyer@t.test", ceiling="40L", city="Hyderabad",
          localities=("Kondapur",), excluded=(), prefs=None, confirmed=True, funding="verified", share=True, **facts):
    admin = user(db, "admin@t.test")
    opp_id = B.create_lead(db, admin, {"full_name": name, "phone": phone, "city": city, "source": "referral",
                                       "consent_contact": "1", "owner_id": str(user(db, owner)["id"]),
                                       "need_type": facts.pop("need_type", "acquire_operating"), "decision_maker": "yes",
                                       "all_in_max": ceiling, "purchase_window": "90d"})["opportunity_id"]
    o = B.load(db, opp_id)
    for i, loc in enumerate(localities):
        B.add_locality(db, admin, o, "preferred", city, loc)
    for loc in excluded:
        B.add_locality(db, admin, o, "excluded", city, loc)
    set_buyer(db, opp_id, **facts)
    B.set_choice_preferences(db, admin, B.load(db, opp_id),
                             {f"pref__{k}": v for k, v in (prefs or {"gender_format__unisex": "must"}).items()})
    if funding:
        db.execute("UPDATE buyer_mandates SET funding_status = ? WHERE opportunity_id = ?", (funding, opp_id))
    if confirmed:
        db.execute("UPDATE buyer_mandates SET criteria_reviewed_version = data_version WHERE opportunity_id = ?",
                   (opp_id,))
    if share:
        consent(db, opp_id, "share_teaser")
    return opp_id


def set_buyer(db, opp_id, **kv):
    admin = user(db, "admin@t.test")
    by_section = {}
    for k, v in kv.items():
        base = k.split("__")[0]
        by_section.setdefault(BFD.BY_KEY[base].section, {})[k] = v
    for sec, data in by_section.items():
        B.update_fields(db, admin, B.load(db, opp_id), data, sec)


def consent(db, opp_id, purpose, granted=1):
    cid = db.scalar("SELECT contact_id FROM opportunities WHERE id = ?", (opp_id,))
    db.insert("contact_consents", {"contact_id": cid, "purpose": purpose, "channel": "any", "granted": granted,
                                   "source": "test", "captured_at": now_iso()})


def cand(db, b, s, refresh=True):
    if refresh:
        M.refresh(db)
    return db.one("SELECT * FROM match_candidates WHERE buyer_opp_id = ? AND seller_opp_id = ?", (b, s))


def factor(db, c, key):
    return next(f for f in M.latest_result(db, c["id"])["factors"] if f["key"] == key)


# -- §8.7 ex.1 / UAT-10 --------------------------------------------------------------------
def test_ex1_uat10_all_in_over_ceiling_is_a_hard_conflict(db, login):
    s = seller(db, asking_price="20L", rent_deposit="5L", deposit_treatment="buyer_pays_separately")
    b = buyer(db, ceiling="18L")
    c = cand(db, b, s)
    assert c["all_in_total"] == 25_00_000 and c["all_in_complete"] == 1
    assert c["gate"] == "blocked" and c["outcome"] == "blocked" and c["readiness"] == "blocked"
    assert "₹25 L all-in" in c["headline"] and "₹18 L" in c["headline"]
    assert factor(db, c, "all_in")["result"] == "miss"
    r = login("lead@t.test").post(f"/matches/{c['id']}/stage", {"to": "approved_to_approach"}, follow_redirects=True)
    assert b"Cannot approve" in r.data
    assert db.scalar("SELECT stage FROM match_candidates WHERE id = ?", (c["id"],)) == "suggested"


def test_unknown_deposit_never_counts_as_zero(db):
    s = seller(db, asking_price="15L", rent_deposit="", rent_deposit__state="unknown",
               deposit_treatment="buyer_pays_separately")
    b = buyer(db, ceiling="40L")
    c = cand(db, b, s)
    assert c["all_in_complete"] == 0 and factor(db, c, "all_in")["result"] == "unknown"
    assert "rent deposit" in c["next_question"].lower()


# -- §8.7 ex.2 / UAT-39 --------------------------------------------------------------------
def test_ex2_uat39_chairs_must_fails_but_prefer_is_a_near_match(db):
    s = seller(db, chairs="4")
    b = buyer(db, chairs_min="6", chairs_min__importance="must")
    c = cand(db, b, s)
    assert c["gate"] == "blocked" and "Chairs" in c["headline"]
    set_buyer(db, b, chairs_min="6", chairs_min__importance="prefer")
    c = cand(db, b, s)
    assert c["gate"] == "pass" and c["outcome"] == "near"
    f = factor(db, c, "chairs")
    assert f["result"] == "partial" and not f["hard"] and "4 vs 6" in f["note"]


# -- §8.7 ex.3 / UAT-11 --------------------------------------------------------------------
def test_ex3_uat11_format_and_city_conflicts_are_explicit_not_a_false_high_match(db):
    s = seller(db, city="Bengaluru", locality="Indiranagar", gender_format="men", positioning="value")
    b = buyer(db, localities=("Jubilee Hills",), prefs={"gender_format__women": "must", "positioning__premium": "prefer"})
    c = cand(db, b, s)
    r = M.latest_result(db, c["id"])
    texts = " | ".join(g["text"] for g in r["gates"])
    assert c["gate"] == "blocked" and "City" in texts and "Format" in texts
    assert c["readiness"] != "ready_to_approach" and c["fit_score"] < 70


# -- §8.7 ex.4 ---------------------------------------------------------------------------------
def test_ex4_short_lease_without_renewal_is_a_severe_blocker_whatever_the_score(db):
    from datetime import timedelta
    from teamos.timeutil import now_utc
    expiry = (now_utc() + timedelta(days=120)).date().isoformat()
    s = seller(db, lease_expiry=expiry, lease_renewal="no")
    b = buyer(db, min_lease_years_left="1", min_lease_years_left__importance="prefer")
    c = cand(db, b, s)
    assert c["gate"] == "blocked" and "Lease" in c["headline"]
    s2 = seller(db, phone="9000011002", salon="Other", lease_transfer_status="not_permitted")
    assert cand(db, b, s2)["gate"] == "blocked"


# -- §8.7 ex.5 ---------------------------------------------------------------------------------
def test_ex5_missing_cost_months_make_economics_uncertain_and_no_roi_claims(db, login):
    s = seller(db)
    admin = user(db, "admin@t.test")
    for m, r, e in [("2026-04", "10L", "7L"), ("2026-05", "10L", "7L"), ("2026-06", "11L", "7L"),
                    ("2026-07", "10L", ""), ("2026-08", "12L", ""), ("2026-09", "11L", "")]:
        S.save_month(db, admin, S.load(db, s), m, r, e, "", "seller_sheet")
    b = buyer(db, min_monthly_sales="8L", min_monthly_sales__importance="prefer")
    c = cand(db, b, s)
    r = M.latest_result(db, c["id"])
    assert r["uncertain_economics"] and c["confidence"] != "high"
    assert "uncertain" in factor(db, c, "sales")["note"]
    page = login("admin@t.test").get(f"/matches/{c['id']}").data.decode().lower()
    assert "economics uncertain" in page
    for claim in ("roi", "payback period", "return on investment", "guaranteed"):
        assert claim not in page.replace("never estimates returns or payback", "")


# -- §8.7 ex.6 ---------------------------------------------------------------------------------
def test_ex6_adjacent_locality_is_partial_with_a_confirmation_question(db):
    s = seller(db, locality="Kokapet")
    b = buyer(db, localities=("Gachibowli", "Kondapur"))
    c = cand(db, b, s)
    f = factor(db, c, "locality")
    assert f["result"] == "partial" and c["gate"] == "pass"
    qs = [q["text"] for q in M.latest_result(db, c["id"])["questions"]]
    assert any("consider Kokapet" in q for q in qs)
    s2 = seller(db, phone="9000011003", salon="Excl", locality="Old City")
    b2 = buyer(db, phone="9100011003", excluded=("Old City",))
    assert cand(db, b2, s2)["gate"] == "blocked"


# -- §8.7 ex.7 / UAT-13 ------------------------------------------------------------------------
def test_ex7_uat13_perfect_fit_without_consent_cannot_be_approached(db, login):
    s = seller(db, share=False)
    b = buyer(db)
    c = cand(db, b, s)
    assert c["gate"] == "pass" and c["fit_score"] >= 90 and c["readiness"] == "awaiting_consent"
    r = login("lead@t.test").post(f"/matches/{c['id']}/stage", {"to": "approved_to_approach"}, follow_redirects=True)
    assert b"awaiting consent" in r.data.lower() and b"seller consent to share details" in r.data
    assert db.scalar("SELECT COUNT(*) FROM audit_events WHERE action = 'match.approve_blocked'") == 1
    consent(db, s, "share_teaser")
    c = cand(db, b, s)
    assert c["readiness"] == "ready_to_approach"
    # a do-not-contact request is a hard gate regardless of fit
    db.execute("UPDATE contacts SET do_not_contact = 1 WHERE id = (SELECT contact_id FROM opportunities WHERE id = ?)",
               (b,))
    assert cand(db, b, s)["gate"] == "blocked"


def test_approve_to_approach_is_reviewer_only_and_interest_follows_approval(db, login):
    s, b = seller(db), buyer(db)
    c = cand(db, b, s)
    assert c["readiness"] == "ready_to_approach"
    member = login("buyer@t.test")
    member.post(f"/matches/{c['id']}/stage", {"to": "interest_confirmed", "note": "x"})
    assert db.scalar("SELECT stage FROM match_candidates") == "suggested"
    assert member.post(f"/matches/{c['id']}/stage", {"to": "approved_to_approach"}).status_code == 403
    login("lead@t.test").post(f"/matches/{c['id']}/stage", {"to": "approved_to_approach"})
    assert db.scalar("SELECT stage FROM match_candidates") == "approved_to_approach"
    member.post(f"/matches/{c['id']}/stage", {"to": "interest_confirmed", "note": "Buyer keen after teaser call"})
    assert db.scalar("SELECT stage FROM match_candidates") == "interest_confirmed"


# -- §8.7 ex.8 / UAT-14 / UAT-36 -------------------------------------------------------------
def test_ex8_uat14_uat36_changes_mark_stale_rescore_and_keep_history(db, login):
    s = seller(db, asking_price="20L", rent_deposit="3L", deposit_treatment="buyer_pays_separately")
    b = buyer(db, ceiling="25L")
    c = cand(db, b, s)
    assert c["all_in_total"] == 23_00_000 and c["gate"] == "pass"
    set_seller(db, s, rent_deposit="8L")  # seller edits deposit after scoring
    assert M.mark_stale(db) == 1 and cand(db, b, s, refresh=False)["stale"] == 1
    assert b"Facts changed since scoring" in login("admin@t.test").get(f"/matches/{c['id']}").data
    c2 = cand(db, b, s)
    assert c2["stale"] == 0 and c2["all_in_total"] == 28_00_000 and c2["gate"] == "blocked"
    snaps = db.all("SELECT * FROM match_snapshots WHERE candidate_id = ? ORDER BY id", (c["id"],))
    assert len(snaps) == 2 and snaps[0]["seller_version"] < snaps[1]["seller_version"]  # prior snapshot preserved
    # UAT-36: buyer raises the ceiling — opening the buyer's Matches tab recomputes
    set_buyer(db, b, all_in_max="30L")
    login("buyer@t.test").get(f"/buyers/{b}?tab=matches")
    c3 = cand(db, b, s, refresh=False)
    assert c3["gate"] == "pass" and c3["buyer_ceiling"] == 30_00_000
    assert db.scalar("SELECT COUNT(*) FROM match_snapshots WHERE candidate_id = ?", (c["id"],)) == 3


# -- §8.7 ex.9 / UAT-35 ------------------------------------------------------------------------
def test_ex9_uat35_split_tasks_with_separate_owners_no_duplicates_parent_closes(db, login):
    s = seller(db, lease_expiry="", lease_expiry__state="unknown")
    b = buyer(db)
    c = cand(db, b, s)
    lead = login("lead@t.test")
    form = {"buyer_task": "Collect funding proof", "seller_task": "Collect lease expiry", "due_at": "2030-01-01T10:00"}
    lead.post(f"/matches/{c['id']}/tasks", form)
    lead.post(f"/matches/{c['id']}/tasks", form)  # second click must not create duplicate calls
    parent = db.one("SELECT * FROM tasks WHERE entity_type = 'match'")
    kids = db.all("SELECT * FROM tasks WHERE parent_task_id = ? ORDER BY id", (parent["id"],))
    assert len(kids) == 2
    assert kids[0]["assignee_id"] == user(db, "buyer@t.test")["id"] and kids[0]["entity_id"] == b
    assert kids[1]["assignee_id"] == user(db, "seller@t.test")["id"] and kids[1]["entity_id"] == s
    T.complete_task(db, user(db, "buyer@t.test"), kids[0]["id"], "Statement received")
    assert db.scalar("SELECT status FROM tasks WHERE id = ?", (parent["id"],)) == "open"
    T.complete_task(db, user(db, "seller@t.test"), kids[1]["id"], "Expiry March 2029")
    assert db.scalar("SELECT status FROM tasks WHERE id = ?", (parent["id"],)) == "done"
    page = lead.get(f"/matches/{c['id']}").data.decode()
    assert "2/2 done" in page


def test_ex9_one_salon_many_buyers_shows_owner_and_last_contact(db, login):
    s = seller(db)
    for i in range(4):
        buyer(db, phone=f"91000120{i:02d}", name=f"Buyer {i}")
    M.refresh(db)
    html = login("seller@t.test").get(f"/sellers/{s}?tab=matches").data.decode()
    assert html.count("/matches/") >= 4 and "Buyer side: Harika Buyer" in html


# -- §8.7 ex.10 / UAT-49 -----------------------------------------------------------------------
def test_ex10_uat49_declined_match_is_suppressed_until_facts_change(db, login):
    s, b = seller(db), buyer(db)
    c = cand(db, b, s)
    member = login("buyer@t.test")
    r = member.post(f"/matches/{c['id']}/stage", {"to": "buyer_declined"}, follow_redirects=True)
    assert b"Choose why" in r.data  # a reason is mandatory
    member.post(f"/matches/{c['id']}/stage", {"to": "buyer_declined", "reason": "rent", "note": "Rent too high"})
    c = cand(db, b, s)  # refresh with no change
    assert c["suppressed"] == 1 and c["suppressed_reason"] == "rent"
    assert b'class="match-card' not in member.get("/matches/?queue=all").data  # gone from active queues
    assert b'class="match-card' in member.get("/matches/?queue=suppressed").data
    set_seller(db, s, monthly_rent="60000")  # revised facts
    c = cand(db, b, s)
    assert c["suppressed"] == 0 and c["stage"] == "suggested"
    ev = db.one("SELECT * FROM match_events WHERE event = 'resurfaced'")
    assert ev and "changed" in ev["body"]


# -- UAT-12 --------------------------------------------------------------------------------------
def test_uat12_missing_chair_count_reduces_coverage_and_asks_for_it(db, login):
    b = buyer(db, chairs_min="6", chairs_min__importance="prefer")
    known = seller(db, chairs="8")
    unknown = seller(db, phone="9000011004", salon="No Chairs", chairs="", chairs__state="unknown")
    ck, cu = cand(db, b, known), cand(db, b, unknown)
    assert cu["coverage_pct"] < ck["coverage_pct"] and factor(db, cu, "chairs")["result"] == "unknown"
    page = login("admin@t.test").get(f"/matches/{cu['id']}").data.decode()
    assert "Collect the salon&#39;s chairs" in page or "Collect the salon's chairs" in page
    assert "Ask the owner these by form" in page


def test_request_missing_answer_sends_a_targeted_form_to_the_right_side(db, login):
    b = buyer(db, chairs_min="6", chairs_min__importance="prefer")  # the buyer cares, so the salon's chairs matter
    s = seller(db, chairs="", chairs__state="unknown")
    c = cand(db, b, s)
    r = login("seller@t.test").post(f"/matches/{c['id']}/ask/seller", {"channel": "copy_link"})
    assert r.status_code == 200 and b"/f/i/" in r.data
    inv = db.one("SELECT * FROM form_invitations")
    assert inv["opportunity_id"] == s and inv["template_key"] == "targeted" and "chairs" in inv["field_keys"]


# -- UAT-37 / UAT-38 -----------------------------------------------------------------------------
def test_uat37_uat38_ranked_lists_both_ways_with_confidentiality(db, login):
    s = seller(db, negotiable="yes", negotiable_min_price="17.3L")
    good = buyer(db, name="Good Fit")
    poor = buyer(db, phone="9100011099", name="Poor Fit", prefs={"gender_format__men": "must"})
    M.refresh(db)
    shtml = login("seller@t.test").get(f"/sellers/{s}?tab=matches").data.decode()
    assert shtml.index("Good Fit") < shtml.index("Poor Fit")  # ordered by rank; blocked last
    assert "fit" in shtml and "known" in shtml and "confidence" in shtml
    bhtml = login("buyer@t.test").get(f"/buyers/{good}?tab=matches").data.decode()
    assert "Test Salon" in bhtml and "confidence" in bhtml
    c = cand(db, good, s, refresh=False)
    for html in (bhtml, login("buyer@t.test").get(f"/matches/{c['id']}").data.decode()):
        assert "17.3" not in html and "17,30,000" not in html  # restricted lowest price never shown
    assert login("fin@t.test").get("/matches/").status_code == 403
    assert login("fin@t.test").get(f"/matches/{c['id']}").status_code == 403


def test_near_match_inside_volunteered_flexibility_needs_negotiation_not_auto_ready(db):
    s = seller(db, asking_price="22L", negotiable="yes", negotiable_min_price="19L")
    b = buyer(db, ceiling="20L")
    c = cand(db, b, s)
    assert c["gate"] == "review" and c["outcome"] == "near"
    assert "flexibility" in factor(db, c, "all_in")["note"] and "19" not in c["headline"]


# -- Productivity layer ---------------------------------------------------------------------
def test_biggest_unlock_ranks_the_question_that_holds_up_most_matches(db, login):
    s = seller(db, rent_deposit="", rent_deposit__state="unknown", deposit_treatment="buyer_pays_separately")
    buyer(db, name="B1")
    buyer(db, phone="9100011002", name="B2")
    M.refresh(db)
    top = M.unblock_suggestions(db, S.load(db, s))[0]
    assert "rent deposit" in top["text"].lower() and top["count"] == 2 and "rent_deposit" in top["fields"]
    html = login("seller@t.test").get(f"/sellers/{s}").data.decode()
    assert "Biggest unlock" in html and "holds up 2 matches" in html


def test_strong_new_match_notifies_both_owners_once(db):
    s, b = seller(db), buyer(db)
    M.refresh(db)
    for email in ("seller@t.test", "buyer@t.test"):
        assert db.scalar("SELECT COUNT(*) FROM notifications WHERE user_id = ? AND kind = 'match'",
                         (user(db, email)["id"],)) == 1
    set_seller(db, s, chairs="9")
    M.refresh(db)
    assert db.scalar("SELECT COUNT(*) FROM notifications WHERE kind = 'match'") == 2  # not again


def test_new_salon_and_exploring_buyers_are_not_matched(db, login):
    seller(db)
    nb = buyer(db, need_type="new_salon")
    M.refresh(db)
    assert db.scalar("SELECT COUNT(*) FROM match_candidates WHERE buyer_opp_id = ?", (nb,)) == 0
    assert b"not matched with salons for sale" in login("buyer@t.test").get(f"/buyers/{nb}?tab=matches").data


def test_pages_render_with_real_csrf_tokens_and_my_day_tiles(app, db, login):
    s, b = seller(db), buyer(db)
    c = cand(db, b, s)
    browser = app.test_client()
    browser.post("/login", data={"email": "lead@t.test", "password": PW})
    for url in ["/matches/", "/matches/?queue=all", f"/matches/{c['id']}", f"/sellers/{s}?tab=matches",
                f"/buyers/{b}?tab=matches", "/", "/team"]:
        html = browser.get(url).data.decode()
        assert "Traceback" not in html, url
        for f in re.findall(r'<form[^>]*method="post"[^>]*>(.*?)</form>', html, re.S):
            m = re.search(r'name="csrf_token" value="([^"]*)"', f)
            assert m and m.group(1), url
    assert "Matching" in browser.get("/").data.decode() and "Matches needing attention" in browser.get("/team").data.decode()
    assert browser.get("/planned/match").headers["Location"].endswith("/matches/")


def test_incomplete_all_in_total_never_shows_headroom(db, login):
    s = seller(db, asking_price="9L", rent_deposit="", rent_deposit__state="unknown",
               deposit_treatment="buyer_pays_separately")
    b = buyer(db, ceiling="60L")
    c = cand(db, b, s)
    assert c["all_in_complete"] == 0
    for url in (f"/matches/{c['id']}", "/matches/?queue=all"):
        html = login("admin@t.test").get(url).data.decode()
        assert "₹9 L+ all-in" in html and "total incomplete" in html
        assert "₹51 L headroom" not in html
