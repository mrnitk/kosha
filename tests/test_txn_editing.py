"""Tests for per-transaction editing: overrides, notes, delete (v10)."""

from __future__ import annotations

import pytest

from kosha import analytics, categorization as cat, crypto
from kosha.db import Database

FAST = crypto.Argon2Params(time_cost=1, memory_cost=8192, parallelism=1)
PW = "correct horse battery"


@pytest.fixture()
def db(tmp_path):
    d = Database(db_file=tmp_path / "kosha.db", salt_file=tmp_path / "kosha.salt")
    d.create(PW, params=FAST)
    con = d.connection
    con.execute("INSERT INTO accounts(id,name,account_type,institution) VALUES (1,'A','bank','hdfc_bank')")
    for tid, kw, amt, direction in [
        (1, "SWIGGY", 300.0, "debit"), (2, "SWIGGY", 200.0, "debit"),
        (3, "ZOMATO", 400.0, "debit"), (4, "EMPLOYER", 60000.0, "credit"),
    ]:
        con.execute(
            "INSERT INTO transactions(id,txn_date,raw_description,amount,direction,account_id,merchant_keyword,dedup_hash) "
            "VALUES (?,?,?,?,?,1,?,?)", (tid, "2026-04-01", f"D {kw}", amt, direction, kw, f"h{tid}"))
    con.commit()
    yield d
    d.lock()


def _eff(db, tid):
    return db.connection.execute(
        "SELECT effective_category, effective_sub_category, effective_tag, effective_excluded, note "
        "FROM v_transactions_resolved WHERE id=?", (tid,)).fetchone()


def test_get_transaction(db):
    t = cat.get_transaction(db, 1)
    assert t["id"] == 1 and t["merchant_keyword"] == "SWIGGY"
    assert t["effective_category"] == "Expense" and t["category_override"] is None


def test_set_overrides_single(db):
    cat.set_transaction_overrides(
        db, [1], category="Savings", sub_category="Investments", tag="work",
        excluded=None, note="one-off", set_note=True)
    cat_, sub, tag, exc, note = _eff(db, 1)
    assert (cat_, sub, tag, exc, note) == ("Savings", "Investments", "work", 0, "one-off")
    # Sibling untouched.
    assert _eff(db, 2)[0] == "Expense"


def test_overrides_beat_rules(db):
    cat.add_rule(db, "SWIGGY", "Expense", "Food")
    cat.set_transaction_overrides(db, [1], category="Savings", sub_category="Gift")
    assert _eff(db, 1)[:2] == ("Savings", "Gift")     # override wins
    assert _eff(db, 2)[:2] == ("Expense", "Food")     # rule applies to sibling


def test_clear_override_via_none(db):
    cat.set_transaction_overrides(db, [1], category="Savings")
    assert _eff(db, 1)[0] == "Savings"
    cat.set_transaction_overrides(db, [1], category=None)   # inherit again
    assert _eff(db, 1)[0] == "Expense"


def test_bulk_override_many(db):
    cat.set_transaction_overrides(db, [1, 2, 3], category="Savings", sub_category="Bulk")
    assert all(_eff(db, t)[:2] == ("Savings", "Bulk") for t in (1, 2, 3))
    assert _eff(db, 4)[0] == "Income"                 # not selected


def test_bulk_does_not_touch_note(db):
    cat.set_transaction_overrides(db, [1], note="keep me", set_note=True)
    cat.set_transaction_overrides(db, [1, 2], category="Savings")   # bulk, set_note=False
    assert _eff(db, 1)[4] == "keep me"                 # note preserved


def test_per_txn_exclude(db):
    cat.set_transaction_overrides(db, [1], excluded=True)
    assert _eff(db, 1)[3] == 1
    # Hidden from the default analytics view.
    rows = analytics.transactions(db, analytics.Filter())
    assert all(r[11] != 1 for r in rows)              # id column; txn 1 excluded
    assert 1 not in [r[11] for r in rows]


def test_delete_transactions(db):
    n = cat.delete_transactions(db, [1, 2])
    assert n == 2
    assert db.connection.execute("SELECT count(*) FROM transactions").fetchone()[0] == 2
    assert cat.get_transaction(db, 1) is None


def test_analytics_transactions_exposes_id_and_note(db):
    cat.set_transaction_overrides(db, [3], note="hello", set_note=True)
    row = next(r for r in analytics.transactions(db, analytics.Filter()) if r[1] == "D ZOMATO")
    assert row[10] == "hello"      # note column
    assert row[11] == 3            # id column
