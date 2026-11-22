"""Pure-function tests: phone normalisation, INR parsing, all-in cost (PRD §6.5, §8.7 example 1)."""
import pytest

from teamos.domain.common import format_inr, normalize_indian_mobile, parse_inr
from teamos.domain.sellers import all_in_cost


@pytest.mark.parametrize("raw", ["9876543210", "+91 98765 43210", "098765-43210", "91 9876543210", "0091 9876543210"])
def test_phone_variants_normalise_to_same_e164(raw):
    assert normalize_indian_mobile(raw) == ("+919876543210", None)


@pytest.mark.parametrize("raw", ["", "12345", "5876543210", "98765432101"])
def test_invalid_phone_rejected(raw):
    phone, err = normalize_indian_mobile(raw)
    assert phone is None and err


def test_inr_parsing_and_formatting():
    assert parse_inr("20L") == 2_000_000
    assert parse_inr("1.2 cr") == 12_000_000
    assert parse_inr("20,00,000") == 2_000_000
    assert parse_inr("") is None
    assert format_inr(2_000_000) == "₹20 L"
    assert format_inr(2_000_000, short=False) == "₹20,00,000"
    assert format_inr(None) == "Not provided"
    with pytest.raises(ValueError):
        parse_inr("twenty")


def _prov(**states):
    return {k: {"answer_state": v} for k, v in states.items()}


def test_uat10_example1_deposit_separately_payable_is_added():
    listing = {"asking_price": 2_000_000, "deposit_treatment": "buyer_pays_separately", "rent_deposit": 500_000,
               "stock_value_separate": None, "business_model": "independent"}
    r = all_in_cost(listing, _prov(stock_value_separate="not_applicable"))
    assert r["known_total"] == 2_500_000 and r["complete"]


def test_deposit_included_is_not_double_counted():
    listing = {"asking_price": 2_000_000, "deposit_treatment": "included_in_asking", "rent_deposit": 500_000,
               "stock_value_separate": None, "business_model": "independent"}
    r = all_in_cost(listing, _prov(stock_value_separate="not_applicable"))
    assert r["known_total"] == 2_000_000 and r["complete"]


def test_unknown_deposit_keeps_total_incomplete_not_zero():
    listing = {"asking_price": 2_000_000, "deposit_treatment": None, "rent_deposit": None,
               "stock_value_separate": None, "business_model": "independent"}
    r = all_in_cost(listing, _prov(stock_value_separate="not_applicable", rent_deposit="unknown"))
    assert not r["complete"]
    assert "Deposit treatment" in r["missing"]
    assert r["known_total"] == 2_000_000  # minimum known, flagged incomplete


def test_franchise_fee_required_for_franchise():
    listing = {"asking_price": 3_500_000, "deposit_treatment": "not_applicable", "stock_value_separate": 0,
               "business_model": "franchise", "franchise_transfer_fee": None}
    r = all_in_cost(listing, _prov(stock_value_separate="value"))
    assert "Franchise transfer fee" in r["missing"]
