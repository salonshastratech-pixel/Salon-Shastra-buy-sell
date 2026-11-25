"""Bulk actions on lists, CSV export, and permanent delete of records (with everything linked to them)."""
from conftest import user
from test_matching import buyer, cand, seller
from test_open_forms import _token
from test_pilot import FULL

from teamos.domain import pilot as P


def _counts(db):
    tables = [r["name"] for r in db.all("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")]
    return {t: db.scalar(f"SELECT COUNT(*) FROM {t}") for t in tables}


def test_delete_removes_the_record_and_everything_linked_only(app, db, login):
    keep = seller(db, phone="9000011009", salon="Keep Salon")
    b = buyer(db)
    s = seller(db, phone="9000011002", salon="Test Salon X")
    cand(db, b, s)                                              # a match between the buyer and the doomed salon
    opp = db.one("SELECT * FROM opportunities WHERE id = ?", (s,))
    P.save_response(db, P.load if False else db.one("SELECT o.*, c.full_name AS contact_name, b.name AS salon_name "
                                                    "FROM opportunities o JOIN contacts c ON c.id = o.contact_id JOIN "
                                                    "salon_businesses b ON b.id = o.business_id WHERE o.id = ?", (s,)),
                    FULL, "staff")
    admin = login("admin@t.test")
    r = admin.post(f"/records/{s}/delete", {"reason": "test data", "confirm": "wrong"})
    assert db.scalar("SELECT COUNT(*) FROM opportunities WHERE id = ?", (s,)) == 1   # wrong confirmation: kept
    r = admin.post(f"/records/{s}/delete", {"reason": "test data", "confirm": opp["ref_code"]})
    assert r.status_code == 302
    assert db.scalar("SELECT COUNT(*) FROM opportunities WHERE id = ?", (s,)) == 0
    for t, col in (("seller_listings", "opportunity_id"), ("pilot_responses", "opportunity_id"),
                   ("swot_reports", "opportunity_id"), ("match_candidates", "seller_opp_id")):
        assert db.scalar(f"SELECT COUNT(*) FROM {t} WHERE {col} = ?", (s,)) == 0, t
    assert db.scalar("SELECT COUNT(*) FROM tasks WHERE entity_type = 'opportunity' AND entity_id = ?", (s,)) == 0
    assert db.scalar("SELECT COUNT(*) FROM activities WHERE entity_type = 'opportunity' AND entity_id = ?", (s,)) == 0
    assert db.scalar("SELECT COUNT(*) FROM contacts WHERE full_name = 'Owner Test Salon X'") == 0
    # nothing unrelated touched; the audit log remembers
    assert db.scalar("SELECT COUNT(*) FROM opportunities WHERE id IN (?, ?)", (keep, b)) == 2
    assert db.scalar("SELECT COUNT(*) FROM seller_listings WHERE opportunity_id = ?", (keep,)) == 1
    assert db.scalar("SELECT COUNT(*) FROM audit_events WHERE action = 'record.deleted'") == 1
    assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    # team members cannot delete
    assert login("seller@t.test").post(f"/records/{keep}/delete", {"reason": "x", "confirm": "x"}).status_code in (302, 403)


def test_bulk_assign_task_delete_and_export(app, db, login):
    pub = app.test_client()
    for i, name in enumerate(("TEST One", "TEST Two")):
        page = pub.get("/f/pilot").data.decode()
        pub.post("/f/pilot", data={**FULL, "owner_name": name, "salon_name": name + " Salon",
                                   "contact_number": f"900000020{i}", "form_token": _token(page), "consent_contact": "1"})
    ids = [r["id"] for r in db.all("SELECT id FROM opportunities ORDER BY id")]
    harika = user(db, "buyer@t.test")
    lead = login("lead@t.test")
    page = lead.get("/sellers/").data.decode()
    assert 'id="bulk"' in page and 'data-select-all' in page and "Export this list" not in page   # export: admins
    lead.post("/records/bulk", {"kind": "seller", "action": "assign", "owner_id": str(harika["id"]), "ids": [str(i) for i in ids]})
    assert {r["primary_owner_id"] for r in db.all("SELECT primary_owner_id FROM opportunities")} == {harika["id"]}
    lead.post("/records/bulk", {"kind": "seller", "action": "task", "title": "Call back about the pilot",
                                "due_at": "2030-01-01T10:00", "ids": [str(i) for i in ids]})
    assert db.scalar("SELECT COUNT(*) FROM tasks WHERE title = 'Call back about the pilot'") == 2
    assert lead.post("/records/bulk", {"kind": "seller", "action": "delete", "reason": "test", "confirm": "DELETE",
                                       "ids": [str(ids[0])]}).status_code in (302, 403)
    assert db.scalar("SELECT COUNT(*) FROM opportunities") == 2                       # not an admin: nothing deleted
    admin = login("admin@t.test")
    csv = admin.get("/sellers/?q=TEST&export=csv")
    body = csv.data.decode("utf-8-sig")
    assert csv.headers["Content-Type"].startswith("text/csv") and "TEST One" in body and "Lead came in" in body
    assert lead.get("/sellers/?export=csv").status_code in (302, 403)
    admin.post("/records/bulk", {"kind": "seller", "action": "delete", "reason": "test data", "confirm": "nope",
                                 "ids": [str(i) for i in ids]})
    assert db.scalar("SELECT COUNT(*) FROM opportunities") == 2                       # confirmation word required
    admin.post("/records/bulk", {"kind": "seller", "action": "delete", "reason": "test data", "confirm": "DELETE",
                                 "ids": [str(i) for i in ids]})
    assert db.scalar("SELECT COUNT(*) FROM opportunities") == 0
    assert db.execute("PRAGMA foreign_key_check").fetchall() == []



def test_funnel_report_counts_each_step_by_source_and_person(app, db, login):
    pub = app.test_client()
    page = pub.get("/f/pilot").data.decode()
    pub.post("/f/pilot", data={**FULL, "form_token": _token(page), "consent_contact": "1"})
    seller(db, phone="9000011003", salon="Phone Salon")             # a lead never contacted, no form
    c = login("seller@t.test")
    oid = db.scalar("SELECT id FROM opportunities ORDER BY id LIMIT 1")
    c.post(f"/sellers/{oid}/call", {"outcome": "connected", "next_title": "x", "next_due": "2030-01-01T10:00"})
    from teamos.domain import funnel as FN
    f = FN.build(db, "seller", "month")
    counts = dict(zip([k for k, _l in f["steps"]], f["overall"]["counts"]))
    assert counts["leads"] == 2 and counts["contacted"] == 1 and counts["spoke"] == 1 and counts["form"] == 1
    assert {r["name"] for r in f["by_source"]} >= {"Website / open form"}
    page = c.get("/reports/funnel?kind=seller&period=month").data.decode()
    assert "Funnel" in page and "Filled the pilot form" in page and "By lead source" in page
    assert "Registration paid" in c.get("/reports/funnel?kind=buyer&period=all").data.decode()



def test_pipeline_board_lists_by_stage_and_moves_cards(app, db, login):
    s = seller(db, phone="9000011004", salon="Board Salon")
    c = login("seller@t.test")
    page = c.get("/sellers/board").data.decode()
    assert "pipeline board" in page and "Board Salon" in page and 'aria-label="Contact attempted"' in page
    r = c.post(f"/sellers/{s}/stage", {"to": "contact_attempted", "next": "/sellers/board"})
    assert r.headers["Location"].endswith("/sellers/board")
    assert db.scalar("SELECT stage FROM opportunities WHERE id = ?", (s,)) == "contact_attempted"
    assert "pipeline board" in c.get("/buyers/board?owner=me").data.decode()
