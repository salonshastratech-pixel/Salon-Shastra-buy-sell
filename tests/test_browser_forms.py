"""Regression for a real-browser bug: forms must carry the CSRF token *rendered in the page*.

Earlier tests injected the token directly, which hid that macro-rendered forms had an empty token.
These tests only use tokens scraped from the HTML, as a browser would.
"""
import re

from conftest import PW, SELLER_FORM

PAGES = ["/", "/sellers/new", "/tasks/", "/team", "/admin/users", "/admin/duplicates", "/imports/"]


def browser(app, email):
    c = app.test_client()
    c.post("/login", data={"email": email, "password": PW})
    return c


def tokens(html):
    return re.findall(r'name="csrf_token" value="([^"]*)"', html)


def test_every_form_on_every_page_has_a_real_token(app, login, db):
    login("seller@t.test").post("/sellers/new", SELLER_FORM)
    c = browser(app, "admin@t.test")
    opp_id = db.scalar("SELECT id FROM opportunities")
    tid = db.scalar("SELECT id FROM tasks")
    pages = PAGES + [f"/sellers/{opp_id}?tab={t}" for t in
                     ("overview", "intake", "financials", "assessment", "approval")] + [f"/tasks/{tid}"]
    for url in pages:
        html = c.get(url).data.decode()
        found = tokens(html)
        assert found, url
        assert all(found), f"empty CSRF token in a form on {url}"


def test_create_seller_using_only_what_the_page_provides(app, db):
    c = browser(app, "seller@t.test")
    token = tokens(c.get("/sellers/new").data.decode())[0]
    r = c.post("/sellers/new", data={**SELLER_FORM, "csrf_token": token})
    assert r.status_code == 302 and db.scalar("SELECT COUNT(*) FROM opportunities") == 1
    opp_id = db.scalar("SELECT id FROM opportunities")
    page = c.get(f"/sellers/{opp_id}").data.decode()
    token = tokens(page)[-1]
    r = c.post(f"/sellers/{opp_id}/note", data={"body": "from the page", "csrf_token": token})
    assert r.status_code == 302 and db.scalar("SELECT COUNT(*) FROM activities WHERE kind = 'note'") == 1
