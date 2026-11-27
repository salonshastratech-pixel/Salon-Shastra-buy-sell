"""Questions are asked only when they make sense: a sale price only from an owner who wants to sell, takeover
questions only from buyers for whom a takeover is on the table. Nothing already recorded is ever erased."""
from conftest import user

from teamos.domain import buyer_fields as BF
from teamos.domain import buyers as B
from teamos.domain import collect as C
from teamos.domain import seller_fields as SF
from teamos.domain import sellers as S


def _owner(db, purpose, price="20L", phone="9300700001"):
    admin = user(db, "admin@t.test")
    data = {"full_name": "Anji", "phone": phone, "salon_name": f"Salon {phone}", "city": "Hyderabad",
            "locality": "Kondapur", "source": "manual_call", "consent_contact": "1", "owner_id": str(admin["id"]),
            "sale_purpose": purpose, "asking_price": price}
    return S.create_lead(db, admin, data)["opportunity_id"]


def test_rules_read_as_intended():
    sell = {"sale_purpose": "sell_entire"}
    help_ = {"sale_purpose": "seek_help"}
    for key in ("asking_price", "sale_timeline", "negotiable", "sale_reason", "signatory_name"):
        fd = SF.BY_KEY[key]
        assert SF.is_applicable(fd, sell) and not SF.is_applicable(fd, help_) and not SF.is_applicable(fd, {})
    # 'lowest price' needs 'negotiable = yes', which itself needs a sale
    low = SF.BY_KEY["negotiable_min_price"]
    assert SF.is_applicable(low, {"sale_purpose": "partnership", "negotiable": "yes"})
    assert not SF.is_applicable(low, {"sale_purpose": "seek_help", "negotiable": "yes"})
    assert SF.is_applicable(SF.BY_KEY["chairs"], help_)                     # salon facts are always asked
    # buyers: takeover questions only when a running salon is on the table
    assert not SF.is_applicable(BF.BY_KEY["budget_min"], {"need_type": "new_salon"}, BF.BY_KEY)
    assert SF.is_applicable(BF.BY_KEY["budget_min"], {"need_type": "new_salon", "consider_takeover": "yes"}, BF.BY_KEY)
    assert SF.is_applicable(BF.BY_KEY["budget_min"], {"need_type": "acquire_operating"}, BF.BY_KEY)
    assert not SF.is_applicable(BF.BY_KEY["consider_takeover"], {"need_type": "acquire_operating"}, BF.BY_KEY)
    assert SF.is_applicable(BF.BY_KEY["all_in_max"], {"need_type": "new_salon"}, BF.BY_KEY)


def test_an_owner_seeking_help_is_not_given_a_sale_price(db, login):
    oid = _owner(db, "seek_help")
    lst = S.load_listing(db, S.load(db, oid)["listing_id"])
    assert lst["sale_purpose"] == "seek_help" and lst["asking_price"] is None        # price not stored
    # the profile hides the price box until they want to sell; "still to collect" never asks for it
    page = login("admin@t.test").get(f"/sellers/{oid}?tab=intake").data.decode()
    assert 'data-show-if="sale_purpose=franchise_transfer|partnership|sell_entire"' in page
    gaps = [i["key"] for i in C.gaps(db, S.load(db, oid))["items"]]
    assert "asking_price" not in gaps and "sale_timeline" not in gaps
    # once they decide to sell, the price is taken
    S.update_fields(db, user(db, "admin@t.test"), S.load(db, oid), {"sale_purpose": "sell_entire", "asking_price": "18L"},
                    "quick")
    assert S.load_listing(db, S.load(db, oid)["listing_id"])["asking_price"] == 18_00_000
    # changing back to 'help' does not erase what was recorded
    S.update_fields(db, user(db, "admin@t.test"), S.load(db, oid), {"sale_purpose": "seek_help", "asking_price": "18L"},
                    "quick")
    assert S.load_listing(db, S.load(db, oid)["listing_id"])["asking_price"] == 18_00_000


def test_a_seller_and_an_unknown_purpose_keep_their_price(db):
    sell = _owner(db, "sell_entire", phone="9300700002")
    assert S.load_listing(db, S.load(db, sell)["listing_id"])["asking_price"] == 20_00_000
    unknown = _owner(db, "", phone="9300700003")       # e.g. a sheet import: nothing dropped while purpose is unknown
    assert S.load_listing(db, S.load(db, unknown)["listing_id"])["asking_price"] == 20_00_000


def test_new_salon_buyer_is_not_asked_takeover_questions(db, login):
    admin = user(db, "admin@t.test")
    bid = B.create_lead(db, admin, {"full_name": "Sita", "phone": "9300700011", "city": "Hyderabad",
                                    "source": "meta_lead", "consent_contact": "1", "owner_id": str(admin["id"]),
                                    "need_type": "new_salon"})["opportunity_id"]
    opp = B.load(db, bid)
    B.update_fields(db, admin, opp, {"budget_min": "10L", "budget_max": "20L", "all_in_max": "30L"}, "budget")
    m = B.load_mandate(db, opp["mandate_id"])
    assert m["budget_min"] is None and m["budget_max"] is None and m["all_in_max"] == 30_00_000
    B.update_fields(db, admin, B.load(db, bid), {"consider_takeover": "yes"}, "intent")
    B.update_fields(db, admin, B.load(db, bid), {"budget_max": "20L"}, "budget")
    assert B.load_mandate(db, opp["mandate_id"])["budget_max"] == 20_00_000


def test_customer_form_and_new_lead_form_show_price_only_for_sellers(db, login):
    c = login("admin@t.test")
    page = c.get("/sellers/new").data.decode()
    assert 'data-show-if="sale_purpose=franchise_transfer|partnership|sell_entire"' in page
    js = c.get("/static/app.js").data.decode()
    assert "split(';')" in js and "split('|')" in js


def test_more_rules_closed_salons_and_cash():
    sell_open = {"sale_purpose": "sell_entire", "operating_status": "operating"}
    sell_closed = {"sale_purpose": "sell_entire", "operating_status": "non_operating"}
    help_open = {"sale_purpose": "seek_help", "operating_status": "operating"}
    sales = SF.BY_KEY["avg_monthly_sales"]
    assert SF.is_applicable(sales, sell_open) and SF.is_applicable(sales, help_open)
    assert SF.is_applicable(sales, {"sale_purpose": "sell_entire"})            # status not given yet: still asked
    assert not SF.is_applicable(sales, sell_closed)                            # a closed salon has no monthly sales
    stay = SF.BY_KEY["staff_continuity"]                                       # needs a sale AND a running salon
    assert SF.is_applicable(stay, sell_open) and not SF.is_applicable(stay, sell_closed)
    assert not SF.is_applicable(stay, help_open)
    for k in ("deposit_treatment", "lease_transfer_status"):
        assert SF.is_applicable(SF.BY_KEY[k], sell_open) and not SF.is_applicable(SF.BY_KEY[k], help_open)
    assert SF.is_applicable(SF.BY_KEY["monthly_rent"], help_open)               # rent matters for help too
    amt = BF.BY_KEY["cash_ready_amount"]
    assert SF.is_applicable(amt, {"cash_ready": "now"}, BF.BY_KEY)
    assert not SF.is_applicable(amt, {"cash_ready": "planned"}, BF.BY_KEY)


def test_buyer_profile_page_carries_the_rules(db, login):
    admin = user(db, "admin@t.test")
    bid = B.create_lead(db, admin, {"full_name": "Sita", "phone": "9300700021", "city": "Hyderabad",
                                    "source": "meta_lead", "consent_contact": "1",
                                    "owner_id": str(admin["id"])})["opportunity_id"]
    page = login("admin@t.test").get(f"/buyers/{bid}?tab=requirements").data.decode()
    assert 'data-show-if="need_type=new_salon"' in page and 'data-show-if="cash_ready=now|within_30d"' in page
    sid = _owner(db, "sell_entire", phone="9300700022")
    page = login("admin@t.test").get(f"/sellers/{sid}?tab=intake").data.decode()
    assert 'data-show-if="operating_status=|operating"' in page                       # trading figures
    assert 'data-also-if="operating_status=|operating"' in page                       # staff staying after sale


def test_a_value_is_kept_while_its_deciding_question_is_blank(db):
    admin = user(db, "admin@t.test")
    bid = B.create_lead(db, admin, {"full_name": "Sita", "phone": "9300700031", "city": "Hyderabad",
                                    "source": "meta_lead", "consent_contact": "1", "owner_id": str(admin["id"]),
                                    "need_type": "acquire_operating"})["opportunity_id"]
    opp = B.load(db, bid)
    B.update_fields(db, admin, opp, {"cash_ready_amount": "15L"}, "funding")       # 'when available' not answered
    assert B.load_mandate(db, opp["mandate_id"])["cash_ready_amount"] == 15_00_000
    kept = SF.drop_not_applicable({"cash_ready_amount": (1, None)}, {"cash_ready": "planned"}, BF.BY_KEY)
    assert kept == {}                                                                 # answered 'planned': not asked
