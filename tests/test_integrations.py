"""Milestone 7.2 — integrations configured by an admin in the app (secrets encrypted), staff numbers at onboarding."""
from conftest import user

from teamos import integrations as IG
from teamos import messaging

WA = {"action": "settings", "connector": "whatsapp", "WHATSAPP_BUSINESS_ACCOUNT_ID": "123456789",
      "WHATSAPP_PHONE_NUMBER_ID": "111222333444", "WHATSAPP_DISPLAY_NUMBER": "+91 90000 00000",
      "WHATSAPP_ACCESS_TOKEN": "EAAtest-secret-token-value", "WHATSAPP_APP_SECRET": "app-secret-xyz",
      "WHATSAPP_VERIFY_TOKEN": "verify-me-123"}


def test_admin_configures_whatsapp_in_the_app_secrets_encrypted_and_never_shown(app, db, login):
    admin = login("admin@t.test")
    r = admin.post("/admin/integrations", WA, follow_redirects=True)
    assert b"Saved:" in r.data
    row = db.one("SELECT * FROM integration_settings WHERE key = 'WHATSAPP_ACCESS_TOKEN'")
    assert row["is_secret"] == 1 and "EAAtest" not in row["value"]          # encrypted at rest
    page = admin.get("/admin/integrations").data.decode()
    assert "EAAtest-secret-token-value" not in page and "app-secret-xyz" not in page   # never shown back
    assert 'value="111222333444"' in page                                   # plain settings are shown
    audit = " ".join(r["detail"] or "" for r in db.all("SELECT detail FROM audit_events"))
    assert "EAAtest" not in audit and "WHATSAPP_ACCESS_TOKEN" in audit       # names logged, never values
    with app.test_request_context():
        st = messaging.whatsapp_status(app.config)
        assert st["missing"] == [] and not st["live"] and "NOT approved" in st["label"]
        assert isinstance(messaging.provider(app.config), messaging.MockWhatsAppProvider)
    # leaving a secret empty keeps it; "-" clears it
    admin.post("/admin/integrations", {"action": "settings", "WHATSAPP_ACCESS_TOKEN": ""})
    with app.test_request_context():
        assert IG.overlay(db)["WHATSAPP_ACCESS_TOKEN"] == "EAAtest-secret-token-value"
    admin.post("/admin/integrations", {"action": "settings", "WHATSAPP_ACCESS_TOKEN": "-"})
    with app.test_request_context():
        assert "WHATSAPP_ACCESS_TOKEN" not in IG.overlay(db)


def test_going_live_needs_typed_confirmation_and_can_always_be_undone(app, db, login):
    admin = login("admin@t.test")
    admin.post("/admin/integrations", WA)
    r = admin.post("/admin/integrations", {"action": "go_live", "confirm": "yes"}, follow_redirects=True)
    assert b"Type GO LIVE" in r.data
    with app.test_request_context():
        assert not messaging.whatsapp_status(app.config)["live"]
    admin.post("/admin/integrations", {"action": "go_live", "confirm": "GO LIVE"})
    with app.test_request_context():
        assert messaging.whatsapp_status(app.config)["live"]
        assert isinstance(messaging.provider(app.config), messaging.MetaCloudProvider)  # nothing is sent here
    admin.post("/admin/integrations", {"action": "test_only"})
    with app.test_request_context():
        assert not messaging.whatsapp_status(app.config)["live"]
    assert db.scalar("SELECT COUNT(*) FROM audit_events WHERE action = 'integration.whatsapp_live'") == 1


def test_only_admins_can_configure_and_server_settings_win(app, db, login):
    for who in ("lead@t.test", "seller@t.test", "fin@t.test"):
        assert login(who).post("/admin/integrations", WA).status_code == 403
    assert not db.scalar("SELECT COUNT(*) FROM integration_settings")
    admin = login("admin@t.test")
    r = admin.post("/admin/integrations", {"action": "settings", "PUBLIC_BASE_URL": "http://example.com"},
                   follow_redirects=True)
    assert b"must start with https://" in r.data
    app.config["SMTP_HOST"] = "smtp.server.example"
    try:
        page = admin.get("/admin/integrations").data.decode()
        assert "set on the server" in page
    finally:
        app.config["SMTP_HOST"] = None


def test_webhook_handshake_uses_the_token_saved_by_the_admin(app, db, login):
    login("admin@t.test").post("/admin/integrations", WA)
    c = app.test_client()
    ok = c.get("/webhooks/whatsapp?hub.mode=subscribe&hub.verify_token=verify-me-123&hub.challenge=42")
    assert ok.status_code == 200 and ok.data == b"42"
    assert c.get("/webhooks/whatsapp?hub.mode=subscribe&hub.verify_token=wrong&hub.challenge=1").status_code == 403


def test_new_staff_member_gets_their_whatsapp_number_at_creation(app, db, login):
    admin = login("admin@t.test")
    admin.post("/admin/users", {"full_name": "Neha New", "email": "neha@t.test", "role": "team_member",
                                "team": "seller", "password": "Abcdefghij1!", "wa_number": "9876512345",
                                "number_id": "555666777888"})
    u = user(db, "neha@t.test")
    assert u["wa_number"] == "+919876512345" and u["wa_phone_number_id"] == "555666777888"
    assert "+919876512345" in admin.get("/admin/users").data.decode()
    page = admin.get("/admin/integrations").data.decode()
    assert "Coming next" in page and "Facebook / Instagram lead forms" in page
