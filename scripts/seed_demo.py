"""Synthetic demonstration data. Every record is flagged is_synthetic=1 and shown with a badge.

Names, phone numbers (+91 90000 0xxxx range) and businesses are invented. Not for production.
"""
import os
from datetime import timedelta

from teamos.domain import buyers as BY
from teamos.domain import sellers as S
from teamos.domain import tasks as T
from teamos.security import hash_password
from teamos.timeutil import now_iso, now_utc, to_iso

DEMO_PASSWORD = os.environ.get("SEED_PASSWORD", "Demo-Shastra-2026")

USERS = [
    ("founder@demo.salonshastra.test", "Founder (demo)", "super_admin", "admin"),
    ("shaheen@demo.salonshastra.test", "Shaheen (demo)", "team_member", "seller"),
    ("harika@demo.salonshastra.test", "Harika (demo)", "team_member", "buyer"),
    ("lead@demo.salonshastra.test", "Ravi Team Lead (demo)", "team_lead", "general"),
    ("accounts@demo.salonshastra.test", "Accounts (demo)", "finance", "finance"),
]


def _ago(**kw):
    return to_iso(now_utc() - timedelta(**kw))


def _in(**kw):
    return to_iso(now_utc() + timedelta(**kw))


def seed(db):
    if db.scalar("SELECT COUNT(*) FROM users WHERE is_synthetic = 1"):
        added = []
        if not db.scalar("SELECT COUNT(*) FROM opportunities WHERE kind = 'buyer' AND is_synthetic = 1"):
            seed_buyers(db)  # demo database created before Milestone 3
            added.append("buyers (M3)")
        if not db.scalar("SELECT COUNT(*) FROM form_invitations"):
            seed_forms(db)  # demo database created before Milestone 4
            added.append("forms & requests (M4)")
        if not db.scalar("SELECT COUNT(*) FROM match_candidates"):
            seed_matches(db)  # demo database created before Milestone 5
            added.append("scored matches (M5)")
        if not db.scalar("SELECT COUNT(*) FROM teasers"):
            seed_deals(db)  # demo database created before Milestone 6
            added.append("teaser, introduction, deal & fee ledger (M6)")
        if not db.scalar("SELECT COUNT(*) FROM conversations WHERE phone = '+919000000999'"):
            seed_inbox(db)  # demo database created before Milestone 7
            added.append("WhatsApp inbox conversations & a draft batch (M7)")
        db.execute("UPDATE tasks SET is_synthetic = 1 WHERE entity_id IN "
                   "(SELECT id FROM opportunities WHERE is_synthetic = 1)")
        print("Synthetic demo data added: " + ", ".join(added) if added else "Demo data already present — skipping.")
        return
    ids = {}
    for email, name, role, team in USERS:
        ids[team if team != "general" else "lead"] = db.insert("users", {
            "email": email, "full_name": name, "role": role, "team": team, "is_synthetic": 1, "is_active": 1,
            "password_hash": hash_password(DEMO_PASSWORD), "created_at": now_iso()})
    founder = db.one("SELECT * FROM users WHERE id = ?", (ids["admin"],))
    shaheen = db.one("SELECT * FROM users WHERE id = ?", (ids["seller"],))
    harika = db.one("SELECT * FROM users WHERE id = ?", (ids["buyer"],))
    lead = db.one("SELECT * FROM users WHERE id = ?", (ids["lead"],))

    def new_seller(actor, **kw):
        base = {"city": "Hyderabad", "source": "manual_call", "consent_contact": "1", "is_synthetic": 1,
                "owner_id": str(shaheen["id"])}
        base.update(kw)
        return S.create_lead(db, founder if actor is None else actor, base, dedupe_decision=kw.pop("dd", None)
                             )["opportunity_id"]

    # 1. Well-qualified, approved listing --------------------------------------------
    o1 = new_seller(founder, full_name="Kavya Reddy", phone="9000000101", salon_name="Glow Studio Unisex",
                    locality="Kondapur", source="meta_lead", operating_status="operating", ownership_role="owner",
                    sale_purpose="sell_entire", asking_price="22L", sale_timeline="within_3m")
    opp = S.load(db, o1)
    S.log_call(db, shaheen, opp, "connected", "Owner relocating to Bengaluru; keen to close in 2–3 months.",
               db.scalar("SELECT id FROM tasks WHERE entity_id = ? AND status = 'open'", (o1,)),
               "Collect lease copy and last 6 months POS report", _in(days=1))
    full = {
        "ownership": {"legal_name": "Glow Studio LLP", "signatory_name": "Kavya Reddy", "owners_count": "1",
                      "outlets_count": "1", "transfer_authority": "yes", "existing_broker": "no"},
        "profile": {"operating_years": "4", "gender_format": "unisex", "business_model": "independent",
                    "positioning": "premium", "address_line": "Plot 14, Botanical Garden Rd", "landmark": "Near Sarath City",
                    "pin_code": "500084"},
        "asset": {"carpet_area_sqft": "1400", "chairs": "8", "wash_stations": "3", "treatment_rooms": "2",
                  "equipment_condition": "good", "inclusions_note": "All chairs, 2 pedicure stations; excludes owner's art."},
        "lease": {"monthly_rent": "1.1L", "rent_deposit": "6L", "deposit_treatment": "buyer_pays_separately",
                  "lease_expiry": "2029-03-31", "lease_renewal": "yes", "lease_transfer_status": "confirmed_transferable",
                  "rent_escalation_pct": "5", "rent_arrears": "0"},
        "customers": {"avg_monthly_bills": "780", "avg_ticket": "1450", "repeat_customer_pct": "55",
                      "prepaid_liabilities": "1.2L"},
        "operations": {"avg_monthly_sales": "11.3L", "avg_monthly_expenses": "8.6L"},
        "staff": {"staff_headcount": "11", "key_staff_dependence": "medium", "owner_involvement": "part_time",
                  "staff_continuity": "partial"},
        "liabilities": {"outstanding_loans": "0", "supplier_dues": "45000", "employee_dues": "0", "litigation": "no"},
        "reason": {"sale_reason": "relocation", "desired_exit_date": "2027-02-28", "transition_support": "yes"},
        "commercial": {"negotiable": "yes", "negotiable_min_price": "19.5L", "sale_structure": "asset",
                       "stock_value_separate": "80000", "working_capital_estimate": "2L", "expected_handover_weeks": "4"},
    }
    for sec, vals in full.items():
        S.update_fields(db, shaheen, S.load(db, o1), vals, sec)
    months = [("2026-05", "10.9L", "8.4L"), ("2026-06", "11.2L", "8.7L"), ("2026-07", "10.4L", "8.5L"),
              ("2026-08", "11.8L", "8.8L"), ("2026-09", "11.5L", "8.6L"), ("2026-10", "12.1L", "8.9L")]
    for m, r, e in months:
        S.save_month(db, shaheen, S.load(db, o1), m, r, e, "1.5L", "pos_report")
    db.execute("UPDATE seller_monthly_financials SET evidence_status = 'document_supported' WHERE month >= '2026-08' "
               "AND listing_id = (SELECT id FROM seller_listings WHERE opportunity_id = ?)", (o1,))
    for f, st in [("asking_price", "staff_reviewed"), ("lease_transfer_status", "document_supported"),
                  ("rent_deposit", "document_supported"), ("chairs", "verified")]:
        S.set_evidence(db, lead, S.load(db, o1), f, st, "Seen during site visit (synthetic)")
    for kind, txt, src, conf in [
            ("strength", "High repeat clientele from nearby gated communities", "seller", "medium"),
            ("strength", "Lease confirmed transferable to 2029", "document", "high"),
            ("weakness", "Two senior stylists may leave with owner", "staff_observation", "medium"),
            ("opportunity", "Underused treatment rooms — skin/facial services", "staff_observation", "medium"),
            ("threat", "New premium chain opening 1 km away in 2027", "third_party", "low")]:
        S.add_swot(db, shaheen, S.load(db, o1), kind, txt, src, conf, None)
    for key, _ in S.LISTING_CHECKLIST:
        S.set_checklist(db, lead, S.load(db, o1), key, "reviewed", None)
    S.change_stage(db, shaheen, S.load(db, o1), "basic_qualified", None)
    S.change_stage(db, shaheen, S.load(db, o1), "assessment_review", None)
    S.request_review(db, shaheen, S.load(db, o1))
    S.decide_listing(db, founder, S.load(db, o1), "approved", "Synthetic demo approval")
    for t in db.all("SELECT id FROM tasks WHERE entity_id = ? AND task_type = 'approval' AND status = 'open'", (o1,)):
        T.complete_task(db, founder, t["id"], "Approved")

    # 2. Partial intake, deposit unknown ----------------------------------------------
    o2 = new_seller(shaheen, full_name="Imran Shaikh", phone="9000000102", salon_name="Mane Street Men's Salon",
                    locality="Madhapur", source="whatsapp", operating_status="operating", ownership_role="owner",
                    sale_purpose="sell_entire", asking_price="9L", sale_timeline="3_6m", owner_id=str(shaheen["id"]))
    S.update_fields(db, shaheen, S.load(db, o2), {"chairs": "4", "gender_format": "men", "business_model": "independent",
                                                 "positioning": "value", "monthly_rent": "45000",
                                                 "rent_deposit__state": "unknown", "deposit_treatment__state": "unknown"},
                    "asset")
    S.update_fields(db, shaheen, S.load(db, o2), {"monthly_rent": "45000", "rent_deposit__state": "unknown",
                                                 "deposit_treatment__state": "unknown",
                                                 "lease_transfer_status": "landlord_pending"}, "lease")
    S.update_fields(db, shaheen, S.load(db, o2), {"gender_format": "men", "business_model": "independent",
                                                 "positioning": "value"}, "profile")
    S.log_call(db, shaheen, S.load(db, o2), "connected", "Doesn't know deposit amount; will check agreement.",
               db.scalar("SELECT id FROM tasks WHERE entity_id = ? AND status = 'open'", (o2,)),
               "Get rent deposit amount from lease agreement", _ago(hours=3))

    # 3. Franchise, revenue present but costs missing ---------------------------------
    o3 = new_seller(founder, full_name="Sunitha Rao", phone="9000000103", salon_name="Lotus Women's Spa & Salon",
                    locality="Jubilee Hills", source="referral", operating_status="operating",
                    ownership_role="partner", sale_purpose="franchise_transfer", asking_price="35L",
                    sale_timeline="6_12m", owner_id=str(harika["id"]), priority="high")
    S.update_fields(db, harika, S.load(db, o3), {"gender_format": "women", "business_model": "franchise",
                                                "franchise_brand": "(synthetic brand)", "positioning": "luxury"}, "profile")
    for m, r, e in [("2026-07", "14L", ""), ("2026-08", "15.2L", ""), ("2026-09", "14.6L", "11.9L"),
                    ("2026-10", "15.9L", "")]:
        S.save_month(db, harika, S.load(db, o3), m, r, e, "", "seller_sheet")
    S.log_call(db, harika, S.load(db, o3), "callback_requested", "Partner wants to consult co-owner first.",
               db.scalar("SELECT id FROM tasks WHERE entity_id = ? AND status = 'open'", (o3,)),
               "Call back after co-owner discussion; ask for monthly cost sheet", _in(hours=2))

    # 4. Brand-new lead, first contact overdue ----------------------------------------
    o4 = new_seller(lead, full_name="Venkat Naidu", phone="9000000104", salon_name="Urban Trim", locality="Kukatpally",
                    source="landing_page", sale_purpose="unsure", owner_id=str(shaheen["id"]))
    db.execute("UPDATE tasks SET due_at = ? WHERE entity_id = ? AND task_type = 'first_contact'", (_ago(hours=5), o4))
    T.refresh_next_action(db, "opportunity", o4)

    # 5. Nurture -----------------------------------------------------------------------
    o5 = new_seller(shaheen, full_name="Priya Menon", phone="9000000105", salon_name="Velvet Touch Salon",
                    locality="Gachibowli", source="event", operating_status="operating", ownership_role="owner",
                    sale_purpose="seek_help", sale_timeline="no_rush", owner_id=str(shaheen["id"]))
    S.log_call(db, shaheen, S.load(db, o5), "connected", "Exploring turnaround help, not selling now.",
               db.scalar("SELECT id FROM tasks WHERE entity_id = ? AND status = 'open'", (o5,)), "Check in", _in(days=2))
    S.change_stage(db, shaheen, S.load(db, o5), "nurture", "Wants turnaround help; revisit in 60 days", _in(days=60))

    # 6. Same owner, second outlet (links to existing contact) -------------------------
    new_seller(shaheen, full_name="Kavya Reddy", phone="+91 90000 00101", salon_name="Glow Studio Express",
               locality="Kokapet", source="manual_call", sale_purpose="sell_entire", ownership_role="owner",
               dd=f"link:{S.load(db, o1)['contact_id']}")

    # 7. Possible duplicate flagged for review -----------------------------------------
    new_seller(harika, full_name="I. Shaikh", phone="9000000102", salon_name="Mane Street Gents", locality="Madhapur",
               source="import", owner_id=str(harika["id"]), dd="new_flagged")

    # A cross-team request and an overdue team task
    with db:
        T.create_task(db, shaheen, title="Confirm whether any active buyer accepts Madhapur men's format",
                      assignee_id=harika["id"], due_at=_ago(hours=20), task_type="collect_info", priority="normal",
                      entity_type="opportunity", entity_id=o2, is_synthetic=1)
    seed_buyers(db)
    seed_forms(db)
    seed_matches(db)
    seed_deals(db)
    seed_inbox(db)
    db.execute("UPDATE tasks SET is_synthetic = 1")
    print(f"Synthetic demo data created. Users (password: {DEMO_PASSWORD}):")
    for email, name, role, _ in USERS:
        print(f"  {email:40s} {role}")


def seed_buyers(db):
    """Milestone 3: synthetic buyers / investors. Phones are in the reserved +91 90000 002xx demo range."""
    u = {r["email"].split("@")[0]: r for r in db.all("SELECT * FROM users WHERE is_synthetic = 1")}
    founder, harika, lead, shaheen = u["founder"], u["harika"], u["lead"], u["shaheen"]

    def new_buyer(actor, dd=None, **kw):
        base = {"city": "Hyderabad", "source": "manual_call", "consent_contact": "1", "is_synthetic": 1,
                "owner_id": str(harika["id"])}
        base.update(kw)
        return BY.create_lead(db, actor, base, dedupe_decision=dd)["opportunity_id"]

    def first_task(oid):
        return db.scalar("SELECT id FROM tasks WHERE entity_id = ? AND status = 'open' ORDER BY id", (oid,))

    # 1. Fully qualified acquirer — ready for the Match Desk (M5) ----------------------
    b1 = new_buyer(founder, full_name="Arjun Rao (demo)", phone="9000000201", locality="Kondapur", source="referral",
                   need_type="acquire_operating", decision_maker="yes", all_in_max="35L", purchase_window="90d",
                   consent_share_criteria="1")
    BY.log_call(db, harika, BY.load(db, b1), "connected",
                "Runs one salon in Miyapur; wants a second unisex outlet in the west.",
                first_task(b1), "Send detailed buyer form", _in(days=1))
    sections = {
        "intent": {"investor_type": "individual", "salon_background": "operator"},
        "budget": {"budget_min": "15L", "budget_max": "25L", "all_in_max": "35L", "includes_deposit": "yes",
                   "includes_stock": "yes", "includes_working_capital": "no", "finance_dependence": "none"},
        "funding": {"funding_source": "self", "cash_ready": "now", "cash_ready_amount": "30L"},
        "geography": {"relocate_willing": "no", "max_commute_km": "15"},
        "salon": {"chairs_min": "6", "chairs_min__importance": "flexible", "chairs_max": "10",
                  "area_min_sqft": "900", "area_min_sqft__importance": "prefer", "staff_continuity_need": "partial",
                  "staff_continuity_need__importance": "must"},
        "business": {"min_monthly_sales": "8L", "min_monthly_sales__importance": "prefer", "turnaround_ok": "no",
                     "max_monthly_rent": "1.2L", "max_monthly_rent__importance": "must", "min_lease_years_left": "2",
                     "min_lease_years_left__importance": "must", "liability_tolerance": "low"},
        "timing": {"involvement": "owner_operator", "visit_availability": "weekends",
                   "transition_support_need": "yes", "transition_support_need__importance": "prefer"},
        "authority": {"payment_approver": "Self", "decision_timeline": "Within a week of a good visit",
                      "must_haves": "Ground floor, parking, at least 2 wash stations",
                      "deal_breakers": "Pending rent arrears or litigation"},
    }
    for sec, vals in sections.items():
        BY.update_fields(db, harika, BY.load(db, b1), vals, sec)
    BY.set_choice_preferences(db, harika, BY.load(db, b1), {
        "pref__gender_format__unisex": "must", "pref__gender_format__men": "avoid",
        "pref__gender_format__women": "flexible", "pref__business_model__independent": "prefer",
        "pref__business_model__franchise": "flexible", "pref__positioning__premium": "prefer",
        "pref__positioning__value": "avoid", "pref__sale_structure__entity": "prefer",
        "pref__sale_structure__asset": "flexible", "pref__sale_structure__partial_stake": "avoid"})
    for loc in ("Gachibowli", "Madhapur"):
        BY.add_locality(db, harika, BY.load(db, b1), "preferred", "Hyderabad", loc)
    BY.add_locality(db, harika, BY.load(db, b1), "excluded", "Hyderabad", "Old City", "Too far to manage daily")
    for st in ("requirements_captured", "basic_qualified", "funding_review"):
        BY.change_stage(db, harika, BY.load(db, b1), st, None)
    BY.set_funding_review(db, lead, BY.load(db, b1), "verified",
                          "Synthetic: bank statement (Nov) shows Rs 31 L available; no loan needed.")
    BY.confirm_criteria(db, lead, BY.load(db, b1))
    BY.change_stage(db, lead, BY.load(db, b1), "qualified_shortlist", None)

    # 2. Investor group — funding evidence requested ----------------------------------
    b2 = new_buyer(founder, full_name="Deepa Iyer (demo)", phone="9000000202", locality="Jubilee Hills",
                   source="event", need_type="partnership", decision_maker="shared", all_in_max="60L",
                   purchase_window="180d")
    BY.log_call(db, harika, BY.load(db, b2), "connected", "Group of three investors; want a stake in a women's spa.",
                first_task(b2), "Collect funding proof from the group", _in(days=3))
    BY.update_fields(db, harika, BY.load(db, b2), {"investor_type": "investor_group"}, "intent")
    BY.update_fields(db, harika, BY.load(db, b2), {"funding_source": "investor_group", "cash_ready": "within_30d"},
                     "funding")
    BY.set_choice_preferences(db, harika, BY.load(db, b2), {"pref__gender_format__women": "must",
                                                            "pref__positioning__luxury": "prefer",
                                                            "pref__sale_structure__partial_stake": "must"})
    BY.add_locality(db, harika, BY.load(db, b2), "preferred", "Hyderabad", "Banjara Hills")
    for st in ("requirements_captured", "basic_qualified", "funding_review"):
        BY.change_stage(db, harika, BY.load(db, b2), st, None)
    BY.set_funding_review(db, lead, BY.load(db, b2), "evidence_requested",
                          "Asked for each partner's commitment letter")

    # 3. Wants to OPEN a new salon — a separate need, never treated as a resale buyer ----
    b3 = new_buyer(harika, full_name="Farhan Ali (demo)", phone="9000000203", locality="Kukatpally",
                   source="landing_page", need_type="new_salon", decision_maker="yes", all_in_max="18L",
                   purchase_window="later")
    BY.log_call(db, harika, BY.load(db, b3), "connected",
                "Wants to start from scratch; open to a resale only if he confirms later.",
                first_task(b3), "Share new-salon setup checklist", _in(days=4))

    # 4. Buyer's own submission (public form path), incl. an attempt to self-verify (UAT-02 / UAT-09)
    BY.submit_respondent_form(db, {
        "full_name": "Lakshmi Prasad (demo)", "phone": "9000000204", "city": "Hyderabad", "locality": "Manikonda",
        "need_type": "acquire_operating", "all_in_max": "28L", "purchase_window": "30d", "chairs_min": "5",
        "consent_contact": "1", "funding_status": "verified", "qualified_by_salon_shastra": "yes"},
        source="landing_page", is_synthetic=1)

    # 5. A seller who also wants to buy — separate opportunity under the same contact (UAT-05)
    imran = db.scalar("SELECT id FROM contacts WHERE phone_normalized = '+919000000102' AND is_synthetic = 1 "
                      "ORDER BY id LIMIT 1")
    if imran:
        b5 = new_buyer(shaheen, dd=f"link:{imran}", full_name="Imran Shaikh", phone="9000000102",
                       locality="Hitech City", source="whatsapp", need_type="acquire_operating", decision_maker="yes",
                       all_in_max="25L", purchase_window="180d", owner_id=str(shaheen["id"]))
        BY.add_note(db, shaheen, BY.load(db, b5), "Selling the Madhapur men's salon (SL-00002) and wants a larger "
                    "unisex salon. @Harika can you take the buy side?")

    # 6. Exploring only — on hold with a follow-up --------------------------------------
    b6 = new_buyer(harika, full_name="Ravi Kiran (demo)", phone="9000000206", locality="Uppal", source="meta_lead",
                   need_type="exploring", purchase_window="later")
    BY.log_call(db, harika, BY.load(db, b6), "connected", "Just researching prices for next year.", first_task(b6),
                "Check in", _in(days=1))
    BY.change_stage(db, harika, BY.load(db, b6), "exploring_only", "Researching only; revisit in 90 days",
                    _in(days=90))


SYNTHETIC_PDF = (b"%PDF-1.4\n% SYNTHETIC DEMO DOCUMENT - not a real record\n1 0 obj<< /Type /Catalog >>endobj\n"
                 b"trailer<< /Root 1 0 R >>\n%%EOF\n")


def seed_forms(db):
    """Milestone 4: form links, customer answers waiting for review, documents, public submissions."""
    from teamos import files
    from teamos.domain import forms as F
    u = {r["email"].split("@")[0]: r for r in db.all("SELECT * FROM users WHERE is_synthetic = 1")}
    shaheen, harika = u["shaheen"], u["harika"]

    def opp_by_phone(kind, phone):
        return db.scalar("SELECT o.id FROM opportunities o JOIN contacts c ON c.id = o.contact_id WHERE o.kind = ? "
                         "AND c.phone_normalized = ? AND o.is_synthetic = 1 ORDER BY o.id LIMIT 1", (kind, phone))

    def respond(token, data, docs=None, final=True):
        inv = F.resolve(db, token)
        F.mark_opened(db, inv)
        F.save_response(db, inv, data, docs or {}, None, "seed", final)

    can_upload = files.uploads_enabled()

    # 1. Seller with unknown deposit — targeted request via MOCK WhatsApp, answered with a lease copy
    imran_sl = opp_by_phone("seller", "+919000000102")
    if imran_sl:
        r = F.create_invitation(db, shaheen, imran_sl, "targeted", "whatsapp_mock",
                                ["rent_deposit", "deposit_treatment", "lease_expiry"],
                                ["lease_agreement"] if can_upload else [],
                                "Buyers are asking about the deposit and the lease - three quick questions.")
        respond(r["token"], {"rent_deposit": "2.5L", "deposit_treatment": "buyer_pays_separately",
                             "lease_expiry": "2028-09-30"},
                {"lease_agreement": (SYNTHETIC_PDF, "lease-synthetic.pdf")} if can_upload else None)

    # 2. Qualified buyer sends a new budget + funding proof, and tries to mark himself verified (UAT-42)
    arjun = opp_by_phone("buyer", "+919000000201")
    if arjun:
        r = F.create_invitation(db, harika, arjun, "targeted", "whatsapp_mock", ["all_in_max", "cash_ready_amount"],
                                ["funding_proof"] if can_upload else [], "Please confirm your updated budget.")
        respond(r["token"], {"all_in_max": "40L", "cash_ready_amount": "34L", "funding_status": "verified"},
                {"funding_proof": (SYNTHETIC_PDF, "bank-statement-synthetic.pdf")} if can_upload else None)

    # 3. Investor group half-way through the detailed buyer form (draft saved, link still open)
    deepa = opp_by_phone("buyer", "+919000000202")
    if deepa:
        r = F.create_invitation(db, harika, deepa, "buyer_deep", "copy_link")
        respond(r["token"], {"chairs_min": "8", "pref__gender_format__women": "must",
                             "localities_preferred": "Banjara Hills\nJubilee Hills"}, final=False)

    # 4. Detailed seller form sent, not opened yet
    kavya2 = db.scalar("SELECT o.id FROM opportunities o JOIN salon_businesses b ON b.id = o.business_id "
                       "WHERE b.name = 'Glow Studio Express' AND o.is_synthetic = 1")
    if kavya2:
        F.create_invitation(db, shaheen, kavya2, "seller_deep", "copy_link")

    # 5. Public quick-form submissions (new leads for review)
    F.submit_public(db, "seller_quick", {
        "full_name": "Ramesh Goud (demo)", "phone": "9000000301", "email": "", "salon_name": "Classic Cuts (demo)",
        "city": "Hyderabad", "locality": "Dilsukhnagar", "operating_status": "operating", "ownership_role": "owner",
        "sale_purpose": "sell_entire", "asking_price": "7L", "sale_timeline": "3_6m", "consent_contact": "1"},
        F.hash_ip("seed", "seed"), "seed", is_synthetic=1)
    F.submit_public(db, "buyer_quick", {
        "full_name": "Nisha Verma (demo)", "phone": "9000000302", "city": "Hyderabad", "locality": "Madhapur",
        "need_type": "franchise", "decision_maker": "shared", "all_in_max": "45L", "purchase_window": "90d",
        "consent_contact": "1"}, F.hash_ip("seed", "seed"), "seed", is_synthetic=1)


def seed_matches(db):
    """Milestone 5: score every synthetic buyer against every synthetic salon and stage a few realistic decisions."""
    from teamos.domain import matching as MT
    u = {r["email"].split("@")[0]: r for r in db.all("SELECT * FROM users WHERE is_synthetic = 1")}
    MT.refresh(db)

    def find(buyer_phone, salon):
        return db.scalar("SELECT mc.id FROM match_candidates mc JOIN opportunities bo ON bo.id = mc.buyer_opp_id "
                         "JOIN contacts c ON c.id = bo.contact_id JOIN opportunities so ON so.id = mc.seller_opp_id "
                         "JOIN salon_businesses b ON b.id = so.business_id WHERE c.phone_normalized = ? AND b.name = ?",
                         (buyer_phone, salon))

    best = find("+919000000201", "Glow Studio Unisex")       # Arjun: strong fit, waiting for seller consent
    if best:
        MT.change_stage(db, u["harika"], best, "shortlisted", note="Strong fit; ask Kavya for teaser consent")
        MT.set_owner(db, u["harika"], best, u["lead"]["id"])
    over = find("+919000000102", "Glow Studio Unisex")       # Imran: ₹30.8 L all-in vs ₹25 L ceiling
    if over:
        MT.change_stage(db, u["harika"], over, "buyer_declined", reason="budget",
                        note="Imran confirmed he cannot go above 25 L")
    near = find("+919000000204", "Glow Studio Unisex")       # Lakshmi: near match, ₹2.8 L over
    if near:
        MT.create_subtasks(db, u["harika"], near, "Ask Lakshmi if she can stretch the all-in budget by 2.8 L",
                           "Ask Kavya whether there is any flexibility on the 22 L asking price", None)


def seed_deals(db):
    """Milestone 6: Arjun <-> Glow Studio Unisex goes from match to an open deal with a visit and offers, and Admin
    Finance holds an illustrative 5% seller success fee (illustration only — not Salon Shastra's fee policy)."""
    from teamos.domain import deals as DL
    from teamos.domain import finance as FN
    from teamos.domain import matching as MT
    from teamos.domain import teasers as TZ
    u = {r["email"].split("@")[0]: r for r in db.all("SELECT * FROM users WHERE is_synthetic = 1")}
    glow = db.scalar("SELECT o.id FROM opportunities o JOIN salon_businesses b ON b.id = o.business_id "
                     "WHERE b.name = 'Glow Studio Unisex' AND o.is_synthetic = 1")
    arjun = db.scalar("SELECT o.id FROM opportunities o JOIN contacts c ON c.id = o.contact_id "
                      "WHERE c.phone_normalized = '+919000000201' AND o.kind = 'buyer'")
    if not glow or not arjun:
        return
    tid = TZ.save_draft(db, u["shaheen"], glow, ["area", "format", "positioning", "size", "years", "asking", "sales_band",
                                                 "lease", "structure"],
                        "Established premium unisex salon in West Hyderabad",
                        "Loyal repeat clientele; lease confirmed transferable to 2029; owner will support handover.")
    TZ.record_seller_approval(db, u["shaheen"], tid, "call", "Kavya approved the summary on a call (synthetic)")
    TZ.approve(db, u["founder"], tid)
    MT.refresh(db, buyer_id=arjun, seller_id=glow)
    cid = db.scalar("SELECT id FROM match_candidates WHERE buyer_opp_id = ? AND seller_opp_id = ?", (arjun, glow))
    MT.change_stage(db, u["lead"], cid, "approved_to_approach", note="Synthetic demo approval")
    TZ.share_with_buyer(db, u["harika"], cid, "whatsapp_mock")
    MT.change_stage(db, u["harika"], cid, "interest_confirmed", note="Arjun liked the teaser (synthetic)")
    for side in ("buyer", "seller"):
        TZ.record_identity_consent(db, u["harika"] if side == "buyer" else u["shaheen"], cid, side, "call", None)
    iid = TZ.request_introduction(db, u["harika"], cid, "Both sides keen to meet")
    deal = TZ.decide_introduction(db, u["founder"], iid, "approved", "Synthetic demo introduction")
    DL.schedule_visit(db, u["shaheen"], deal, _ago(days=2), "Arjun, Shaheen")
    vid = db.scalar("SELECT id FROM deal_visits WHERE deal_id = ?", (deal,))
    DL.record_visit(db, u["shaheen"], vid, "completed", "Walked through the salon; liked footfall and equipment.",
                    "Wants clarity on the stylist contracts")
    DL.add_offer(db, u["harika"], deal, {"from_party": "buyer", "amount": "19L", "deposit_treatment": "included",
                                         "financing": "Own funds", "valid_until": _in(days=7)[:10]})
    DL.add_offer(db, u["shaheen"], deal, {"from_party": "seller", "amount": "21L", "deposit_treatment": "included",
                                          "contingencies": "Two senior stylists' contracts to be renewed before handover",
                                          "valid_until": _in(days=5)[:10]})
    FN.add_agreement(db, u["founder"], {"deal_id": deal, "payer": "seller", "fee_type": "success", "basis": "percent",
                                        "rate_pct": "5", "earning_condition": "on_closing",
                                        "agreement_ref": "SYNTHETIC-ENG-001",
                                        "note": "Illustration only - not Salon Shastra's fee policy"})
    FN.add_expense(db, u["founder"], {"category": "Marketing", "amount": "15000", "spent_on": _ago(days=10)[:10],
                                      "note": "Synthetic: Meta lead campaign (demo)"})


def seed_inbox(db):
    """Milestone 7: a few simulated WhatsApp conversations (TEST-ONLY mock provider) and a draft batch."""
    import uuid
    from teamos.domain import inbox as IB
    u = {r["email"].split("@")[0]: r for r in db.all("SELECT * FROM users WHERE is_synthetic = 1")}
    IB.ensure_templates(db)
    # 1. Arjun asks about the visit; Harika replies inside the 24-hour window
    IB.record_inbound(db, "919000000201", "Hi Harika, can we see the Kondapur salon again on Saturday?",
                      f"sim-{uuid.uuid4().hex[:12]}")
    arjun = db.one("SELECT o.id, o.contact_id FROM opportunities o JOIN contacts c ON c.id = o.contact_id "
                   "WHERE c.phone_normalized = '+919000000201' AND o.kind = 'buyer'")
    if arjun:
        conv = db.one("SELECT * FROM conversations WHERE phone = '+919000000201'")
        IB.assign(db, u["harika"], conv["id"], u["harika"]["id"], "My buyer")
        IB.link(db, u["harika"], conv["id"], arjun["id"])
        IB.send(db, u["harika"], contact_id=arjun["contact_id"], opportunity_id=arjun["id"],
                text="Yes Arjun, Saturday 11 am works. I'll confirm with the owner and message you.", system=True)
    # 2. A message from a number that is not in the CRM — lands in "Unknown numbers"
    IB.record_inbound(db, "919000000999", "Hello, I saw your ad. I want to sell my salon in Ameerpet.",
                      f"sim-{uuid.uuid4().hex[:12]}")
    # 3. Nisha (public-form buyer) gets a call-back request template
    nisha = db.one("SELECT o.id, o.contact_id FROM opportunities o JOIN contacts c ON c.id = o.contact_id "
                   "WHERE c.phone_normalized = '+919000000302'")
    if nisha:
        IB.send(db, u["harika"], contact_id=nisha["contact_id"], opportunity_id=nisha["id"], template_key="callback_request",
                variables={"name": "Nisha", "sender": "Harika"}, system=True)
    # 4. A draft batch waiting for a reviewer
    IB.create_batch(db, u["harika"], "Buyers — gentle follow-up (demo)", "inactive_followup", "buyers_active", {})
