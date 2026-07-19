"""Tests for txn_type / merchant_keyword feature engineering."""

from __future__ import annotations

from datetime import date

import pytest

from kosha import features


@pytest.mark.parametrize("narration,expected", [
    ("UPI-SWIGGY LIMITED-SWIGGY@YBL-YESB0YBLUPI-1-PAID VIA CRED", features.UPI),
    ("UPILITE-DORMANT-LRNXX0364456125-31/03", features.UPI),
    ("NEFT CR-YESB0000001-ACME BROKING LTD-REF", features.NEFT),
    ("IMPS-410512345678-RAHUL VERMA-HDFC0000123-X", features.IMPS),
    ("ACH D- SOME MUTUAL FUND-0000ABCD1234", features.SI),
    ("NWD-ATM CASH-DELHI-410599", features.ATM),
    ("POS 552260XXXXXX1234 AMAZON RETAIL", features.CARD),
    ("IB BILLPAY DR-HDFCWI-552260XXXXXX1234", features.OTHER),
    ("SOMETHING WEIRD 123", features.OTHER),
])
def test_derive_txn_type(narration, expected):
    assert features.derive_txn_type(narration) == expected


@pytest.mark.parametrize("narration,ttype,expected", [
    ("UPI-SWIGGY LIMITED-SWIGGY@YBL-YESB0YBLUPI-1-PAID VIA CRED", features.UPI, "SWIGGY LIMITED"),
    ("NEFT CR-YESB0000001-ACME BROKING LTD-SETTLEMENT-X", features.NEFT, "ACME BROKING LTD"),
    ("ACH D- SOME MUTUAL FUND-0000ABCD1234", features.SI, "SOME MUTUAL FUND"),
    ("IMPS-410512345678-RAHUL VERMA-HDFC0000123-X", features.IMPS, "RAHUL VERMA"),
    ("IB BILLPAY DR-HDFCWI-552260XXXXXX1234", features.OTHER, "HDFCWI"),
])
def test_derive_merchant_keyword(narration, ttype, expected):
    assert features.derive_merchant_keyword(narration, ttype) == expected


def test_merchant_keyword_skips_noise_and_refs():
    # First segment is the noise remark; extraction should walk to a real name.
    n = "UPI-PAID VIA CRED-JOHN@OKAXIS-UTIB0000001-99999999-JOHN DOE"
    kw = features.derive_merchant_keyword(n, features.UPI)
    assert kw not in (None, "", "PAID VIA CRED")
    assert "@" not in kw


def test_dedup_hash_is_stable_and_whitespace_insensitive():
    d = date(2026, 4, 1)
    h1 = features.dedup_hash(d, 275.0, "debit", "UPI-SWIGGY  LIMITED", 1)
    h2 = features.dedup_hash(d, 275.0, "debit", "UPI-SWIGGY LIMITED", 1)   # collapsed spaces
    assert h1 == h2


def test_dedup_hash_varies_by_account_amount_direction():
    d = date(2026, 4, 1)
    base = features.dedup_hash(d, 275.0, "debit", "X", 1)
    assert base != features.dedup_hash(d, 275.0, "debit", "X", 2)     # account
    assert base != features.dedup_hash(d, 276.0, "debit", "X", 1)     # amount
    assert base != features.dedup_hash(d, 275.0, "credit", "X", 1)    # direction
