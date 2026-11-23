"""Fixes from the 100-seller / 100-buyer load sweep: faster Match Desk and Team page (same numbers), clearer messages."""
from test_matching import buyer, cand, seller

from teamos.domain import matching as M


def test_one_pass_queue_counts_equal_the_per_queue_counts(db):
    for i in range(3):
        seller(db, phone=f"900001100{i}", salon=f"Salon {i}")
    for i in range(3):
        buyer(db, phone=f"910001100{i}", name=f"Buyer {i}", ceiling=f"{20 + i * 10}L")
    M.refresh(db, force=True)
    fast = M.queue_counts(db)
    for k, (_l, cond) in M.QUEUES.items():
        slow = db.scalar("SELECT COUNT(*) FROM match_candidates mc JOIN opportunities bo ON bo.id = mc.buyer_opp_id "
                         "JOIN opportunities so ON so.id = mc.seller_opp_id WHERE " + M.ACTIVE_WHERE + f" AND ({cond})")
        assert fast[k] == slow, k


def test_open_tasks_attached_only_for_shown_rows(db, login):
    s, b = seller(db), buyer(db)
    c = cand(db, b, s)
    login("lead@t.test").post(f"/matches/{c['id']}/tasks", {"buyer_task": "Ask budget", "seller_task": "Ask rent",
                                                            "due_at": "2030-01-01T10:00"})
    rows = M.attach_open_tasks(db, db.all(M.CAND_SELECT_LIGHT + "WHERE mc.id = ?", (c["id"],)))
    full = db.one(M.CAND_SELECT + "WHERE mc.id = ?", (c["id"],))
    assert rows[0]["open_tasks"] == full["open_tasks"] == 2


def test_desk_and_team_pages_render_with_queue_parameter(db, login):
    s, b = seller(db), buyer(db)
    cand(db, b, s)
    lead = login("lead@t.test")
    for url in ("/matches/", "/matches/?queue=all", "/matches/?q=info", "/matches/?queue=near&mine=1", "/team"):
        assert lead.get(url).status_code == 200, url


def test_asking_for_answers_that_need_a_call_explains_instead_of_erroring(db, login):
    s = seller(db)
    b = buyer(db, localities=("Gachibowli",))   # the salon is in Kondapur: a question for a call, not a form field
    c = cand(db, b, s)
    r = login("lead@t.test").post(f"/matches/{c['id']}/ask/buyer", {"channel": "copy_link"}, follow_redirects=True)
    body = r.data.decode()
    assert "Pick at least one question" not in body
    assert "need a quick call" in body or "Link created" in body or "/f/i/" in body


def test_financial_notes_are_shown_as_warnings_not_errors(db, login):
    s = seller(db)
    c = login("seller@t.test")
    c.post(f"/sellers/{s}/financials", {"month": "2026-06", "revenue": "5L", "expenses": "3L", "drawings": ""})
    page = c.get(f"/sellers/{s}?tab=financials").data.decode()
    assert 'class="flash warn">All figures are seller-declared' in page
    assert 'class="flash error">All figures are seller-declared' not in page
