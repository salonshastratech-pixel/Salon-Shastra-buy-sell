"""Privacy policy and data deletion: open to anyone (Meta checks them), linked from sign-in and every customer form."""


def test_privacy_and_deletion_pages_are_public_and_linked(app):
    c = app.test_client()
    p = c.get("/privacy")
    assert p.status_code == 200 and "Privacy policy" in p.data.decode() and "support@salonshastra.com" in p.data.decode()
    d = c.get("/data-deletion")
    page = d.data.decode()
    assert d.status_code == 200 and "DELETE MY DATA" in page and "30 days" in page
    for path in ("/login", "/f/academy"):
        html = c.get(path).data.decode()
        assert 'href="/privacy"' in html and 'href="/data-deletion"' in html, path
    assert c.get("/static/brand/app-icon-1024.png").status_code == 200


def test_verify_token_can_be_generated_right_under_its_box(db, login):
    c = login("admin@t.test")
    page = c.get("/admin/integrations").data.decode()
    assert 'form="gen-verify"' in page and 'id="gen-verify"' in page and "/webhooks/whatsapp</code>" in page
    r = c.post("/admin/integrations", {"action": "verify_token"}, follow_redirects=True)
    html = r.data.decode()
    assert "Webhook verify token (copy it into Meta now" in html


def test_connect_and_check_and_real_test_message(db, login, monkeypatch):
    from teamos import messaging as M
    from teamos import integrations as IG
    admin = __import__("conftest").user(db, "admin@t.test")
    monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", "tok")
    IG.save(db, admin, {"WHATSAPP_BUSINESS_ACCOUNT_ID": "1364860231191250", "WHATSAPP_PHONE_NUMBER_ID": "580520328479798"})
    calls = []

    def fake_graph(method, path, token, timeout=15):
        calls.append((method, path))
        if path.startswith("580520328479798"):
            return True, {"display_phone_number": "+91 86868 78090", "verified_name": "Salon Shastra",
                          "name_status": "APPROVED", "quality_rating": "GREEN", "platform_type": "CLOUD_API"}
        if method == "POST":
            return True, {"success": True}
        return True, {"data": [{"whatsapp_business_api_data": {"name": "SalonshastraOS"}}]}
    monkeypatch.setattr(M, "_graph", fake_graph)
    c = login("admin@t.test")
    page = c.post("/admin/integrations", {"action": "wa_connect"}, follow_redirects=True).data.decode()
    assert ("POST", "1364860231191250/subscribed_apps") in calls
    assert "Messages sent to TeamOS: Yes" in page and "On the Cloud API: Yes" in page and "SalonshastraOS" in page
    sent = []
    monkeypatch.setattr(M.MetaCloudProvider, "send",
                        lambda self, to, body, t=None, template_name=None, language="en", **k:
                        sent.append((to, template_name)) or {"status": "provider_accepted"})
    page = c.post("/admin/integrations", {"action": "test_whatsapp", "to": "9876543210"}, follow_redirects=True).data.decode()
    assert sent == [("+919876543210", None)] and "provider accepted" in page      # real send, not the simulation


def test_submit_all_templates_to_meta(db, login, monkeypatch):
    import io
    import json as _json
    import urllib.error

    from conftest import user
    from teamos import integrations as IG
    from teamos import meta_templates as MT
    IG.save(db, user(db, "admin@t.test"), {"WHATSAPP_BUSINESS_ACCOUNT_ID": "1364860231191250"})
    monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", "tok")
    monkeypatch.setattr(MT, "fetch_meta_templates", lambda config: [{"name": "enquiry_ack", "status": "PENDING"}])
    sent = []

    class R:
        def __init__(self, data): self.data = data
        def read(self): return self.data
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_open(req, timeout=20):
        body = _json.loads(req.data.decode())
        sent.append(body)
        if body["name"] == "market_update":
            raise urllib.error.HTTPError(req.full_url, 400, "bad", {}, io.BytesIO(
                _json.dumps({"error": {"error_user_msg": "Too many variables for the text"}}).encode()))
        return R(b'{"id": "1", "status": "PENDING"}')
    monkeypatch.setattr("urllib.request.urlopen", fake_open)
    page = login("admin@t.test").post("/admin/templates", {"action": "meta_submit"}, follow_redirects=True).data.decode()
    names = [b["name"] for b in sent]
    assert "enquiry_ack" not in names and "optout_confirm" not in names and "deep_form_link" in names
    form = next(b for b in sent if b["name"] == "deep_form_link")
    assert form["category"] == "UTILITY" and form["language"] == "en"
    assert form["components"][0]["text"].startswith("Hello {{1}}, this is {{2}} from Salon Shastra.")
    assert form["components"][0]["example"]["body_text"][0][2].startswith("https://team.salonshastra.com/")
    assert next(b for b in sent if b["name"] == "inactive_followup")["category"] == "MARKETING"
    alert = db.scalar("SELECT title FROM notifications WHERE kind = 'system' ORDER BY id DESC")
    assert "Sending the templates to Meta now" in page
    assert "Templates sent to Meta for approval: 15" in alert and "market_update (Too many variables" in alert
