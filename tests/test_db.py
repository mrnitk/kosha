"""Tests for the encrypted database lifecycle and key derivation."""

from __future__ import annotations

import pytest

from kosha import crypto
from kosha.db import (
    Database,
    DatabaseExistsError,
    DatabaseLockedError,
    SCHEMA_VERSION,
    WrongPasswordError,
)

# Fast Argon2 params so the suite runs quickly; real app uses the defaults.
FAST = crypto.Argon2Params(time_cost=1, memory_cost=8192, parallelism=1)

PW = "correct horse battery"
WRONG = "Tr0ub4dor&3"


@pytest.fixture()
def db(tmp_path):
    return Database(db_file=tmp_path / "kosha.db", salt_file=tmp_path / "kosha.salt")


# --- crypto ------------------------------------------------------------------

def test_derive_key_is_deterministic():
    salt = crypto.new_salt()
    k1 = crypto.derive_key(PW, salt, FAST)
    k2 = crypto.derive_key(PW, salt, FAST)
    assert k1 == k2
    assert len(k1) == crypto.KEY_BYTES


def test_derive_key_varies_by_salt_and_password():
    salt_a, salt_b = crypto.new_salt(), crypto.new_salt()
    assert crypto.derive_key(PW, salt_a, FAST) != crypto.derive_key(PW, salt_b, FAST)
    assert crypto.derive_key(PW, salt_a, FAST) != crypto.derive_key(WRONG, salt_a, FAST)


def test_salt_file_roundtrip(tmp_path):
    path = tmp_path / "kosha.salt"
    salt = crypto.new_salt()
    crypto.write_salt_file(path, salt, FAST)
    got_salt, got_params = crypto.read_salt_file(path)
    assert got_salt == salt
    assert got_params == FAST


# --- lifecycle ---------------------------------------------------------------

def test_accepts_string_paths(tmp_path):
    # Frozen entry point passes str paths; Database must coerce to Path.
    d = Database(db_file=str(tmp_path / "k.db"), salt_file=str(tmp_path / "k.salt"))
    assert not d.exists
    d.create(PW, params=FAST)
    assert d.exists
    d.lock()


def test_create_then_unlock(db):
    assert not db.exists
    db.create(PW, params=FAST)
    assert db.exists and db.is_unlocked
    assert db.connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    db.lock()
    assert not db.is_unlocked

    db.unlock(PW)
    assert db.is_unlocked
    tables = {
        r[0] for r in db.connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert {"transactions", "accounts", "category_rules", "import_batches"} <= tables
    db.lock()


def test_wrong_password_rejected(db):
    db.create(PW, params=FAST)
    db.lock()
    with pytest.raises(WrongPasswordError):
        db.unlock(WRONG)
    assert not db.is_unlocked


def test_create_twice_fails(db):
    db.create(PW, params=FAST)
    db.lock()
    with pytest.raises(DatabaseExistsError):
        db.create(PW, params=FAST)


def test_data_survives_lock_cycle(db):
    db.create(PW, params=FAST)
    db.connection.execute(
        "INSERT INTO accounts(name, account_type, institution) VALUES (?,?,?)",
        ("HDFC Savings", "bank", "hdfc"),
    )
    db.connection.commit()
    db.lock()

    db.unlock(PW)
    rows = db.connection.execute("SELECT name FROM accounts").fetchall()
    assert rows == [("HDFC Savings",)]
    db.lock()


def test_change_password(db):
    db.create(PW, params=FAST)
    new_pw = "a whole new password"
    db.change_password(PW, new_pw, params=FAST)     # FAST keeps the test quick
    db.lock()

    with pytest.raises(WrongPasswordError):
        db.unlock(PW)
    db.unlock(new_pw)
    assert db.is_unlocked
    db.lock()


def test_change_password_wrong_old(db):
    db.create(PW, params=FAST)
    with pytest.raises(WrongPasswordError):
        db.change_password(WRONG, "irrelevant new pw")


def test_change_password_upgrades_kdf_params(db, tmp_path):
    """A re-key adopts stronger Argon2 defaults, so old vaults catch up."""
    weak = crypto.Argon2Params(time_cost=1, memory_cost=8192, parallelism=1)
    db.create(PW, params=weak)
    stronger = crypto.Argon2Params(time_cost=2, memory_cost=16384, parallelism=1)
    db.change_password(PW, "a whole new password", params=stronger)
    _salt, params = crypto.read_salt_file(db.salt_path)
    assert params == stronger                       # salt file records the new cost
    db.lock()
    db.unlock("a whole new password")                # still opens with them
    assert db.is_unlocked
    db.lock()


def test_connection_when_locked_raises(db):
    with pytest.raises(DatabaseLockedError):
        _ = db.connection


def test_encrypted_at_rest(db, tmp_path):
    db.create(PW, params=FAST)
    db.connection.execute(
        "INSERT INTO accounts(name, account_type, institution) VALUES (?,?,?)",
        ("SECRET_MERCHANT_MARKER", "bank", "hdfc"),
    )
    db.connection.commit()
    db.lock()
    raw = (tmp_path / "kosha.db").read_bytes()
    assert b"SECRET_MERCHANT_MARKER" not in raw
    assert raw[:6] != b"SQLite"  # header is encrypted too


def test_v3_migration_normalizes_categories(tmp_path):
    # Simulate a pre-v3 vault that used free-text category values.
    d = Database(db_file=tmp_path / "k.db", salt_file=tmp_path / "k.salt")
    d.create(PW, params=FAST)
    con = d.connection
    con.execute("INSERT INTO accounts(id,name,account_type,institution) VALUES (1,'A','bank','x')")
    con.execute("INSERT INTO category_rules(keyword,category,sub_category) VALUES ('K','Saving','Inv')")
    con.execute("PRAGMA user_version = 2")
    con.commit()
    d.lock()

    d.unlock(PW)                       # triggers migration to v3
    assert d.connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    cat = d.connection.execute("SELECT category FROM category_rules WHERE keyword='K'").fetchone()[0]
    assert cat == "Savings"            # 'Saving' -> canonical
    d.lock()
