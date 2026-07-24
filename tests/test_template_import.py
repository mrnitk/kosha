"""Tests for the standard-template importer."""

from __future__ import annotations

from datetime import date

import pytest

from kosha import crypto, importer, template_import as ti
from kosha.db import Database

FAST = crypto.Argon2Params(time_cost=1, memory_cost=8192, parallelism=1)
PW = "correct horse battery"


@pytest.fixture()
def db(tmp_path):
    d = Database(db_file=tmp_path / "kosha.db", salt_file=tmp_path / "kosha.salt")
    d.create(PW, params=FAST)
    yield d
    d.lock()


def _csv(path, text):
    path.write_text(text, encoding="utf-8")
    return path


# --- value parsing -----------------------------------------------------------

@pytest.mark.parametrize("cell,expected", [
    ("1,234.50", 1234.50), ("₹1,23,456.78", 123456.78), ("(500)", -500.0), ("", 0.0),
])
def test_parse_amount(cell, expected):
    assert ti.parse_amount(cell) == expected


@pytest.mark.parametrize("cell,expected", [
    ("05/04/2026", date(2026, 4, 5)), ("2026-04-05", date(2026, 4, 5)),
    ("5 Apr 2026", date(2026, 4, 5)), ("nope", None),
])
def test_parse_date(cell, expected):
    assert ti.parse_date(cell) == expected


# --- reading the template ----------------------------------------------------

def test_read_template_basic(tmp_path):
    p = _csv(tmp_path / "t.csv", (
        "Date,Transaction Remarks,Debit,Credit,Source,Account Type\n"
        "05/04/2026,UPI SWIGGY,300,,HDFC Bank,Bank\n"
        "06/04/2026,Salary,,60000,HDFC Bank,Bank\n"
        "Closing balance,,,,,\n"                       # no date -> skipped
    ))
    recs, problems = ti.read_template(p)
    assert len(recs) == 2 and problems == []
    (raw0, src0, type0) = recs[0]
    assert raw0.direction == "debit" and raw0.amount == 300 and raw0.raw_description == "UPI SWIGGY"
    assert src0 == "HDFC Bank" and type0 == "bank"
    assert recs[1][0].direction == "credit"


def test_header_aliases_and_metadata_rows(tmp_path):
    # Title rows above the header, and alternative header spellings.
    p = _csv(tmp_path / "t.csv", (
        "My Bank Statement,,,\n"
        ",,,\n"
        "Txn Date,Narration,Withdrawal,Deposit\n"
        "05/04/2026,SWIGGY,300,\n"
    ))
    recs, _ = ti.read_template(p)
    assert len(recs) == 1 and recs[0][0].amount == 300
    assert recs[0][1] == "Imported"                    # no Source column -> default


def test_account_type_inferred_from_source(tmp_path):
    p = _csv(tmp_path / "t.csv", (
        "Date,Transaction Remarks,Debit,Credit,Source\n"
        "05/04/2026,AMAZON,1000,,HDFC Credit Card\n"
    ))
    _raw, _src, atype = ti.read_template(p)[0][0]
    assert atype == "credit_card"


def test_missing_headers_raises(tmp_path):
    p = _csv(tmp_path / "t.csv", "foo,bar,baz\n1,2,3\n")
    with pytest.raises(ti.TemplateError):
        ti.read_template(p)


def test_row_with_both_debit_and_credit_is_rejected(tmp_path):
    p = _csv(tmp_path / "t.csv", (
        "Date,Transaction Remarks,Debit,Credit,Source\n"
        "05/04/2026,BOTH,8168,4580,HDFC\n"          # both -> skipped + reported
        "06/04/2026,OK,300,,HDFC\n"
    ))
    recs, problems = ti.read_template(p)
    assert len(recs) == 1 and recs[0][0].raw_description == "OK"
    assert len(problems) == 1 and "both" in problems[0].lower() and "row 2" in problems[0]


def test_negative_amount_rejected(tmp_path):
    p = _csv(tmp_path / "t.csv", (
        "Date,Transaction Remarks,Debit,Credit,Source\n"
        "05/04/2026,NEG,-500,,HDFC\n"
        "06/04/2026,OK,,700,HDFC\n"
    ))
    recs, problems = ti.read_template(p)
    assert len(recs) == 1 and recs[0][0].raw_description == "OK"
    assert len(problems) == 1 and "negative" in problems[0].lower()


def test_import_template_reports_problems(tmp_path, db):
    p = _csv(tmp_path / "t.csv", (
        "Date,Transaction Remarks,Debit,Credit,Source\n"
        "05/04/2026,BOTH,100,200,HDFC\n"
        "06/04/2026,GOOD,300,,HDFC\n"
    ))
    result = importer.import_template(db, p)
    assert result.total_inserted == 1
    assert result.has_problems and len(result.problems) == 1
    assert "both" in result.summary().lower()


# --- import end to end -------------------------------------------------------

def test_import_template_multi_source(tmp_path, db):
    p = _csv(tmp_path / "t.csv", (
        "Date,Transaction Remarks,Debit,Credit,Source,Account Type\n"
        "05/04/2026,SWIGGY,300,,HDFC Bank,Bank\n"
        "06/04/2026,Salary,,60000,HDFC Bank,Bank\n"
        "07/04/2026,AMAZON,1000,,Axis Card,Credit Card\n"
    ))
    result = importer.import_template(db, p)
    assert result.total_inserted == 3
    sources = {s for s, _r in result.per_source}
    assert sources == {"HDFC Bank", "Axis Card"}
    # Source is stored per account and visible on the resolved view.
    got = dict(db.connection.execute(
        "SELECT raw_description, account_name FROM v_transactions_resolved"))
    assert got["AMAZON"] == "Axis Card" and got["SWIGGY"] == "HDFC Bank"


def test_import_template_dedupes(tmp_path, db):
    p = _csv(tmp_path / "t.csv", (
        "Date,Transaction Remarks,Debit,Credit,Source\n"
        "05/04/2026,SWIGGY,300,,HDFC Bank\n"
    ))
    first = importer.import_template(db, p)
    second = importer.import_template(db, p)
    assert first.total_inserted == 1 and second.total_inserted == 0 and second.total_skipped == 1


# --- blank template generation round-trips -----------------------------------

def test_write_template_is_readable(tmp_path, db):
    out = tmp_path / "blank.xlsx"
    ti.write_template(out)
    assert out.exists()
    # Blank template has headers but no data rows -> zero transactions, no error.
    assert ti.read_template(out)[0] == []
    import openpyxl
    wb = openpyxl.load_workbook(out)
    assert wb.sheetnames[0] == "Transactions"
    assert [c.value for c in wb["Transactions"][1]] == ti.TEMPLATE_HEADERS
