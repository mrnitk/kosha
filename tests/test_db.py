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
    db.change_password(PW, new_pw)
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
