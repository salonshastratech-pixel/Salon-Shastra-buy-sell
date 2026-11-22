"""Synthetic demonstration data. Every record is flagged is_synthetic=1 and shown with a badge.

Names, phone numbers (+91 90000 0xxxx range) and businesses are invented. Not for production.
"""
import os
from datetime import timedelta

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
        print("Demo data already present — skipping.")
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
    db.execute("UPDATE tasks SET is_synthetic = 1")
    print(f"Synthetic demo data created. Users (password: {DEMO_PASSWORD}):")
    for email, name, role, _ in USERS:
        print(f"  {email:40s} {role}")
