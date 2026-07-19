"""Tests for the categorization engine: rules, overrides, resolution view."""

from __future__ import annotations

from datetime import date

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
    """Insert an account and a few transactions with known keywords."""
    con = db.connection
    con.execute("INSERT INTO accounts(id,name,account_type,institution) VALUES (1,'A','bank','hdfc_bank')")
    rows = [
        # id, keyword, amount, direction
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


# --- rules -------------------------------------------------------------------

def test_unresolved_defaults_to_uncategorized(db):
    assert _effective(db, 1)[0] == cat.UNCATEGORIZED


def test_add_rule_resolves_retroactively(db):
    cat.add_rule(db, "SWIGGY LIMITED", "Food", "Delivery")
    assert _effective(db, 1) == ("Food", "Delivery")
    assert _effective(db, 2) == ("Food", "Delivery")   # applies to all matching history


def test_add_rule_normalizes_keyword(db):
    # Lowercase + extra spaces should still match the stored uppercase keyword.
    cat.add_rule(db, "  swiggy   limited ", "Food")
    assert _effective(db, 1)[0] == "Food"


def test_reassigning_keyword_updates_in_place(db):
    cat.add_rule(db, "SWIGGY LIMITED", "Food")
    cat.add_rule(db, "SWIGGY LIMITED", "Dining")
    rules = [r for r in cat.list_rules(db) if r.keyword == "SWIGGY LIMITED"]
    assert len(rules) == 1
    assert rules[0].category == "Dining"


def test_priority_breaks_ties(db):
    con = db.connection
    con.execute("INSERT INTO category_rules(keyword,category,priority) VALUES ('ZERODHA','Investments',10)")
    con.execute("INSERT INTO category_rules(keyword,category,priority) VALUES ('ZERODHA','Misc',1)")
    con.commit()
    assert _effective(db, 3)[0] == "Investments"


def test_delete_rule_reverts_to_uncategorized(db):
    rid = cat.add_rule(db, "SWIGGY LIMITED", "Food")
    cat.delete_rule(db, rid)
    assert _effective(db, 1)[0] == cat.UNCATEGORIZED


def test_add_rule_rejects_empty(db):
    with pytest.raises(ValueError):
        cat.add_rule(db, "   ", "Food")
    with pytest.raises(ValueError):
        cat.add_rule(db, "SWIGGY", "")


# --- overrides ---------------------------------------------------------------

def test_override_beats_rule(db):
    cat.add_rule(db, "SWIGGY LIMITED", "Food")
    cat.set_override(db, 1, "Gifts")
    assert _effective(db, 1)[0] == "Gifts"        # override wins
    assert _effective(db, 2)[0] == "Food"         # sibling still follows rule


def test_clear_override_falls_back_to_rule(db):
    cat.add_rule(db, "SWIGGY LIMITED", "Food")
    cat.set_override(db, 1, "Gifts")
    cat.clear_override(db, 1)
    assert _effective(db, 1)[0] == "Food"


# --- review queries ----------------------------------------------------------

def test_uncategorized_keywords_ranked_by_spend(db):
    uk = cat.uncategorized_keywords(db)          # debits only
    keywords = [k.keyword for k in uk]
    assert "ACME EMPLOYER" not in keywords       # a credit, excluded
    assert keywords[0] == "ZERODHA"              # 5000 > swiggy 500 > shop 150
    swiggy = next(k for k in uk if k.keyword == "SWIGGY LIMITED")
    assert swiggy.txn_count == 2 and swiggy.total_amount == 500.0


def test_categorized_keyword_leaves_uncategorized_list(db):
    cat.add_rule(db, "ZERODHA", "Investments")
    assert "ZERODHA" not in [k.keyword for k in cat.uncategorized_keywords(db)]


def test_override_removes_from_uncategorized(db):
    cat.set_override(db, 5, "Misc")              # RANDOM SHOP
    assert "RANDOM SHOP" not in [k.keyword for k in cat.uncategorized_keywords(db)]


def test_category_totals_use_live_resolution(db):
    cat.add_rule(db, "SWIGGY LIMITED", "Food")
    totals = dict((c, t) for c, t, _ in cat.category_totals(db))
    assert totals["Food"] == 500.0
    assert cat.UNCATEGORIZED in totals
