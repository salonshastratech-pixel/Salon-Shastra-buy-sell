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
