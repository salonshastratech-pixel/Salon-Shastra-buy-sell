"""Authentication, sessions, CSRF and server-side authorisation (PRD §3, §14.1)."""
from conftest import PW, SELLER_FORM, user

from teamos.domain import sellers as S


def test_login_wrong_password_rejected_and_audited(app, db):
    c = app.test_client()
    r = c.post("/login", data={"email": "seller@t.test", "password": "nope"})
    assert r.status_code == 401
    assert db.scalar("SELECT COUNT(*) FROM audit_events WHERE action = 'auth.login' AND outcome = 'denied'") == 1


def test_lockout_after_repeated_failures(app):
    c = app.test_client()
    for _ in range(5):
        c.post("/login", data={"email": "seller@t.test", "password": "bad"})
    r = c.post("/login", data={"email": "seller@t.test", "password": PW})
    assert r.status_code == 401 and b"Too many failed attempts" in r.data


def test_unauthenticated_redirects_to_login(app):
    r = app.test_client().get("/sellers/")
    assert r.status_code == 302 and "/login" in r.headers["Location"]


def test_post_without_csrf_rejected(login):
    c = login("seller@t.test")
    r = c.post("/sellers/new", SELLER_FORM, csrf=False)
    assert r.status_code == 400


def test_uat27_disabled_user_loses_session_immediately(login, db):
    staff = login("seller@t.test")
    assert staff.get("/").status_code == 200
    admin = login("admin@t.test")
    uid = user(db, "seller@t.test")["id"]
    assert admin.post(f"/admin/users/{uid}/active").status_code == 302
    r = staff.get("/sellers/")
    assert r.status_code == 302 and "/login" in r.headers["Location"]


def test_uat33_staff_cannot_open_admin_finance_and_attempt_is_logged(login, db):
    r = login("seller@t.test").get("/admin/finance")
    assert r.status_code == 403
    row = db.one("SELECT * FROM audit_events WHERE action = 'access.denied' ORDER BY id DESC LIMIT 1")
    assert row["outcome"] == "denied" and "finance.view" in row["detail"]
    assert login("fin@t.test").get("/admin/finance").status_code == 200
    assert login("admin@t.test").get("/admin/finance").status_code == 200


def test_finance_role_has_no_operational_crm(login):
    assert login("fin@t.test").get("/sellers/").status_code == 403


def test_team_members_share_operational_visibility(login, db):
    """UAT-31/32 (seller part): a buyer-team member can open a seller record owned by someone else."""
    login("seller@t.test").post("/sellers/new", SELLER_FORM)
    opp_id = db.scalar("SELECT id FROM opportunities")
    assert login("buyer@t.test").get(f"/sellers/{opp_id}").status_code == 200


def test_restricted_fields_masked_for_non_owner(login, db, app):
    owner = login("seller@t.test")
    owner.post("/sellers/new", SELLER_FORM)
    opp_id = db.scalar("SELECT id FROM opportunities")
    owner.post(f"/sellers/{opp_id}/section/reason", {"sale_reason": "personal",
                                                     "sale_reason_note": "PRIVATE-DEBT-CONTEXT"})
    owner.post(f"/sellers/{opp_id}/section/commercial", {"negotiable": "yes", "negotiable_min_price": "17L"})
    assert b"PRIVATE-DEBT-CONTEXT" in owner.get(f"/sellers/{opp_id}?tab=intake").data
    other = login("buyer@t.test")
    page = other.get(f"/sellers/{opp_id}?tab=intake").data
    assert b"PRIVATE-DEBT-CONTEXT" not in page
    assert b"negotiable_min_price" not in page
    hist = other.get(f"/sellers/{opp_id}?tab=history").data
    assert b"PRIVATE-DEBT-CONTEXT" not in hist and b"1700000" not in hist
    # and cannot overwrite it by posting the field
    other.post(f"/sellers/{opp_id}/section/reason", {"sale_reason": "personal", "sale_reason_note": "overwritten"})
    assert db.scalar("SELECT sale_reason_note FROM seller_listings") == "PRIVATE-DEBT-CONTEXT"
    assert login("admin@t.test").get(f"/sellers/{opp_id}?tab=intake").data.count(b"PRIVATE-DEBT-CONTEXT") == 1


def test_scoped_grant_unlocks_restricted_fields_for_one_record(login, db):
    login("seller@t.test").post("/sellers/new", SELLER_FORM)
    opp_id = db.scalar("SELECT id FROM opportunities")
    login("seller@t.test").post(f"/sellers/{opp_id}/section/reason", {"sale_reason_note": "SECRET"})
    buyer_id = user(db, "buyer@t.test")["id"]
    login("admin@t.test").post("/admin/grants", {"user_id": buyer_id, "capability": "seller.view_restricted",
                                                 "scope_id": opp_id, "reason": "Deal team for this listing"})
    assert b"SECRET" in login("buyer@t.test").get(f"/sellers/{opp_id}?tab=intake").data


def test_uat08_staff_cannot_approve_listing_admin_can(login, db):
    staff = login("seller@t.test")
    staff.post("/sellers/new", SELLER_FORM)
    opp_id = db.scalar("SELECT id FROM opportunities")
    listing_id = db.scalar("SELECT id FROM seller_listings")
    for key, _ in S.LISTING_CHECKLIST:
        db.execute("UPDATE listing_checklist SET status = 'reviewed' WHERE listing_id = ? AND item_key = ?",
                   (listing_id, key))
    r = staff.post(f"/sellers/{opp_id}/decision", {"decision": "approved"})
    assert r.status_code == 403
    assert db.scalar("SELECT approval_state FROM seller_listings") == "draft"
    assert db.scalar("SELECT COUNT(*) FROM audit_events WHERE action = 'listing.decide' AND outcome = 'denied'") == 1
    login("admin@t.test").post(f"/sellers/{opp_id}/decision", {"decision": "approved"})
    assert db.scalar("SELECT approval_state FROM seller_listings") == "approved"
    assert db.scalar("SELECT stage FROM opportunities") == "listing_approved"


def test_evidence_review_requires_reviewer(login, db):
    staff = login("seller@t.test")
    staff.post("/sellers/new", SELLER_FORM)
    opp_id = db.scalar("SELECT id FROM opportunities")
    r = staff.post(f"/sellers/{opp_id}/evidence", {"field_key": "asking_price", "status": "verified"})
    assert r.status_code == 403
    login("lead@t.test").post(f"/sellers/{opp_id}/evidence", {"field_key": "asking_price", "status": "verified"})
    assert db.scalar("SELECT evidence_status FROM field_provenance WHERE field_key = 'asking_price'") == "verified"


def test_finance_team_task_list_hides_seller_linked_tasks(login):
    from conftest import SELLER_FORM
    admin = login("admin@t.test")
    assert admin.post("/sellers/new", SELLER_FORM).status_code == 302
    assert b"Test Salon" in admin.get("/tasks/?view=all").data
    for view in ("all", "mine", "created", "done"):
        body = login("fin@t.test").get(f"/tasks/?view={view}").data
        assert b"Test Salon" not in body and b"Test Owner" not in body, view
