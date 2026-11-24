"""Seller management workflows mapped to PRD UAT scenarios."""
from conftest import SELLER_FORM, user

from teamos.domain import sellers as S


def _create(c, **over):
    return c.post("/sellers/new", {**SELLER_FORM, **over})


def test_uat01_manual_seller_lead_gets_owner_source_task_timeline(login, db):
    r = _create(login("seller@t.test"))
    assert r.status_code == 302
    opp = db.one("SELECT * FROM opportunities")
    assert opp["ref_code"].startswith("SL-") and opp["source"] == "manual_call"
    assert opp["primary_owner_id"] == user(db, "seller@t.test")["id"]
    task = db.one("SELECT * FROM tasks WHERE entity_id = ?", (opp["id"],))
    assert task["task_type"] == "first_contact" and task["assignee_id"] == opp["primary_owner_id"]
    assert opp["next_action"] == task["title"]
    assert db.scalar("SELECT COUNT(*) FROM activities WHERE entity_id = ?", (opp["id"],)) >= 1
    assert db.scalar("SELECT COUNT(*) FROM audit_events WHERE action = 'seller.create'") == 1
    consent = db.one("SELECT * FROM contact_consents")
    assert consent["granted"] == 1 and consent["text_version"]


def test_uat03_same_mobile_is_flagged_and_never_overwrites(login, db):
    c = login("seller@t.test")
    _create(c)
    r = _create(c, full_name="Different Name", phone="+91 98765-43210", salon_name="Other Salon")
    assert r.status_code == 200 and b"Possible duplicate" in r.data
    assert db.scalar("SELECT COUNT(*) FROM opportunities") == 1  # nothing written yet
    _create(c, full_name="Different Name", phone="9876543210", salon_name="Other Salon", dedupe_decision="new_flagged")
    assert db.scalar("SELECT COUNT(*) FROM contacts") == 2
    assert db.scalar("SELECT COUNT(*) FROM duplicate_reviews WHERE status = 'open'") == 1
    assert db.scalar("SELECT full_name FROM contacts WHERE id = 1") == "Test Owner"


def test_uat04_one_owner_two_outlets_two_listings_one_contact(login, db):
    c = login("seller@t.test")
    _create(c)
    cid = db.scalar("SELECT id FROM contacts")
    _create(c, salon_name="Second Outlet", locality="Kokapet", full_name="Changed Name", dedupe_decision=f"link:{cid}")
    assert db.scalar("SELECT COUNT(*) FROM contacts") == 1
    assert db.scalar("SELECT COUNT(*) FROM seller_listings") == 2
    assert db.scalar("SELECT COUNT(DISTINCT business_id) FROM seller_listings") == 2
    assert db.scalar("SELECT full_name FROM contacts") == "Test Owner"  # link never overwrites


def test_uat06_unknown_deposit_stays_unknown_not_zero(login, db):
    c = login("seller@t.test")
    _create(c)
    opp_id = db.scalar("SELECT id FROM opportunities")
    c.post(f"/sellers/{opp_id}/section/lease", {"rent_deposit": "", "rent_deposit__state": "unknown",
                                                 "monthly_rent": "0", "monthly_rent__state": ""})
    row = db.one("SELECT rent_deposit, monthly_rent FROM seller_listings")
    assert row["rent_deposit"] is None and row["monthly_rent"] == 0
    states = {r["field_key"]: r["answer_state"] for r in db.all("SELECT * FROM field_provenance")}
    assert states["rent_deposit"] == "unknown" and states["monthly_rent"] == "value"


def test_uat07_revenue_without_costs_warns_surplus_incomplete(login, db):
    c = login("seller@t.test")
    _create(c)
    opp_id = db.scalar("SELECT id FROM opportunities")
    c.post(f"/sellers/{opp_id}/financials", {"month": "2026-09", "revenue": "10L", "expenses": "", "source": "seller_call"})
    c.post(f"/sellers/{opp_id}/financials", {"month": "2026-10", "revenue": "11L", "expenses": "8L", "source": "seller_call"})
    fin = S.financial_summary(db, db.scalar("SELECT id FROM seller_listings"))
    assert fin["months_complete"] == 1 and fin["avg_reported_surplus"] == 300_000
    assert any("Costs missing" in w for w in fin["warnings"])
    assert any("seller-declared" in w for w in fin["warnings"])


def test_uat28_material_change_after_approval_requires_re_review(login, db):
    staff = login("seller@t.test")
    _create(staff)
    opp_id = db.scalar("SELECT id FROM opportunities")
    lid = db.scalar("SELECT id FROM seller_listings")
    db.execute("UPDATE listing_checklist SET status = 'reviewed' WHERE listing_id = ?", (lid,))
    login("admin@t.test").post(f"/sellers/{opp_id}/decision", {"decision": "approved"})
    v_approved = db.scalar("SELECT approved_version FROM seller_listings")
    staff.post(f"/sellers/{opp_id}/section/quick", {"salon_name": "Test Salon", "city": "Hyderabad",
                                                   "locality": "Kondapur", "asking_price": "24L"})
    row = db.one("SELECT * FROM seller_listings")
    assert row["approval_state"] == "re_review_required"
    assert row["approved_version"] == v_approved and row["data_version"] > v_approved
    decisions = [r["decision"] for r in db.all("SELECT decision FROM listing_approvals ORDER BY id")]
    assert decisions == ["approved", "re_review_flagged"]  # history retained
    assert db.scalar("SELECT COUNT(*) FROM field_change_log WHERE field_key = 'asking_price'") == 2


def test_non_material_change_keeps_approval(login, db):
    staff = login("seller@t.test")
    _create(staff)
    opp_id = db.scalar("SELECT id FROM opportunities")
    db.execute("UPDATE listing_checklist SET status = 'reviewed'")
    login("admin@t.test").post(f"/sellers/{opp_id}/decision", {"decision": "approved"})
    staff.post(f"/sellers/{opp_id}/section/profile", {"landmark": "Near metro"})
    assert db.scalar("SELECT approval_state FROM seller_listings") == "approved"


def test_approval_blocked_until_checklist_reviewed(login, db):
    _create(login("seller@t.test"))
    opp_id = db.scalar("SELECT id FROM opportunities")
    login("admin@t.test").post(f"/sellers/{opp_id}/decision", {"decision": "approved"})
    assert db.scalar("SELECT approval_state FROM seller_listings") == "draft"


def test_help_seeking_owner_cannot_be_basic_qualified(login, db):
    c = login("seller@t.test")
    _create(c, sale_purpose="seek_help")
    opp_id = db.scalar("SELECT id FROM opportunities")
    c.post(f"/sellers/{opp_id}/stage", {"to": "basic_qualified"})
    assert db.scalar("SELECT stage FROM opportunities") == "new"


def test_closed_lost_requires_reason_and_cancels_tasks(login, db):
    c = login("seller@t.test")
    _create(c)
    opp_id = db.scalar("SELECT id FROM opportunities")
    c.post(f"/sellers/{opp_id}/stage", {"to": "closed_lost"})
    assert db.scalar("SELECT stage FROM opportunities") == "new"
    c.post(f"/sellers/{opp_id}/stage", {"to": "closed_lost", "reason": "Sold privately"})
    opp = db.one("SELECT * FROM opportunities")
    assert opp["stage"] == "closed_lost" and opp["status"] == "closed" and opp["closed_reason"] == "Sold privately"
    assert db.scalar("SELECT COUNT(*) FROM tasks WHERE status = 'open'") == 0


def test_uat20_closed_lost_seller_re_enquires_new_opportunity_history_preserved(login, db):
    c = login("seller@t.test")
    _create(c)
    first = db.scalar("SELECT id FROM opportunities")
    c.post(f"/sellers/{first}/stage", {"to": "closed_lost", "reason": "Changed mind"})
    cid = db.scalar("SELECT id FROM contacts")
    _create(c, dedupe_decision=f"link:{cid}")
    rows = db.all("SELECT id, stage FROM opportunities ORDER BY id")
    assert [r["stage"] for r in rows] == ["closed_lost", "new"]
    assert db.scalar("SELECT COUNT(*) FROM stage_history WHERE opportunity_id = ?", (first,)) == 2


def test_call_logging_auto_progresses_and_enforces_next_action(login, db):
    c = login("seller@t.test")
    _create(c)
    opp_id = db.scalar("SELECT id FROM opportunities")
    tid = db.scalar("SELECT id FROM tasks")
    # completing the only task with no next action is refused
    c.post(f"/sellers/{opp_id}/call", {"outcome": "no_answer", "complete_task_id": tid})
    assert db.scalar("SELECT status FROM tasks WHERE id = ?", (tid,)) == "open"
    assert db.scalar("SELECT COUNT(*) FROM activities WHERE kind = 'call'") == 0  # rolled back
    c.post(f"/sellers/{opp_id}/call", {"outcome": "no_answer", "complete_task_id": tid,
                                       "next_title": "Try again", "next_due": "2030-01-01T10:00"})
    assert db.scalar("SELECT stage FROM opportunities") == "contact_attempted"
    c.post(f"/sellers/{opp_id}/call", {"outcome": "connected", "note": "Spoke to owner"})
    assert db.scalar("SELECT stage FROM opportunities") == "connected"


def test_do_not_contact_outcome_suppresses_contact(login, db):
    c = login("seller@t.test")
    _create(c)
    opp_id = db.scalar("SELECT id FROM opportunities")
    c.post(f"/sellers/{opp_id}/call", {"outcome": "do_not_contact", "note": "Asked not to call"})
    assert db.scalar("SELECT do_not_contact FROM contacts") == 1
    assert db.scalar("SELECT stage FROM opportunities") == "do_not_contact"
    latest = db.one("SELECT * FROM contact_consents ORDER BY id DESC LIMIT 1")
    assert latest["granted"] == 0 and latest["purpose"] == "service_contact"


def test_uat21_reassignment_keeps_call_attribution(login, db):
    staff = login("seller@t.test")
    _create(staff)
    opp_id = db.scalar("SELECT id FROM opportunities")
    staff.post(f"/sellers/{opp_id}/call", {"outcome": "connected", "next_title": "Follow up",
                                           "next_due": "2030-01-01T10:00"})
    new_owner = user(db, "buyer@t.test")["id"]
    staff.post(f"/sellers/{opp_id}/assign", {"owner_id": new_owner, "reason": "Workload"})
    assert db.scalar("SELECT primary_owner_id FROM opportunities") == new_owner
    assert db.scalar("SELECT actor_id FROM activities WHERE kind = 'call'") == user(db, "seller@t.test")["id"]


def test_uat09_style_reviewer_fields_not_settable_from_intake(login, db):
    """Evidence status can't be smuggled through the intake form (analogue of UAT-09 for sellers)."""
    c = login("seller@t.test")
    _create(c)
    opp_id = db.scalar("SELECT id FROM opportunities")
    c.post(f"/sellers/{opp_id}/section/quick", {"salon_name": "Test Salon", "city": "Hyderabad", "locality": "Kondapur",
                                               "asking_price": "21L", "asking_price__evidence": "verified",
                                               "approval_state": "approved"})
    assert db.scalar("SELECT evidence_status FROM field_provenance WHERE field_key = 'asking_price'") == "seller_declared"
    assert db.scalar("SELECT approval_state FROM seller_listings") == "draft"


def test_readiness_score_is_explainable_and_missing_is_not_positive(login, db):
    _create(login("seller@t.test"), sale_purpose="unsure", sale_timeline="no_rush", ownership_role="manager")
    opp = S.load(db, db.scalar("SELECT id FROM opportunities"))
    listing = S.load_listing(db, opp["listing_id"])
    r = S.readiness_score(db, opp, listing, S.provenance(db, listing["id"]))
    assert r["score"] < 20 and len(r["factors"]) == 6
    assert sum(f["max"] for f in r["factors"]) == 100
    assert all(f["reason"] for f in r["factors"])


def test_search_finds_by_phone_and_locality(login, db):
    c = login("seller@t.test")
    _create(c)
    assert b"Test Salon" in c.get("/search?q=43210").data
    assert b"Test Salon" in c.get("/search?q=kondapur").data
