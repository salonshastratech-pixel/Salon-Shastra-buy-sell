"""Dark theme: the team's pages open dark by default; a device that chose light keeps it; customer forms stay light."""


def test_dark_by_default_light_by_choice_forms_unchanged(app, login):
    c = login("seller@t.test")
    page = c.get("/").data.decode()
    assert '<html lang="en" data-theme="dark">' in page and "data-theme-toggle" in page and "Light mode" in page
    c.c.set_cookie("teamos_theme", "light")
    page = c.get("/").data.decode()
    assert '<html lang="en" data-theme="light">' in page and "Dark mode" in page
    assert 'data-theme="dark"' in app.test_client().get("/login").data.decode()
    assert 'data-theme="dark"' not in app.test_client().get("/f/academy").data.decode()
    css = app.test_client().get("/static/app.css").data.decode()
    assert 'html[data-theme="dark"]' in css and "#893815" in css
    assert app.test_client().get("/manifest.webmanifest").json["theme_color"] == "#3b1a0e"
