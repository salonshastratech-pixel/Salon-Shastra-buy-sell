"""Milestone 7.3 — only Meta-approved templates (with matching wording) are used live; Meta's format rules."""
import pytest

from conftest import user
from test_matching import buyer

from teamos import messaging
from teamos import meta_templates as MT
from teamos.domain import inbox as I


def test_every_built_in_template_meets_metas_format_rules():
    for key, (_n, cat, body) in messaging.TEMPLATES.items():
        assert MT.lint(key, body, MT.META_CATEGORY[key]) == [], key
        assert MT.META_CATEGORY[key] in ("UTILITY", "MARKETING", "REPLY")
        assert (cat == "marketing") == (MT.META_CATEGORY[key] == "MARKETING"), key
    text, order = MT.to_meta(messaging.TEMPLATES["callback_confirm"][2])
    assert "{{1}}" in text and "{{3}}" in text and order == ["name", "sender", "when"]
    assert MT.meta_name("Call-Thanks Seller") == "call_thanks_seller"


def test_lint_catches_what_meta_rejects_or_recategorises():
    assert any("Starts with" in i for i in MT.lint("x", "{name} hello there friend. Reply STOP", "UTILITY"))
    assert any("Ends with" in i for i in MT.lint("x", "Hello friend, reply STOP to opt out {name}", "UTILITY"))
    assert any("side by side" in i for i in MT.lint("x", "Hello {name} {sender} from us. Reply STOP to opt out", "UTILITY"))
    assert any("MARKETING" in i for i in MT.lint("x", "Hello {name}, special discount on salons today. Reply STOP to "
                                                     "opt out", "UTILITY"))
    assert any("opt-out" in i for i in MT.lint("x", "Hello {name}, see you soon at the salon today.", "UTILITY"))
    assert MT.clean_param("Line one\nline two\t  end      x") == "Line one line two   end   x"


def _meta(name, body, status="APPROVED", category="UTILITY"):
    return {"name": name, "status": status, "category": category, "language": "en",
            "components": [{"type": "BODY", "text": body}]}


def test_cross_check_marks_only_approved_and_identical_templates_live_ready(app, db):
    with app.test_request_context():
        I.ensure_templates(db)
        ok_text, _ = MT.to_meta(messaging.TEMPLATES["callback_request"][2])
        c = MT.cross_check(db, None, [
            _meta("callback_request", ok_text),
            _meta("enquiry_ack", "Hello {{1}}, different wording. Reply STOP to opt out"),
            _meta("visit_confirm", MT.to_meta(messaging.TEMPLATES["visit_confirm"][2])[0], status="PENDING"),
            _meta("market_update", MT.to_meta(messaging.TEMPLATES["market_update"][2])[0], status="REJECTED",
                  category="MARKETING")])
    assert c["approved"] == 1 and c["mismatch"] == 1 and c["pending"] == 1 and c["rejected"] == 1
    assert c["missing"] >= 10
    row = lambda k: db.one("SELECT * FROM message_templates WHERE key = ?", (k,))  # noqa: E731
    assert row("callback_request")["status"] == "provider_approved" and row("callback_request")["meta_status"] == "APPROVED"
    assert "differs" in row("enquiry_ack")["meta_issue"] and row("enquiry_ack")["status"] == "approved_local"
    assert row("next_steps")["meta_status"] == "NOT_FOUND"
    assert row("optout_confirm")["meta_status"] is None  # a reply, not a template
    live = [t["key"] for t in I.usable_templates(db, live=True)]
    assert live == ["callback_request"]
    assert len(I.usable_templates(db, live=False)) > 10
    # live sending refuses anything not confirmed by Meta
    assert I.template_problems(row("callback_request"), {"name": "A", "sender": "B"}, live=True) == []
    assert I.template_problems(row("enquiry_ack"), {"name": "A", "sender": "B"}, live=True)


def test_changing_the_wording_after_approval_needs_metas_approval_again(app, db, login):
    with app.test_request_context():
        I.ensure_templates(db)
        MT.cross_check(db, None, [_meta("callback_request", MT.to_meta(messaging.TEMPLATES["callback_request"][2])[0])])
    login("admin@t.test").post("/admin/templates", {"key": "callback_request", "name": "Call-back request",
                                                    "category": "service", "status": "approved_local",
                                                    "provider_name": "callback_request",
                                                    "body": "Hello {name}, this is {sender}. Please call us back when "
                                                            "free. Reply STOP to opt out."})
    t = db.one("SELECT * FROM message_templates WHERE key = 'callback_request'")
    assert t["meta_issue"] and "check again" in t["meta_issue"] and not I.usable_templates(db, live=True)
    # an admin cannot simply mark a template "approved by Meta"
    login("admin@t.test").post("/admin/templates", {"key": "callback_request", "name": "x", "category": "service",
                                                    "status": "provider_approved", "body": "Hello {name} reply STOP ok"})
    assert db.scalar("SELECT status FROM message_templates WHERE key = 'callback_request'") != "provider_approved"


def test_check_with_meta_button(app, db, login, monkeypatch):
    admin = login("admin@t.test")
    r = admin.post("/admin/templates", {"action": "meta_check"}, follow_redirects=True)
    assert b"Fill in the WhatsApp Business account ID" in r.data
    monkeypatch.setattr(MT, "fetch_meta_templates", lambda cfg: [
        _meta("next_steps", MT.to_meta(messaging.TEMPLATES["next_steps"][2])[0])])
    r = admin.post("/admin/templates", {"action": "meta_check"}, follow_redirects=True)
    assert b"Checked with Meta: 1 approved and matching" in r.data
    page = admin.get("/admin/templates").data.decode()
    assert "approved · live-ready" in page and "For Meta" in page and "{{1}}" in page
    assert login("lead@t.test").post("/admin/templates", {"action": "meta_check"}).status_code == 403


def test_reengagement_follow_up_is_marketing_and_needs_marketing_consent(app, db):
    b = buyer(db)
    with app.test_request_context():
        I.ensure_templates(db)
        assert I.template(db, "inactive_followup")["category"] == "marketing"
        cid = db.scalar("SELECT contact_id FROM opportunities WHERE id = ?", (b,))
        with pytest.raises(I.ValidationError) as e:
            I.send(db, user(db, "buyer@t.test"), contact_id=cid, opportunity_id=b, template_key="inactive_followup",
                   variables={"name": "A", "sender": "B"})
    assert "marketing consent" in str(e.value)
