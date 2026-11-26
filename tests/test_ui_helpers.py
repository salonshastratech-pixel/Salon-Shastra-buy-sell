"""Small display helpers used across lists and profiles, and the filter bar's 'More filters' counter."""
from conftest import user

from teamos import _nice, _short_action


def test_names_typed_in_lower_case_are_shown_properly():
    assert _nice("morning kisses salon") == "Morning Kisses Salon"
    assert _nice("GlowUp by RK") == "GlowUp by RK"           # anything with capitals is left as typed
    assert _nice("") == "" and _nice(None) == "" and _nice("123") == "123"


def test_next_action_drops_the_name_already_on_the_row():
    assert _short_action("First call: Anji (morning kisses salon)", "Anji", "Morning Kisses") == "First call"
    assert _short_action("Call back: Ravi", "Anji") == "Call back: Ravi"
    assert _short_action("Send pilot form", "Anji") == "Send pilot form"


def test_more_filters_shows_how_many_are_in_use(db, login):
    c = login("admin@t.test")
    page = c.get("/sellers/?cat=sell&src=meta_lead&temp=hot").data.decode()
    assert "More filters" in page and 'badge info small">2<' in page and "Clear all" in page
    page = c.get("/buyers/").data.decode()
    assert "More filters" in page and "Clear all" not in page
    assert user(db, "admin@t.test")


def test_an_old_link_to_a_removed_task_explains_instead_of_erroring(login):
    r = login("seller@t.test").get("/tasks/99999", follow_redirects=True)
    assert r.status_code == 200 and "no longer exists" in r.data.decode()


def test_review_page_bulk_bar_has_its_own_wording(login):
    page = login("admin@t.test").get("/records/review").data.decode()
    assert 'data-empty="Tick the records you want to remove for good."' in page and "bulk-inline" in page
