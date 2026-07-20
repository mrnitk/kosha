"""Tests for the categorization engine under the Income/Expense/Savings model."""

from __future__ import annotations

import pytest

from kosha import categorization as cat
from kosha import crypto
from kosha.db import Database

FAST = crypto.Argon2Params(time_cost=1, memory_cost=8192, parallelism=1)
PW = "correct horse battery"


@pytest.fixture()
def db(tmp_path):
    d = Database(db_file=tmp_path / "kosha.db", salt_file=tmp_path / "kosha.salt")
    d.create(PW, params=FAST)
    _seed(d)
    yield d
    d.lock()


def _seed(db: Database) -> None:
    con = db.connection
    con.execute("INSERT INTO accounts(id,name,account_type,institution) VALUES (1,'A','bank','hdfc_bank')")
    rows = [
        (1, "SWIGGY LIMITED", 300.0, "debit"),
        (2, "SWIGGY LIMITED", 200.0, "debit"),
        (3, "ZERODHA", 5000.0, "debit"),
        (4, "ACME EMPLOYER", 90000.0, "credit"),
        (5, "RANDOM SHOP", 150.0, "debit"),
    ]
    for tid, kw, amt, direction in rows:
        con.execute(
            "INSERT INTO transactions(id,txn_date,raw_description,amount,direction,account_id,merchant_keyword,dedup_hash) "
            "VALUES (?,?,?,?,?,1,?,?)",
            (tid, "2026-04-01", f"DESC {kw}", amt, direction, kw, f"h{tid}"),
        )
    con.commit()


def _effective(db, txn_id):
    return db.connection.execute(
        "SELECT effective_category, effective_sub_category FROM v_transactions_resolved WHERE id=?",
        (txn_id,),
    ).fetchone()


# --- defaults & helpers ------------------------------------------------------

def test_default_category_by_direction(db):
    assert _effective(db, 1)[0] == "Expense"     # a debit
    assert _effective(db, 4)[0] == "Income"      # a credit
    assert _effective(db, 1)[1] is None          # no sub-category yet


def test_default_category_helper():
    assert cat.default_category("credit") == "Income"
    assert cat.default_category("debit") == "Expense"


@pytest.mark.parametrize("raw,expected", [
    ("income", "Income"), ("Saving", "Savings"), ("savings", "Savings"),
    ("investment", "Savings"), ("expense", "Expense"), ("nonsense", "Expense"),
])
def test_normalize_category(raw, expected):
    assert cat.normalize_category(raw) == expected


# --- rules -------------------------------------------------------------------

def test_add_rule_assigns_sub_category(db):
    cat.add_rule(db, "SWIGGY LIMITED", "Expense", "Food")
    assert _effective(db, 1) == ("Expense", "Food")
    assert _effective(db, 2) == ("Expense", "Food")   # retroactive


def test_add_rule_reclassifies_to_savings(db):
    cat.add_rule(db, "ZERODHA", "Savings", "Investments")
    assert _effective(db, 3) == ("Savings", "Investments")


def test_add_rule_normalizes_keyword_and_category(db):
    cat.add_rule(db, "  swiggy   limited ", "saving", "Treats")
    assert _effective(db, 1) == ("Savings", "Treats")


def test_reassigning_keyword_updates_in_place(db):
    cat.add_rule(db, "SWIGGY LIMITED", "Expense", "Food")
    cat.add_rule(db, "SWIGGY LIMITED", "Expense", "Dining")
    rules = [r for r in cat.list_rules(db) if r.keyword == "SWIGGY LIMITED"]
    assert len(rules) == 1 and rules[0].sub_category == "Dining"


def test_priority_breaks_ties(db):
    con = db.connection
    con.execute("INSERT INTO category_rules(keyword,category,sub_category,priority) VALUES ('ZERODHA','Savings','Invest',10)")
    con.execute("INSERT INTO category_rules(keyword,category,sub_category,priority) VALUES ('ZERODHA','Expense','Misc',1)")
    con.commit()
    assert _effective(db, 3) == ("Savings", "Invest")


def test_delete_rule_reverts_to_direction_default(db):
    rid = cat.add_rule(db, "SWIGGY LIMITED", "Savings", "Food")
    cat.delete_rule(db, rid)
    assert _effective(db, 1) == ("Expense", None)     # back to direction default


def test_add_rule_rejects_empty_keyword(db):
    with pytest.raises(ValueError):
        cat.add_rule(db, "   ", "Expense", "Food")


def test_assign_many_bulk(db):
    n = cat.assign_many(db, ["SWIGGY LIMITED", "RANDOM SHOP"], "Expense", "Shopping")
    assert n == 2
    assert _effective(db, 1)[1] == "Shopping"
    assert _effective(db, 5)[1] == "Shopping"


# --- overrides ---------------------------------------------------------------

def test_override_beats_rule(db):
    cat.add_rule(db, "SWIGGY LIMITED", "Expense", "Food")
    cat.set_override(db, 1, "Savings", "Gift")
    assert _effective(db, 1) == ("Savings", "Gift")   # override wins
    assert _effective(db, 2) == ("Expense", "Food")   # sibling follows rule


def test_clear_override_falls_back_to_rule(db):
    cat.add_rule(db, "SWIGGY LIMITED", "Expense", "Food")
    cat.set_override(db, 1, "Savings", "Gift")
    cat.clear_override(db, 1)
    assert _effective(db, 1) == ("Expense", "Food")


# --- review queries ----------------------------------------------------------

def test_unreviewed_keywords_ranked_and_direction(db):
    uk = cat.unreviewed_keywords(db)
    keywords = [k.keyword for k in uk]
    assert keywords[0] == "ACME EMPLOYER"             # 90000 highest amount
    acme = uk[0]
    assert acme.dominant_direction == "credit"
    assert acme.suggested_category == "Income"
    swiggy = next(k for k in uk if k.keyword == "SWIGGY LIMITED")
    assert swiggy.txn_count == 2 and swiggy.total_amount == 500.0


def test_assigning_sub_category_leaves_review_list(db):
    cat.add_rule(db, "ZERODHA", "Savings", "Investments")
    assert "ZERODHA" not in [k.keyword for k in cat.unreviewed_keywords(db)]


def test_rule_without_sub_category_stays_unreviewed(db):
    # Setting only a category (no sub-category) doesn't count as reviewed.
    cat.add_rule(db, "ZERODHA", "Savings", None)
    assert "ZERODHA" in [k.keyword for k in cat.unreviewed_keywords(db)]


def test_transactions_for_keyword(db):
    rows = cat.transactions_for_keyword(db, "SWIGGY LIMITED")
    assert len(rows) == 2
    assert all(r[3] == "debit" for r in rows)         # direction column


def test_category_totals_span_all_categories(db):
    cat.add_rule(db, "ZERODHA", "Savings", "Investments")
    totals = {c: t for c, t, _n in cat.category_totals(db)}
    assert totals["Income"] == 90000.0
    assert totals["Savings"] == 5000.0
    assert totals["Expense"] == 650.0                 # 300+200+150


def test_distinct_sub_categories(db):
    cat.add_rule(db, "SWIGGY LIMITED", "Expense", "Food")
    cat.add_rule(db, "RANDOM SHOP", "Expense", "Shopping")
    assert cat.distinct_sub_categories(db) == ["Food", "Shopping"]
