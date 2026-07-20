"""Tests for the import pipeline: enrichment, dedup, batches, accounts."""

from __future__ import annotations

from pathlib import Path

import pytest

from kosha import crypto
from kosha.db import Database
from kosha.importer import get_or_create_account, import_file
from kosha.parsers.hdfc_bank import HdfcBankParser

FIXTURE = Path(__file__).parent / "fixtures" / "hdfc_bank_sample.xls"
FAST = crypto.Argon2Params(time_cost=1, memory_cost=8192, parallelism=1)
PW = "correct horse battery"


@pytest.fixture()
def db(tmp_path):
    d = Database(db_file=tmp_path / "kosha.db", salt_file=tmp_path / "kosha.salt")
    d.create(PW, params=FAST)
    yield d
    d.lock()


@pytest.fixture()
def account(db):
    p = HdfcBankParser()
    return get_or_create_account(db, "HDFC Test", p.account_type, p.institution)


def test_get_or_create_account_is_idempotent(db):
    a1 = get_or_create_account(db, "HDFC Test", "bank", "hdfc_bank")
    a2 = get_or_create_account(db, "HDFC Test", "bank", "hdfc_bank")
    assert a1 == a2
    n = db.connection.execute("SELECT count(*) FROM accounts").fetchone()[0]
    assert n == 1


def test_import_inserts_all_rows(db, account):
    res = import_file(db, HdfcBankParser(), FIXTURE, account)
    assert res.parsed == 9
    assert res.inserted == 9
    assert res.skipped_duplicates == 0


def test_reimport_skips_duplicates(db, account):
    import_file(db, HdfcBankParser(), FIXTURE, account)
    res = import_file(db, HdfcBankParser(), FIXTURE, account)
    assert res.inserted == 0
    assert res.skipped_duplicates == 9
    total = db.connection.execute("SELECT count(*) FROM transactions").fetchone()[0]
    assert total == 9


def test_enrichment_written(db, account):
    import_file(db, HdfcBankParser(), FIXTURE, account)
    rows = dict(db.connection.execute(
        "SELECT txn_type, count(*) FROM transactions GROUP BY txn_type"
    ).fetchall())
    assert rows["UPI"] == 3
    assert rows["NEFT"] == 1
    assert rows["SI"] == 1
    assert rows["IMPS"] == 1
    assert rows["ATM"] == 1
    assert rows["CARD"] == 1
    # No transaction should be missing a merchant keyword.
    nulls = db.connection.execute(
        "SELECT count(*) FROM transactions WHERE merchant_keyword IS NULL OR merchant_keyword=''"
    ).fetchone()[0]
    assert nulls == 0


def test_batch_row_count_tracks_inserted(db, account):
    res = import_file(db, HdfcBankParser(), FIXTURE, account)
    batch_count = db.connection.execute(
        "SELECT row_count FROM import_batches WHERE id = ?", (res.batch_id,)
    ).fetchone()[0]
    assert batch_count == 9


def test_two_accounts_do_not_dedup_against_each_other(db):
    p = HdfcBankParser()
    a1 = get_or_create_account(db, "HDFC One", p.account_type, p.institution)
    a2 = get_or_create_account(db, "HDFC Two", p.account_type, p.institution)
    import_file(db, p, FIXTURE, a1)
    res = import_file(db, p, FIXTURE, a2)     # same file, different account
    assert res.inserted == 9                  # account is part of the dedup hash


# --- bulk import (Phase 7) ---------------------------------------------------

CARD_FIXTURE = Path(__file__).parent / "fixtures" / "hdfc_card_sample.xls"


def test_detect_parser_picks_right_one():
    from kosha.importer import detect_parser
    from kosha.parsers.hdfc_bank import HdfcBankParser
    from kosha.parsers.hdfc_card import HdfcCardParser
    assert isinstance(detect_parser(FIXTURE), HdfcBankParser)
    assert isinstance(detect_parser(CARD_FIXTURE), HdfcCardParser)
    assert detect_parser(Path(__file__)) is None      # not a statement


def test_import_paths_bulk_auto_files_by_institution(db):
    from kosha.importer import import_paths
    res = import_paths(db, [FIXTURE, CARD_FIXTURE])
    assert len(res.imported) == 2
    assert res.total_inserted == 9 + 8
    assert not res.unrecognized and not res.failed
    # One account per institution, auto-named.
    names = {r[0] for r in db.connection.execute("SELECT name FROM accounts").fetchall()}
    assert names == {"HDFC Bank", "HDFC Card"}


def test_import_paths_reports_unrecognized(db, tmp_path):
    from kosha.importer import import_paths
    junk = tmp_path / "notes.txt"; junk.write_text("hi")
    res = import_paths(db, [FIXTURE, junk])
    assert res.total_inserted == 9
    assert res.unrecognized == ["notes.txt"]
