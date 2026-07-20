"""Tests for the HDFC credit-card parser and card enrichment."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from kosha import crypto, features
from kosha.db import Database
from kosha.importer import get_or_create_account, import_file
from kosha.parsers import get_parser
from kosha.parsers.hdfc_bank import HdfcBankParser
from kosha.parsers.hdfc_card import HdfcCardParser

CARD_FIXTURE = Path(__file__).parent / "fixtures" / "hdfc_card_sample.xls"
BANK_FIXTURE = Path(__file__).parent / "fixtures" / "hdfc_bank_sample.xls"
FAST = crypto.Argon2Params(time_cost=1, memory_cost=8192, parallelism=1)


@pytest.fixture()
def parser():
    return HdfcCardParser()


def test_registry_has_card_parser():
    assert isinstance(get_parser("hdfc_card"), HdfcCardParser)


def test_can_parse_card_and_rejects_bank(parser):
    assert parser.can_parse(CARD_FIXTURE) is True
    # Cross-detection: card parser must not claim a bank statement, and the
    # bank parser must not claim the card statement.
    assert parser.can_parse(BANK_FIXTURE) is False
    assert HdfcBankParser().can_parse(CARD_FIXTURE) is False


def test_parse_counts_and_skips_summary(parser):
    txns = list(parser.parse(CARD_FIXTURE))
    assert len(txns) == 8                       # summary rows excluded


def test_parse_direction_and_dates(parser):
    txns = list(parser.parse(CARD_FIXTURE))
    first = txns[0]
    assert first.txn_date == date(2026, 3, 14)
    assert first.amount == 1346.00
    assert first.direction == "debit"           # a purchase
    credits = [t for t in txns if t.direction == "credit"]
    assert len(credits) == 2                     # the 'Cr' refund + payment
    assert any("PAYMENT RECEIVED" in t.raw_description for t in credits)


def test_international_row_parsed(parser):
    txns = list(parser.parse(CARD_FIXTURE))
    assert any("AMAZON WEB SERVICES" in t.raw_description for t in txns)


def test_card_import_classifies_as_card_and_extracts_merchants(tmp_path):
    db = Database(db_file=tmp_path / "k.db", salt_file=tmp_path / "k.salt")
    db.create("pw", params=FAST)
    p = HdfcCardParser()
    acct = get_or_create_account(db, "Card", p.account_type, p.institution)
    res = import_file(db, p, CARD_FIXTURE, acct)
    assert res.inserted == 8

    con = db.connection
    # Every card row is classified CARD regardless of description prefix.
    types = dict(con.execute("SELECT txn_type, count(*) FROM transactions GROUP BY txn_type").fetchall())
    assert types == {"CARD": 8}
    # Gateway prefixes stripped: PYU*Swiggy -> SWIGGY ...
    kws = [r[0] for r in con.execute("SELECT DISTINCT merchant_keyword FROM transactions")]
    assert any(k.startswith("SWIGGY") for k in kws)
    assert not any("PYU*" in k or "WWW" in k.split() for k in kws)
    assert con.execute(
        "SELECT count(*) FROM transactions WHERE merchant_keyword IS NULL OR merchant_keyword=''"
    ).fetchone()[0] == 0
    db.lock()


def test_card_gateway_prefix_stripping():
    assert features._keyword_from_tokens("PYU*Swiggy Food Bangalore") == "SWIGGY FOOD BANGALORE"
    assert features._keyword_from_tokens("WWW SWIGGY IN BANGALORE") == "SWIGGY IN BANGALORE"
