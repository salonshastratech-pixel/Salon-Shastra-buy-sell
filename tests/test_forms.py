"""Milestone 4 — forms & data requests (PRD §5.6, §10.2, §10.4; UAT-41 mock, UAT-42; FR-009/010/011/013/043/047)."""
import io
import json
import re
from datetime import timedelta

from conftest import SELLER_FORM, user

from teamos.domain import forms as F
from teamos.timeutil import now_utc, to_iso

PDF = b"%PDF-1.4\n%synthetic test document\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"
BUYER = {"full_name": "Arjun Investor", "phone": "91234 56789", "city": "Hyderabad", "locality": "Kondapur",
         "source": "referral", "consent_contact": "1", "need_type": "acquire_operating", "decision_maker": "yes",
         "all_in_max": "35L", "purchase_window": "90d"}


def new_seller(login, db, **over):
    c = login("seller@t.test")
    assert c.post("/sellers/new", {**SELLER_FORM, **over}).status_code == 302
    return c, db.scalar("SELECT MAX(id) FROM opportunities WHERE kind = 'seller'")


def new_buyer(login, db, **over):
    c = login("buyer@t.test")
    assert c.post("/buyers/new", {**BUYER, **over}).status_code == 302
    return c, db.scalar("SELECT MAX(id) FROM opportunities WHERE kind = 'buyer'")


def send(c, opp_id, template_key, channel="copy_link", **extra):
    r = c.post(f"/forms/send/{opp_id}", {"template_key": template_key, "channel": channel, **extra})
    assert r.status_code == 200, r.data[:400]
    html = r.data.decode()
    token = re.search(r"/f/i/([A-Za-z0-9_\-]+)", html).group(1)
    return token, html


def public(app):
    return app.test_client()


# -- UAT-41 (mock) ---------------------------------------------------------------------
def test_uat41_form_link_via_mock_whatsapp_records_source_invitation_and_provider_trace(app, db, login):
    c, opp_id = new_seller(login, db)
    token, html = send(c, opp_id, "seller_deep", "whatsapp_mock")
    inv = db.one("SELECT * FROM form_invitations")
    comm = db.one("SELECT m.*, v.contact_id FROM messages m JOIN conversations v ON v.id = m.conversation_id "
                  "WHERE m.direction = 'out'")
    contact_id = db.scalar("SELECT contact_id FROM opportunities WHERE id = ?", (opp_id,))
    # exact source record and invitation
    assert inv["opportunity_id"] == opp_id and inv["contact_id"] == contact_id and inv["template_version"] == "seller-deep v1"
    assert comm["invitation_id"] == inv["id"] and comm["opportunity_id"] == opp_id and comm["contact_id"] == contact_id
    # provider message trace — honestly a mock, and never claimed delivered
    assert comm["provider"] == "mock-whatsapp" and comm["provider_message_id"].startswith("mock-")
    assert comm["status"] == "provider_accepted" and comm["sent_by"] == user(db, "seller@t.test")["id"]
    assert "TEST-ONLY mock" in html and comm["provider_message_id"] in html
    # the secret is shown once and never stored in plain text — not in the DB, not in the message log
    assert token not in json.dumps(db.all("SELECT * FROM form_invitations")) and token not in comm["body"]
    assert inv["token_hash"] == F._hash(token) and "[personal link]" in comm["body"]
    detail = json.loads(db.scalar("SELECT detail FROM audit_events WHERE action = 'form.invitation_created'"))
    assert detail["invitation_id"] == inv["id"] and detail["provider_message_id"] == comm["provider_message_id"]
    assert comm["provider_message_id"].encode() in c.get(f"/sellers/{opp_id}").data  # visible on the record


# -- UAT-42 ----------------------------------------------------------------------------
def test_uat42_customer_submits_new_budget_review_task_and_no_auto_verified_funding(app, db, login):
    c, opp_id = new_buyer(login, db)
    token, _ = send(c, opp_id, "buyer_deep")
    p = public(app)
    r = p.post(f"/f/i/{token}", data={"all_in_max": "50L", "cash_ready": "now", "funding_status": "verified",
                                       "funding_verified": "yes", "qualified_by_salon_shastra": "yes",
                                       "action": "submit"})
    assert r.status_code == 200 and b"Thank you" in r.data
    m = db.one("SELECT * FROM buyer_mandates WHERE opportunity_id = ?", (opp_id,))
    assert m["all_in_max"] == 35_00_000  # nothing changes until a person reviews
    assert m["funding_status"] == "not_reviewed"
    sub = db.one("SELECT * FROM form_submissions WHERE status = 'submitted'")
    task = db.one("SELECT * FROM tasks WHERE id = ?", (sub["review_task_id"],))
    assert task["task_type"] == "review" and task["status"] == "open" and task["entity_id"] == opp_id
    assert json.loads(sub["ignored_fields"]) == ["funding_status", "funding_verified", "qualified_by_salon_shastra"]
    assert db.scalar("SELECT COUNT(*) FROM audit_events WHERE action = 'form.privileged_field_ignored' "
                     "AND outcome = 'denied'") == 1
    # reviewer sees the diff and applies the budget only
    lead = login("lead@t.test")
    lead.post(f"/buyers/{opp_id}/confirm-criteria")
    page = lead.get(f"/forms/submissions/{sub['id']}").data.decode()
    assert "₹50 L" in page and "₹35 L" in page and "reviewer-only fields" in page
    lead.post(f"/forms/submissions/{sub['id']}", {"accept": ["all_in_max"], "action": "apply"})
    m = db.one("SELECT * FROM buyer_mandates WHERE opportunity_id = ?", (opp_id,))
    assert m["all_in_max"] == 50_00_000 and m["cash_ready"] is None  # unticked answer not applied
    assert m["funding_status"] == "not_reviewed"
    prov = db.one("SELECT * FROM field_provenance WHERE entity_type = 'buyer_mandate' AND field_key = 'all_in_max'")
    assert prov["evidence_status"] == "buyer_declared" and prov["source"] == f"form:buyer-deep v1#{sub['id']}"
    assert m["criteria_reviewed_version"] != m["data_version"]  # confirmed criteria now marked as changed
    sub = db.one("SELECT * FROM form_submissions WHERE id = ?", (sub["id"],))
    assert sub["review_outcome"] == "partially_applied" and json.loads(sub["applied_fields"]) == ["all_in_max"]
    assert db.scalar("SELECT status FROM tasks WHERE id = ?", (sub["review_task_id"],)) == "done"
    assert db.scalar("SELECT status FROM form_invitations") == "accepted"


# -- Link lifecycle --------------------------------------------------------------------
def test_link_lifecycle_open_draft_resume_submit_close(app, db, login):
    c, opp_id = new_seller(login, db)
    token, _ = send(c, opp_id, "seller_deep")
    p = public(app)
    assert db.scalar("SELECT status FROM form_invitations") == "sent"
    page = p.get(f"/f/i/{token}")
    assert page.status_code == 200 and b"Hello Test" in page.data
    assert page.headers["Cache-Control"] == "no-store" and "noindex" in page.headers["X-Robots-Tag"]
    assert db.scalar("SELECT status FROM form_invitations") == "opened"
    assert b'value="2000000"' in page.data  # prefilled from the record (quick-screen asking price)
    p.post(f"/f/i/{token}", data={"chairs": "7", "monthly_rent__state": "unknown", "action": "save"})
    assert db.scalar("SELECT status FROM form_invitations") == "started"
    resumed = p.get(f"/f/i/{token}").data.decode()
    assert 'value="7"' in resumed  # draft restored
    assert db.scalar("SELECT COUNT(*) FROM form_submissions") == 1  # one draft, updated in place
    r = p.post(f"/f/i/{token}", data={"chairs": "8", "action": "submit"})
    assert b"Thank you" in r.data
    assert db.scalar("SELECT status FROM form_invitations") == "submitted"
    assert b"reviewing them" in p.get(f"/f/i/{token}").data
    assert b"no longer active" in p.post(f"/f/i/{token}", data={"chairs": "9", "action": "submit"}).data \
        or db.scalar("SELECT COUNT(*) FROM form_submissions WHERE status = 'submitted'") == 1


def test_expiry_revocation_and_one_live_deep_link(app, db, login):
    c, opp_id = new_seller(login, db)
    t1, _ = send(c, opp_id, "seller_deep")
    t2, _ = send(c, opp_id, "seller_deep")
    p = public(app)
    assert p.get(f"/f/i/{t1}").status_code == 410  # superseded link no longer works
    assert p.get(f"/f/i/{t2}").status_code == 200
    db.execute("UPDATE form_invitations SET expires_at = ? WHERE status = 'opened'",
               (to_iso(now_utc() - timedelta(minutes=1)),))
    r = p.get(f"/f/i/{t2}")
    assert r.status_code == 410 and b"expired" in r.data
    t3, _ = send(c, opp_id, "targeted", fields=["chairs"])
    inv = db.one("SELECT id FROM form_invitations WHERE template_key = 'targeted'")
    c.post(f"/forms/invitations/{inv['id']}/revoke")
    assert p.get(f"/f/i/{t3}").status_code == 410
    assert p.get("/f/i/not-a-real-token").status_code == 404


def test_cannot_send_without_consent_or_to_do_not_contact(db, login):
    c, opp_id = new_seller(login, db, consent_contact="")
    r = c.post(f"/forms/send/{opp_id}", {"template_key": "seller_deep", "channel": "whatsapp_mock"},
               follow_redirects=True)
    assert b"no recorded consent" in r.data
    assert db.scalar("SELECT COUNT(*) FROM form_invitations") == 0
    assert db.scalar("SELECT COUNT(*) FROM messages") == 0
    assert db.scalar("SELECT COUNT(*) FROM audit_events WHERE action = 'form.send_blocked'") == 1


# -- Targeted requests -----------------------------------------------------------------
def test_targeted_request_asks_only_for_chosen_fields_with_purpose(app, db, login):
    c, opp_id = new_seller(login, db)
    r = c.post(f"/forms/send/{opp_id}", {"template_key": "targeted", "channel": "copy_link",
                                         "fields": ["funding_status"]}, follow_redirects=True)
    assert b"cannot be requested" in r.data
    token, _ = send(c, opp_id, "targeted", fields=["rent_deposit", "lease_expiry"], docs=["lease_agreement"],
                    purpose="Two quick lease questions from buyers")
    html = public(app).get(f"/f/i/{token}").data.decode()
    assert 'name="rent_deposit"' in html and 'name="lease_expiry"' in html and 'name="doc__lease_agreement"' in html
    assert 'name="chairs"' not in html and 'name="monthly_rent"' not in html
    assert "Two quick lease questions from buyers" in html and "Why we ask" in html


# -- Public quick forms ----------------------------------------------------------------
def _token(page):
    return re.search(r'name="form_token" value="([^"]+)"', page.data.decode()).group(1)


def test_public_quick_forms_create_new_records_for_review_never_auto_linked(app, db, login):
    new_seller(login, db)  # existing contact with the same mobile
    p = public(app)
    tok = _token(p.get("/f/seller"))
    r = p.post("/f/seller", data={"form_token": tok, "full_name": "Online Owner", "phone": SELLER_FORM["phone"],
                                  "salon_name": "Web Salon", "city": "Hyderabad", "locality": "Ameerpet",
                                  "asking_price": "15L", "consent_contact": "1"})
    assert r.status_code == 302 and r.headers["Location"].endswith("/f/seller/thanks")
    opp = db.one("SELECT * FROM opportunities ORDER BY id DESC LIMIT 1")
    assert opp["source"] == "landing_page" and opp["stage"] == "new"
    first = db.scalar("SELECT contact_id FROM opportunities ORDER BY id LIMIT 1")
    assert opp["contact_id"] != first  # never silently linked
    assert db.scalar("SELECT COUNT(*) FROM duplicate_reviews WHERE status = 'open'") == 1
    assert db.scalar("SELECT asking_price FROM seller_listings WHERE opportunity_id = ?", (opp["id"],)) == 15_00_000
    assert db.scalar("SELECT COUNT(*) FROM tasks WHERE entity_id = ? AND task_type = 'review'", (opp["id"],)) == 1
    consent = db.one("SELECT * FROM contact_consents WHERE contact_id = ?", (opp["contact_id"],))
    assert consent["granted"] == 1 and consent["text_version"] == "public-form-consent-v0.1"
    # buyer quick form: privileged field ignored and audited
    tok = _token(p.get("/f/buyer"))
    p.post("/f/buyer", data={"form_token": tok, "full_name": "Web Buyer", "phone": "9123400001", "city": "Hyderabad",
                             "need_type": "acquire_operating", "all_in_max": "30L", "consent_contact": "1",
                             "funding_status": "verified"})
    b = db.one("SELECT m.* FROM buyer_mandates m ORDER BY id DESC LIMIT 1")
    assert b["all_in_max"] == 30_00_000 and b["funding_status"] == "not_reviewed"
    assert db.scalar("SELECT COUNT(*) FROM audit_events WHERE action = 'buyer.privileged_field_ignored'") == 1
    # both appear in the inbox; marking reviewed closes the review task
    admin = login("admin@t.test")
    inbox = admin.get("/forms/").data
    assert b"Online Owner" in inbox and b"Web Buyer" in inbox
    sid = db.scalar("SELECT id FROM form_submissions WHERE template_key = 'seller_quick'")
    admin.post(f"/forms/submissions/{sid}", {"action": "recorded"})
    assert db.scalar("SELECT review_outcome FROM form_submissions WHERE id = ?", (sid,)) == "recorded"


def test_public_form_abuse_controls(app, db):
    p = public(app)
    base = {"full_name": "X", "phone": "9123400002", "salon_name": "S", "city": "Hyderabad", "consent_contact": "1"}
    assert p.post("/f/seller", data={**base, "form_token": "forged"}).status_code == 400
    assert p.post("/f/seller", data={**base, "form_token": _token(p.get("/f/buyer"))}).status_code == 400  # wrong form
    r = p.post("/f/seller", data={**base, "form_token": _token(p.get("/f/seller")), "website": "spam.example"})
    assert r.status_code == 302 and db.scalar("SELECT COUNT(*) FROM opportunities") == 0  # honeypot: silently dropped
    r = p.post("/f/seller", data={**{k: v for k, v in base.items() if k != "consent_contact"},
                                  "form_token": _token(p.get("/f/seller"))})
    assert r.status_code == 200 and b"Please confirm we may contact you" in r.data
    for i in range(5):
        tok = _token(p.get("/f/seller"))
        assert p.post("/f/seller", data={**base, "phone": f"912340001{i}", "form_token": tok}).status_code == 302
    tok = _token(p.get("/f/seller"))
    assert p.post("/f/seller", data={**base, "phone": "9123400099", "form_token": tok}).status_code == 429
    assert db.scalar("SELECT COUNT(*) FROM opportunities") == 5


# -- Secure uploads (FR-013, UAT-16) ---------------------------------------------------
def test_uploads_are_encrypted_and_only_authorised_people_can_download(app, db, login):
    c, opp_id = new_seller(login, db)  # seller@ is the primary owner
    token, _ = send(c, opp_id, "targeted", docs=["lease_agreement"])
    p = public(app)
    r = p.post(f"/f/i/{token}", data={"doc__lease_agreement": (io.BytesIO(b"MZ\x90\x00not a pdf"), "evil.exe"),
                                      "action": "submit"}, content_type="multipart/form-data")
    assert b"Only PDF, PNG or JPG" in r.data and db.scalar("SELECT COUNT(*) FROM documents") == 0
    r = p.post(f"/f/i/{token}", data={"doc__lease_agreement": (io.BytesIO(PDF), "lease.pdf"), "action": "submit"},
               content_type="multipart/form-data")
    assert b"Thank you" in r.data
    doc = db.one("SELECT * FROM documents")
    raw = (app.config["UPLOAD_DIR"] + "/" + doc["stored_name"])
    with open(raw, "rb") as fh:
        assert PDF not in fh.read()  # encrypted at rest
    assert doc["content_type"] == "application/pdf" and doc["encrypted"] == 1 and doc["opportunity_id"] == opp_id
    assert c.get(f"/forms/documents/{doc['id']}").data == PDF  # primary owner
    assert login("admin@t.test").get(f"/forms/documents/{doc['id']}").data == PDF
    other = login("buyer@t.test")  # team member, not the owner
    assert other.get(f"/forms/documents/{doc['id']}").status_code == 403
    assert login("fin@t.test").get(f"/forms/documents/{doc['id']}").status_code == 403
    assert db.scalar("SELECT COUNT(*) FROM audit_events WHERE action = 'document.open' AND outcome = 'denied'") == 2
    assert b"Restricted" in other.get(f"/sellers/{opp_id}").data
    assert p.get(f"/forms/documents/{doc['id']}").status_code == 302  # anonymous → login


def test_buyer_funding_proof_visible_to_the_team_not_finance(app, db, login):
    """D-22 (founder, 23 Nov 2026): the team sees buyers' proof of funds by default."""
    c, opp_id = new_buyer(login, db)
    token, _ = send(c, opp_id, "targeted", docs=["funding_proof"])
    public(app).post(f"/f/i/{token}", data={"doc__funding_proof": (io.BytesIO(PDF), "bank.pdf"), "action": "submit"},
                     content_type="multipart/form-data")
    doc_id = db.scalar("SELECT id FROM documents")
    assert c.get(f"/forms/documents/{doc_id}").data == PDF  # buyer-team member
    assert login("seller@t.test").get(f"/forms/documents/{doc_id}").data == PDF  # any team member
    assert login("lead@t.test").get(f"/forms/documents/{doc_id}").data == PDF
    assert login("fin@t.test").get(f"/forms/documents/{doc_id}").status_code == 403  # no CRM access
    assert db.scalar("SELECT funding_status FROM buyer_mandates") == "not_reviewed"  # a document is not verification


# -- Review rules ----------------------------------------------------------------------
def test_material_change_from_form_sends_approved_listing_back_to_review(app, db, login):
    c, opp_id = new_seller(login, db)
    db.execute("UPDATE seller_listings SET approval_state = 'approved' WHERE opportunity_id = ?", (opp_id,))
    token, _ = send(c, opp_id, "targeted", fields=["asking_price", "landmark"])
    public(app).post(f"/f/i/{token}", data={"asking_price": "18L", "landmark": "Near metro", "action": "submit"})
    sid = db.scalar("SELECT id FROM form_submissions")
    r = c.post(f"/forms/submissions/{sid}", {"action": "reject"}, follow_redirects=True)
    assert b"Add a short note" in r.data  # rejecting needs a reason
    c.post(f"/forms/submissions/{sid}", {"accept": ["asking_price", "landmark"], "action": "apply"})
    listing = db.one("SELECT * FROM seller_listings WHERE opportunity_id = ?", (opp_id,))
    assert listing["asking_price"] == 18_00_000 and listing["approval_state"] == "re_review_required"
    assert db.scalar("SELECT review_outcome FROM form_submissions") == "applied"


def test_chase_task_once_after_48h_then_expiry(app, db, login):
    c, opp_id = new_seller(login, db)
    send(c, opp_id, "seller_deep")
    assert F.chase_and_expire(db) == "chased 0, expired 0"
    db.execute("UPDATE form_invitations SET created_at = ?", (to_iso(now_utc() - timedelta(hours=49)),))
    assert F.chase_and_expire(db) == "chased 1, expired 0"
    assert F.chase_and_expire(db) == "chased 0, expired 0"  # once only
    assert db.scalar("SELECT COUNT(*) FROM tasks WHERE title LIKE 'Form not completed%'") == 1
    db.execute("UPDATE form_invitations SET expires_at = ?", (to_iso(now_utc() - timedelta(hours=1)),))
    assert F.chase_and_expire(db) == "chased 0, expired 1"


def test_staff_pages_render_with_real_csrf_tokens(app, db, login):
    c, opp_id = new_seller(login, db)
    token, _ = send(c, opp_id, "seller_deep")
    public(app).post(f"/f/i/{token}", data={"chairs": "6", "action": "submit"})
    sid = db.scalar("SELECT id FROM form_submissions")
    browser = app.test_client()
    from conftest import PW
    browser.post("/login", data={"email": "admin@t.test", "password": PW})
    for url in ("/forms/", f"/forms/submissions/{sid}", f"/sellers/{opp_id}"):
        html = browser.get(url).data.decode()
        for f in re.findall(r'<form[^>]*method="post"[^>]*>(.*?)</form>', html, re.S):
            m = re.search(r'name="csrf_token" value="([^"]*)"', f)
            assert m and m.group(1), url
    assert browser.get("/planned/forms").headers["Location"].endswith("/forms/")


def test_customer_forms_never_show_internal_staff_guidance(app, db, login):
    from teamos.domain import buyer_fields, seller_fields
    p = public(app)
    c, opp_id = new_seller(login, db)
    token, _ = send(c, opp_id, "seller_deep")
    pages = [p.get("/f/seller").data.decode(), p.get("/f/buyer").data.decode(), p.get(f"/f/i/{token}").data.decode()]
    staff_help = [f.help for f in seller_fields.FIELDS + buyer_fields.FIELDS if f.help]
    for html in pages:
        for h in staff_help:
            assert h.replace("'", "&#39;") not in html and h not in html, h
        assert "respondent" not in html.lower()
