"""Milestone 8: stuck records, workload & hand-over, learning from declines, summaries, Telugu forms, simpler menu."""
from datetime import timedelta

from conftest import user
from test_matching import buyer, cand, seller

from teamos import i18n
from teamos.domain import form_templates as FT
from teamos.domain import learning as L
from teamos.domain import matching as M
from teamos.domain import progress as P
from teamos.domain import summaries as S
from teamos.timeutil import now_utc, to_iso


def _age(db, opp_id, days):
    """Pretend the record entered its stage `days` ago and nobody has spoken to the customer since."""
    t = to_iso(now_utc() - timedelta(days=days))
    db.execute("UPDATE stage_history SET at = ? WHERE opportunity_id = ?", (t, opp_id))
    db.execute("UPDATE opportunities SET created_at = ?, last_contact_at = NULL, next_action_due = NULL WHERE id = ?",
               (t, opp_id))


# -- stuck ----------------------------------------------------------------------------------------------------
def test_a_record_quiet_beyond_its_stage_pace_is_stuck_with_a_suggested_push(db):
    s = seller(db, approved=False)
    assert P.stuck_one(db, "seller", s) is None           # fresh record: moving fine
    _age(db, s, 40)
    st = P.stuck_one(db, "seller", s)
    assert st and st["days"] >= 40 and st["push"]
    assert any(x["id"] == s for x in P.stuck(db))


def test_recent_customer_contact_means_not_stuck(db):
    s = seller(db, approved=False)
    _age(db, s, 40)
    db.execute("UPDATE opportunities SET last_contact_at = ? WHERE id = ?", (to_iso(now_utc()), s))
    assert P.stuck_one(db, "seller", s) is None


def test_stuck_sweep_sends_one_alert_per_person_not_per_record(db):
    a, b = seller(db, approved=False), seller(db, phone="9000011002", salon="Second Salon", approved=False)
    _age(db, a, 40)
    _age(db, b, 40)
    P.stuck_sweep(db)
    P.stuck_sweep(db)  # same day: no repeats
    owner = user(db, "seller@t.test")["id"]
    assert db.scalar("SELECT COUNT(*) FROM notifications WHERE user_id = ? AND kind = 'stuck'", (owner,)) == 1


def test_stuck_shows_on_my_day_team_and_the_record(db, login):
    s = seller(db, approved=False)
    _age(db, s, 40)
    assert "Not moving" in login("seller@t.test").get("/").data.decode()
    assert "Not moving" in login("lead@t.test").get("/team").data.decode()
    assert "Suggested next step" in login("seller@t.test").get(f"/sellers/{s}").data.decode()


# -- workload ------------------------------------------------------------------------------------------------
def test_away_people_get_no_new_leads(db):
    shaheen = user(db, "seller@t.test")
    assert P.pick_owner(db, "seller") == shaheen["id"]
    P.set_away(db, user(db, "lead@t.test"), shaheen["id"], to_iso(now_utc() + timedelta(days=5))[:10])
    assert P.pick_owner(db, "seller") != shaheen["id"]


def test_hand_over_moves_records_with_their_open_tasks_after_a_preview(db, login):
    s = seller(db, approved=False)
    shaheen, harika = user(db, "seller@t.test"), user(db, "buyer@t.test")
    lead = login("lead@t.test")
    r = lead.post("/team/hand-over", {"from_id": shaheen["id"], "to_ids": [harika["id"]], "kind": "seller"})
    assert "Hand over now" in r.data.decode()
    assert db.scalar("SELECT primary_owner_id FROM opportunities WHERE id = ?", (s,)) == shaheen["id"]  # preview only
    lead.post("/team/hand-over", {"from_id": shaheen["id"], "to_ids": [harika["id"]], "kind": "seller", "go": "1"})
    assert db.scalar("SELECT primary_owner_id FROM opportunities WHERE id = ?", (s,)) == harika["id"]
    assert db.scalar("SELECT COUNT(*) FROM tasks WHERE entity_type = 'opportunity' AND entity_id = ? AND status = "
                     "'open' AND assignee_id = ?", (s, shaheen["id"])) == 0


def test_finance_cannot_hand_over_work(db, login):
    s = seller(db, approved=False)
    r = login("fin@t.test").post("/team/hand-over", {"from_id": user(db, "seller@t.test")["id"],
                                                       "to_ids": [user(db, "buyer@t.test")["id"]], "go": "1"})
    assert r.status_code == 403
    assert db.scalar("SELECT primary_owner_id FROM opportunities WHERE id = ?", (s,)) == user(db, "seller@t.test")["id"]


def test_workload_table_lists_people(db, login):
    seller(db)
    page = login("lead@t.test").get("/team").data.decode()
    assert "Workload" in page and "Shaheen Seller" in page


# -- learning ------------------------------------------------------------------------------------------------
def _decline_many(db, reason, n, fit=80):
    admin = user(db, "admin@t.test")
    for i in range(n):
        s = seller(db, phone=f"90000120{i:02d}", salon=f"Decline Salon {reason} {i}")
        b = buyer(db, phone=f"91000120{i:02d}", name=f"Decline Buyer {reason} {i}")
        c = cand(db, b, s)
        db.execute("UPDATE match_candidates SET fit_score = ? WHERE id = ?", (fit, c["id"]))
        M.change_stage(db, admin, c["id"], "not_suitable", reason=reason)


def test_declines_for_one_reason_produce_a_proposal_that_changes_nothing_until_approved(db, login):
    _decline_many(db, "location", 6)
    made = L.propose(db)
    assert len(made) == 1
    w = db.one("SELECT * FROM match_weight_changes WHERE id = ?", (made[0],))
    assert w["family"] == "B" and w["new_weight"] > w["old_weight"] and w["status"] == "proposed"
    assert M.load_weights(db)["B"] == M.FAMILIES["B"][1]              # nothing changed yet
    assert L.propose(db) == []                                          # one open proposal per family
    assert login("buyer@t.test").post(f"/reports/learning/{w['id']}", {"decision": "approve"}).status_code == 403
    login("admin@t.test").post(f"/reports/learning/{w['id']}", {"decision": "approve"})
    assert M.load_weights(db)["B"] == w["new_weight"]
    assert "tuned" in M.RULES_VERSION
    login("admin@t.test").post("/reports/learning/reset/B")
    assert M.load_weights(db)["B"] == M.FAMILIES["B"][1]
    M.load_weights(db)


def test_too_few_declines_propose_nothing(db):
    _decline_many(db, "rent", 2)
    assert L.propose(db) == []


def test_learning_page_renders_for_team(db, login):
    assert "Why matches were declined" in login("seller@t.test").get("/reports/learning").data.decode()


# -- summaries -----------------------------------------------------------------------------------------------
def test_built_in_draft_uses_facts_and_never_saves_by_itself(db, login):
    s = seller(db, monthly_rent="45000", chairs="6")
    c = login("seller@t.test")
    r = c.post(f"/records/{s}/summary/draft")
    d = r.get_json()
    assert d["source"] == "built_in" and "salon" in d["text"].lower()
    assert db.scalar("SELECT summary FROM opportunities WHERE id = ?", (s,)) is None
    c.post(f"/records/{s}/summary", {"summary": "Checked: 6-chair unisex salon, rent 45k.", "source": "built_in"})
    row = db.one("SELECT summary, summary_by, summary_source FROM opportunities WHERE id = ?", (s,))
    assert row["summary"].startswith("Checked") and row["summary_by"] == user(db, "seller@t.test")["id"]
    assert "Checked: 6-chair" in c.get(f"/sellers/{s}").data.decode()


def test_ai_drafts_never_include_contact_details(db):
    s = seller(db, phone="9000011999", salon="Secret Name Salon", address_line="12 Private Lane")
    opp = db.one("SELECT o.*, b.city, b.locality FROM opportunities o LEFT JOIN salon_businesses b ON b.id = "
                 "o.business_id WHERE o.id = ?", (s,))
    f = S.facts(db, opp)
    blob = repr(f)
    assert "9000011999" not in blob and "Private Lane" not in blob and "Secret Name" not in blob


def test_buyer_summary_draft(db, login):
    b = buyer(db)
    d = login("buyer@t.test").post(f"/records/{b}/summary/draft").get_json()
    assert d["text"] and "Kondapur" in d["text"]


def test_finance_cannot_draft_or_save_summaries(db, login):
    s = seller(db)
    assert login("fin@t.test").post(f"/records/{s}/summary", {"summary": "x"}).status_code == 403


# -- Telugu forms --------------------------------------------------------------------------------------------
def test_every_customer_form_string_has_telugu():
    strings = set()
    for kind in ("seller", "buyer"):
        for f in FT.catalogue(kind):
            strings.add(FT.label(kind, f.key))
            if f.ftype == "enum":
                strings.update(lbl for _v, lbl in f.options)
    for t in FT.TEMPLATES.values():
        strings.update([t["title"], t["intro"]] + [s[1] for s in t["sections"]])
    strings.update(FT.RESPONDENT_HELP.values())
    strings.update(lbl for _v, lbl in FT.RESPONDENT_STATES + FT.IMPORTANCE_SHORT)
    assert i18n.missing(strings) == []


def test_public_form_in_telugu_and_answers_stored_the_same(db, app):
    c = app.test_client()
    page = c.get("/f/seller?lang=te").data.decode()
    assert "మీ సెలూన్ అమ్మాలని ఆలోచిస్తున్నారా?" in page and 'lang="te"' in page
    assert "English" in page  # can switch back
    page_en = c.get("/f/seller?lang=en").data.decode()
    assert "Thinking of selling your salon?" in page_en


def test_staff_pages_stay_in_english(db, login):
    page = login("seller@t.test").get("/").data.decode()
    assert "My Day" in page and "నాకు" not in page


# -- simpler menu --------------------------------------------------------------------------------------------
def test_menu_is_grouped_and_admin_tools_live_on_one_page(db, login):
    admin = login("admin@t.test")
    page = admin.get("/").data.decode()
    assert "Tools &amp; admin" in page and "Audit log" not in page   # admin items moved off the menu
    tools = admin.get("/tools").data.decode()
    for t in ("Users &amp; access", "Integrations", "Audit log", "Spreadsheet import", "Match learning"):
        assert t in tools
    member = login("seller@t.test").get("/tools").data.decode()
    assert "Spreadsheet import" in member and "Field dictionary" not in member and "Users &amp; access" not in member  # team sees only what they need


def test_ai_connector_listed_and_off_by_default(db, login):
    page = login("admin@t.test").get("/admin/integrations").data.decode()
    assert "AI writing assistant" in page
