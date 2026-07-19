"""Tests for the HDFC bank statement parser against a synthetic fixture."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from kosha.parsers import get_parser
from kosha.parsers.hdfc_bank import HdfcBankParser

FIXTURE = Path(__file__).parent / "fixtures" / "hdfc_bank_sample.xls"


@pytest.fixture()
def parser():
    return HdfcBankParser()


def test_registry_returns_hdfc():
    assert isinstance(get_parser("hdfc_bank"), HdfcBankParser)


def test_can_parse_accepts_fixture(parser):
    assert parser.can_parse(FIXTURE) is True


def test_can_parse_rejects_non_xls(parser, tmp_path):
    other = tmp_path / "note.txt"
    other.write_text("hello")
    assert parser.can_parse(other) is False


def test_parse_row_count_and_shape(parser):
    txns = list(parser.parse(FIXTURE))
    assert len(txns) == 9                     # every fixture line has an amount
    first = txns[0]
    assert first.txn_date == date(2026, 4, 1)
    assert first.amount == 275.00
    assert first.direction == "debit"
    assert first.raw_description.startswith("UPI-SWIGGY LIMITED")


def test_parse_detects_credit(parser):
    txns = list(parser.parse(FIXTURE))
    credits = [t for t in txns if t.direction == "credit"]
    assert len(credits) == 1
    assert credits[0].amount == 15000.00      # the NEFT CR line


def test_parse_raw_description_is_verbatim(parser):
    txns = list(parser.parse(FIXTURE))
    # No enrichment or trimming beyond surrounding whitespace.
    assert any("SWIGGY@YBL" in t.raw_description for t in txns)
