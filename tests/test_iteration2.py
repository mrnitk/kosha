"""Tests for iteration 2: global search, recurring detection, backup/restore."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from kosha import analytics, backup, crypto
from kosha.db import Database

FAST = crypto.Argon2Params(time_cost=1, memory_cost=8192, parallelism=1)
PW = "correct horse battery"


@pytest.fixture()
def db(tmp_path):
    d = Database(db_file=tmp_path / "kosha.db", salt_file=tmp_path / "kosha.salt")
    d.create(PW, params=FAST)
    d.connection.execute(
        "INSERT INTO accounts(id,name,account_type,institution) VALUES (1,'A','bank','hdfc_bank')")
    d.connection.commit()
    yield d
    d.lock()


def _add(db, tid, d, kw, amt, direction="debit"):
    db.connection.execute(
        "INSERT INTO transactions(id,txn_date,raw_description,amount,direction,account_id,merchant_keyword,dedup_hash) "
        "VALUES (?,?,?,?,?,1,?,?)", (tid, d, f"UPI {kw} ref", amt, direction, kw, f"h{tid}"))


# --- global search -----------------------------------------------------------

def test_filter_search_matches_description_keyword_amount(db):
    _add(db, 1, "2026-04-01", "SWIGGY", 300.0)
    _add(db, 2, "2026-04-02", "AMAZON", 1500.0)
    db.connection.commit()
    # description / keyword
    rows = analytics.transactions(db, analytics.Filter(search="swiggy"))
    assert len(rows) == 1 and rows[0][6] == "SWIGGY"
    # amount substring
    rows = analytics.transactions(db, analytics.Filter(search="1500"))
    assert len(rows) == 1 and rows[0][6] == "AMAZON"
    # no match
    assert analytics.transactions(db, analytics.Filter(search="zzzz")) == []


# --- recurring detection -----------------------------------------------------

def test_recurring_detects_monthly(db):
    # Netflix on the 5th of each month for 5 months -> monthly cadence.
    for i in range(5):
        d = date(2026, 1 + i, 5).isoformat()
        _add(db, 10 + i, d, "NETFLIX", 199.0)
    # A one-off purchase should not be flagged.
    _add(db, 30, "2026-03-11", "RANDOM SHOP", 800.0)
    db.connection.commit()
    rows, total = analytics.recurring_merchants(db)
    netflix = next(r for r in rows if r.keyword == "NETFLIX")
    assert netflix.cadence == "monthly" and netflix.count == 5
    assert round(netflix.avg_amount) == 199
    assert 190 <= netflix.monthly_amount <= 210          # ~199/month
    assert "RANDOM SHOP" not in {r.keyword for r in rows}
    assert total >= netflix.monthly_amount


def test_recurring_ignores_transfers(db):
    from kosha import categorization as cat
    for i in range(4):
        _add(db, 40 + i, date(2026, 1 + i, 2).isoformat(), "HDFC CC", 5000.0)
    db.connection.commit()
    cat.add_rule(db, "HDFC CC", "Transfer")
    rows, _total = analytics.recurring_merchants(db)
    assert "HDFC CC" not in {r.keyword for r in rows}     # transfers excluded


def test_recurring_needs_min_occurrences(db):
    _add(db, 50, "2026-01-05", "GYM", 1000.0)
    _add(db, 51, "2026-02-05", "GYM", 1000.0)             # only 2 -> not enough
    db.connection.commit()
    rows, _t = analytics.recurring_merchants(db)
    assert "GYM" not in {r.keyword for r in rows}


# --- backup / restore --------------------------------------------------------

def test_backup_and_restore_roundtrip(tmp_path):
    db_file, salt_file = tmp_path / "kosha.db", tmp_path / "kosha.salt"
    d = Database(db_file=db_file, salt_file=salt_file)
    d.create(PW, params=FAST)
    d.connection.execute(
        "INSERT INTO accounts(id,name,account_type,institution) VALUES (1,'A','bank','x')")
    d.connection.execute(
        "INSERT INTO transactions(id,txn_date,raw_description,amount,direction,account_id,dedup_hash) "
        "VALUES (1,'2026-04-01','X',100,'debit',1,'h1')")
    d.connection.commit()
    d.lock()

    zip_path = tmp_path / "backup.zip"
    backup.create_backup(zip_path, db_file, salt_file)
    assert zip_path.exists() and backup.is_valid_backup(zip_path)

    # Corrupt/replace the live DB, then restore.
    db_file.write_bytes(b"broken")
    backup.restore_backup(zip_path, db_file, salt_file)

    d2 = Database(db_file=db_file, salt_file=salt_file)
    d2.unlock(PW)
    n = d2.connection.execute("SELECT count(*) FROM transactions").fetchone()[0]
    assert n == 1
    d2.lock()


def test_is_valid_backup_rejects_junk(tmp_path):
    bad = tmp_path / "notzip.zip"
    bad.write_bytes(b"not a zip")
    assert backup.is_valid_backup(bad) is False


def test_restore_rejects_wrong_zip(tmp_path):
    import zipfile
    z = tmp_path / "other.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("random.txt", "hi")
    with pytest.raises(ValueError):
        backup.restore_backup(z, tmp_path / "kosha.db", tmp_path / "kosha.salt")
