"""Tests for multi-tags (comma-separated) and cash-transaction identification."""

from __future__ import annotations

import pytest

from kosha import analytics, categorization as cat, crypto, importer, template_import as ti
from kosha.db import Database

FAST = crypto.Argon2Params(time_cost=1, memory_cost=8192, parallelism=1)
PW = "correct horse battery"


@pytest.fixture()
def db(tmp_path):
    d = Database(db_file=tmp_path / "kosha.db", salt_file=tmp_path / "kosha.salt")
    d.create(PW, params=FAST)
    con = d.connection
    con.execute("INSERT INTO accounts(id,name,account_type,institution) VALUES (1,'A','bank','hdfc_bank')")
    con.execute("INSERT INTO transactions(id,txn_date,raw_description,amount,direction,account_id,merchant_keyword,dedup_hash) "
                "VALUES (1,'2026-04-01','D SWIGGY',300,'debit',1,'SWIGGY','h1')")
    con.commit()
    yield d
    d.lock()


# --- multi-tags --------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("a, b, c", ["a", "b", "c"]),
    ("a; b, c", ["a", "b", "c"]),
    ("  x ,, y ", ["x", "y"]),
    ("dup, DUP, dup", ["dup"]),        # case-insensitive dedupe, first casing kept
    ("", []),
    (None, []),
])
def test_split_tags(text, expected):
    assert cat.split_tags(text) == expected


def test_normalize_tags():
    assert cat.normalize_tags("trip-goa,  reimbursable , trip-goa") == "trip-goa, reimbursable"
    assert cat.normalize_tags("  ") is None


def test_rule_stores_multiple_tags(db):
    cat.add_rule(db, "SWIGGY", "Expense", "Food", tag="reimbursable, trip-goa")
    eff = db.connection.execute(
        "SELECT effective_tag FROM v_transactions_resolved WHERE merchant_keyword='SWIGGY'").fetchone()[0]
    assert eff == "reimbursable, trip-goa"


def test_txn_override_multiple_tags(db):
    cat.set_transaction_overrides(db, [1], tag="work, urgent")
    eff = db.connection.execute(
        "SELECT effective_tag FROM v_transactions_resolved WHERE id=1").fetchone()[0]
    assert eff == "work, urgent"


def test_distinct_tags_splits_individual(db):
    cat.add_rule(db, "SWIGGY", "Expense", "Food", tag="reimbursable, trip-goa")
    cat.set_transaction_overrides(db, [1], tag="trip-goa, personal")
    # Individual tags across rules + overrides, deduped.
    assert cat.distinct_tags(db) == ["personal", "reimbursable", "trip-goa"]


# --- tag filter + tag visual --------------------------------------------------

@pytest.fixture()
def tagged(db):
    """Three expense txns with overlapping tags."""
    con = db.connection
    for tid, kw, amt in [(11, "A", 100.0), (12, "B", 200.0), (13, "C", 300.0)]:
        con.execute(
            "INSERT INTO transactions(id,txn_date,raw_description,amount,direction,account_id,merchant_keyword,dedup_hash) "
            "VALUES (?,?,?,?, 'debit',1,?,?)", (tid, "2026-04-05", f"D {kw}", amt, kw, f"t{tid}"))
    con.commit()
    cat.set_transaction_overrides(db, [11], tag="trip-goa, reimbursable")
    cat.set_transaction_overrides(db, [12], tag="trip-goa")
    cat.set_transaction_overrides(db, [13], tag="personal")
    return db


def test_filter_by_single_tag(tagged):
    rows = analytics.transactions(tagged, analytics.Filter(tags=("trip-goa",)))
    descs = {r[1] for r in rows}
    assert descs == {"D A", "D B"}          # both have trip-goa


def test_filter_by_multiple_tags_is_or(tagged):
    rows = analytics.transactions(tagged, analytics.Filter(tags=("reimbursable", "personal")))
    assert {r[1] for r in rows} == {"D A", "D C"}


def test_tag_filter_matches_on_boundaries(tagged):
    # 'goa' must not match 'trip-goa' (whole-tag match on comma boundaries).
    assert analytics.transactions(tagged, analytics.Filter(tags=("goa",))) == []


def test_spend_by_period_tag_explodes(tagged):
    rows = analytics.spend_by_period_tag(tagged, analytics.Filter(), "month")
    got = {(p, t): v for p, t, v in rows}
    assert got[("2026-04", "trip-goa")] == 300.0     # 100 + 200 (overlap)
    assert got[("2026-04", "reimbursable")] == 100.0
    assert got[("2026-04", "personal")] == 300.0


def test_tag_totals_and_distinct(tagged):
    totals = {t: v for t, v, _n in analytics.tag_totals(tagged, analytics.Filter())}
    assert totals["trip-goa"] == 300.0 and totals["personal"] == 300.0
    assert analytics.distinct_tags(tagged) == ["personal", "reimbursable", "trip-goa"]


def test_untagged_expense_bucketed(db):
    # The seeded SWIGGY txn (id 1) has no tag.
    rows = analytics.spend_by_period_tag(db, analytics.Filter(), "month")
    assert any(t == "Untagged" for _p, t, _v in rows)


def test_search_matches_tag_category_note_source(tagged):
    # Keep the existing tag while adding a note (overrides are set as a whole).
    cat.set_transaction_overrides(
        tagged, [11], tag="trip-goa, reimbursable", note="dinner with team", set_note=True)
    # tag
    assert {r[1] for r in analytics.transactions(tagged, analytics.Filter(search="reimbursable"))} == {"D A"}
    # note (not filterable any other way)
    assert {r[1] for r in analytics.transactions(tagged, analytics.Filter(search="dinner"))} == {"D A"}
    # category
    assert len(analytics.transactions(tagged, analytics.Filter(search="expense"))) >= 3


# --- cash identification ------------------------------------------------------

@pytest.mark.parametrize("type_text,source,expected", [
    ("Cash", "", "cash"),
    ("", "Petty Cash", "cash"),
    ("Credit Card", "", "credit_card"),
    ("", "HDFC Card", "credit_card"),
    ("Bank", "SBI", "bank"),
    ("", "", "bank"),
])
def test_account_type_detects_cash(type_text, source, expected):
    assert ti._account_type(type_text, source) == expected


def test_import_template_cash_source(tmp_path, db):
    p = tmp_path / "cash.csv"
    p.write_text(
        "Date,Transaction Remarks,Debit,Credit,Source,Account Type\n"
        "05/04/2026,Auto rickshaw,80,,Cash,Cash\n"
        "06/04/2026,Vegetables,120,,Cash,\n",       # type inferred from Source
        encoding="utf-8")
    result = importer.import_template(db, p)
    assert result.total_inserted == 2
    acc = db.connection.execute(
        "SELECT account_type FROM accounts WHERE name='Cash'").fetchone()
    assert acc[0] == "cash"
    # Cash transactions are identifiable by their source on the resolved view.
    src = db.connection.execute(
        "SELECT DISTINCT account_name, account_type FROM v_transactions_resolved "
        "WHERE raw_description='Auto rickshaw'").fetchone()
    assert src == ("Cash", "cash")
