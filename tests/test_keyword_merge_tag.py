"""Tests for keyword merging (grouping) and the single Tag field (v9)."""

from __future__ import annotations

import pytest

from kosha import categorization as cat, crypto, importer
from kosha.db import Database
from kosha.parsers.base import RawTransaction
from datetime import date

FAST = crypto.Argon2Params(time_cost=1, memory_cost=8192, parallelism=1)
PW = "correct horse battery"


@pytest.fixture()
def db(tmp_path):
    d = Database(db_file=tmp_path / "kosha.db", salt_file=tmp_path / "kosha.salt")
    d.create(PW, params=FAST)
    con = d.connection
    con.execute("INSERT INTO accounts(id,name,account_type,institution) VALUES (1,'A','bank','hdfc_bank')")
    rows = [
        (1, "NEST", 100.0, "debit"),
        (2, "A S", 200.0, "debit"),
        (3, "A S NEST", 300.0, "debit"),
        (4, "SWIGGY", 150.0, "debit"),
        (5, "SWIGGY LIMITED", 250.0, "debit"),
        (6, "SWIGGY INSTAMART", 350.0, "debit"),
    ]
    for tid, kw, amt, d_ in rows:
        con.execute(
            "INSERT INTO transactions(id,txn_date,raw_description,amount,direction,account_id,merchant_keyword,dedup_hash) "
            "VALUES (?,?,?,?,?,1,?,?)", (tid, "2026-04-01", f"D {kw}", amt, d_, kw, f"h{tid}"))
    con.commit()
    yield d
    d.lock()


def _kw_of(db, tid):
    return db.connection.execute("SELECT merchant_keyword FROM transactions WHERE id=?", (tid,)).fetchone()[0]


# --- merging -----------------------------------------------------------------

def test_merge_folds_variants_to_canonical(db):
    moved = cat.merge_keywords(db, ["NEST", "A S", "A S NEST"], "A S NEST")
    assert moved == 2                       # NEST + A S re-pointed (A S NEST already canonical)
    assert _kw_of(db, 1) == "A S NEST" and _kw_of(db, 2) == "A S NEST" and _kw_of(db, 3) == "A S NEST"


def test_merge_to_shortest_for_brand(db):
    cat.merge_keywords(db, ["SWIGGY LIMITED", "SWIGGY INSTAMART"], "SWIGGY")
    assert {_kw_of(db, t) for t in (4, 5, 6)} == {"SWIGGY"}


def test_merge_records_alias_and_applies_on_import(db):
    cat.merge_keywords(db, ["SWIGGY LIMITED"], "SWIGGY")
    # A future import whose keyword derives to 'SWIGGY LIMITED' folds to 'SWIGGY'.
    assert cat.apply_alias(db, "SWIGGY LIMITED") == "SWIGGY"
    importer.import_stream(
        db, "new.csv", 1,
        [RawTransaction(date(2026, 5, 1), "SWIGGY LIMITED", 90.0, "debit")],
        force_card=False)
    got = db.connection.execute(
        "SELECT merchant_keyword FROM transactions WHERE raw_description='SWIGGY LIMITED'").fetchone()[0]
    assert got == "SWIGGY"


def test_merge_repoints_rules(db):
    cat.add_rule(db, "SWIGGY LIMITED", "Expense", "Food")
    cat.merge_keywords(db, ["SWIGGY LIMITED"], "SWIGGY")
    rule = {r.keyword for r in cat.list_rules(db)}
    assert "SWIGGY" in rule and "SWIGGY LIMITED" not in rule


def test_all_keywords_counts(db):
    kws = dict(cat.all_keywords(db))
    assert kws["SWIGGY"] == 1 and kws["A S NEST"] == 1


def test_merge_rejects_empty_canonical(db):
    with pytest.raises(ValueError):
        cat.merge_keywords(db, ["NEST"], "   ")


# --- tags --------------------------------------------------------------------

def test_tag_resolves_like_sub_category(db):
    cat.add_rule(db, "SWIGGY", "Expense", "Food", tag="reimbursable")
    row = db.connection.execute(
        "SELECT effective_tag FROM v_transactions_resolved WHERE merchant_keyword='SWIGGY'").fetchone()
    assert row[0] == "reimbursable"


def test_tag_in_list_rules_and_distinct(db):
    cat.add_rule(db, "SWIGGY", "Expense", "Food", tag="trip-goa")
    assert next(r for r in cat.list_rules(db) if r.keyword == "SWIGGY").tag == "trip-goa"
    assert "trip-goa" in cat.distinct_tags(db)


def test_edit_rule_sets_tag(db):
    rid = cat.add_rule(db, "SWIGGY", "Expense", "Food")
    rule = next(r for r in cat.list_rules(db) if r.id == rid)
    cat.edit_rule(db, rule.id, keyword="SWIGGY", category="Expense", sub_category="Food",
                  priority=0, excluded=False, direction=None, tag="work")
    assert next(r for r in cat.list_rules(db) if r.id == rid).tag == "work"
