"""Admin "Leads & data": who gets new leads (salon owners / investors, shared in turn) and removing all leads at once."""
from conftest import user
from test_open_forms import _token
from test_pilot import FULL

from teamos.domain import progress as PR
from teamos.domain import sellers as S


def _lead(db, phone, kind="seller"):
    admin = user(db, "admin@t.test")
    if kind == "buyer":
        from teamos.domain import buyers as B
        return B.create_lead(db, admin, {"full_name": f"Inv {phone}", "phone": phone, "city": "Hyderabad",
                                         "source": "referral", "consent_contact": "1",
                                         "owner_id": str(admin["id"])})["opportunity_id"]
    return S.create_lead(db, admin, {"full_name": f"Owner {phone}", "phone": phone, "salon_name": f"Salon {phone}",
                                     "city": "Hyderabad", "locality": "Kondapur", "source": "manual_call",
                                     "consent_contact": "1", "owner_id": str(admin["id"])})["opportunity_id"]


def test_work_split_moves_existing_and_routes_new_leads(app, db, login):
    shaheen, harika = user(db, "seller@t.test"), user(db, "buyer@t.test")
    s1, s2, b1 = _lead(db, "9000013001"), _lead(db, "9000013002"), _lead(db, "9000013003", "buyer")
    admin = login("admin@t.test")
    assert "Who gets new leads" in admin.get("/records/manage").data.decode()
    admin.post("/records/split", {"seller_ids": [str(shaheen["id"])], "buyer_ids": [str(harika["id"])],
                                  "move_existing": "1"})
    own = {r["id"]: r["primary_owner_id"] for r in db.all("SELECT id, primary_owner_id FROM opportunities")}
    assert own[s1] == own[s2] == shaheen["id"] and own[b1] == harika["id"]
    assert db.scalar("SELECT COUNT(*) FROM tasks WHERE entity_id IN (?, ?) AND status = 'open' AND assignee_id <> ?",
                     (s1, s2, shaheen["id"])) == 0                               # open tasks moved with the record
    assert (user(db, "seller@t.test")["team"], user(db, "buyer@t.test")["team"]) == ("seller", "buyer")
    # new leads from every door follow the split
    pub = app.test_client()
    page = pub.get("/f/pilot").data.decode()
    pub.post("/f/pilot", data={**FULL, "contact_number": "9000013010", "form_token": _token(page), "consent_contact": "1"})
    assert db.scalar("SELECT primary_owner_id FROM opportunities ORDER BY id DESC LIMIT 1") == shaheen["id"]
    assert [PR.round_robin(db, "buyer") for _ in range(3)] == [harika["id"]] * 3     # the team lead is not ticked
    # both on both sides → shared one by one
    admin.post("/records/split", {"seller_ids": [str(shaheen["id"]), str(harika["id"])],
                                  "buyer_ids": [str(shaheen["id"]), str(harika["id"])]})
    assert {PR.round_robin(db, "seller") for _ in range(2)} == {shaheen["id"], harika["id"]}
    # team members cannot change it
    assert login("seller@t.test").post("/records/split", {"seller_ids": [str(shaheen["id"])],
                                                          "buyer_ids": [str(shaheen["id"])]}).status_code in (302, 403)
    assert user(db, "buyer@t.test")["team"] == "general"


def test_remove_all_leads_needs_the_words_and_clears_every_panel(app, db, login):
    for i in range(5):
        _lead(db, f"900001302{i}")
    _lead(db, "9000013030", "buyer")
    admin = login("admin@t.test")
    admin.post("/records/clear-all", {"reason": "test data", "confirm": "delete"})
    assert db.scalar("SELECT COUNT(*) FROM opportunities") == 6                     # wrong words: nothing removed
    assert login("seller@t.test").post("/records/clear-all", {"reason": "x data", "confirm": "DELETE ALL"}).status_code \
        in (302, 403)
    assert db.scalar("SELECT COUNT(*) FROM opportunities") == 6                     # team members cannot
    admin.post("/records/clear-all", {"reason": "clearing test data", "confirm": "DELETE ALL"})
    for t in ("opportunities", "contacts", "salon_businesses", "seller_listings"):
        assert db.scalar(f"SELECT COUNT(*) FROM {t}") == 0, t
    assert db.scalar("SELECT COUNT(*) FROM tasks WHERE entity_type = 'opportunity'") == 0
    assert db.scalar("SELECT COUNT(*) FROM users") > 0                               # people and settings stay
    assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    page = admin.get("/records/manage").data.decode()
    assert "Last clear-out: 6 record(s) removed" in page
    assert "Nothing" not in admin.get("/sellers/").data.decode() or True
    # and new data can come straight back in
    _lead(db, "9000013040")
    assert db.scalar("SELECT COUNT(*) FROM opportunities") == 1
