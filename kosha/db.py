"""Encrypted database lifecycle for Kosha.

Wraps a SQLCipher connection with three explicit states:

    * **create**  — first run: generate salt, derive key, apply schema.
    * **unlock**  — later runs: derive key from the master password, verify it.
    * **lock**    — drop the key/connection from memory (called on close).

The derived key lives only inside this object's SQLCipher connection; we never
write it to disk and clear our reference on lock.
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path
from typing import Optional

import sqlcipher3

from . import config, crypto

SCHEMA_VERSION = 3


class WrongPasswordError(Exception):
    """Raised when the derived key fails to decrypt the database."""


class DatabaseExistsError(Exception):
    """Raised when create() is called but a database is already present."""


class DatabaseLockedError(Exception):
    """Raised when an operation needs an unlocked connection but none exists."""


def _load_schema_sql() -> str:
    return resources.files("kosha").joinpath("schema.sql").read_text(encoding="utf-8")


class Database:
    """A single encrypted Kosha database.

    Not thread-safe; intended to be owned by the app's main thread.
    """

    def __init__(self, db_file: Optional[Path] = None, salt_file: Optional[Path] = None):
        self._db_path = Path(db_file) if db_file else config.db_path()
        self._salt_path = Path(salt_file) if salt_file else config.salt_path()
        self._con: Optional[sqlcipher3.Connection] = None

    # --- state queries -------------------------------------------------------

    @property
    def exists(self) -> bool:
        """True if a database has been created (both DB and salt present)."""
        return self._db_path.exists() and self._salt_path.exists()

    @property
    def is_unlocked(self) -> bool:
        return self._con is not None

    # --- lifecycle -----------------------------------------------------------

    def create(self, password: str, params: Optional[crypto.Argon2Params] = None) -> None:
        """Initialise a brand-new encrypted database with ``password``."""
        if self.exists:
            raise DatabaseExistsError(f"database already exists at {self._db_path}")
        params = params or crypto.Argon2Params()
        salt = crypto.new_salt()
        key = crypto.derive_key(password, salt, params)

        con = sqlcipher3.connect(str(self._db_path))
        try:
            self._apply_key(con, key)
            # Force the header to be written so a wrong password later actually fails.
            con.execute("CREATE TABLE IF NOT EXISTS _kosha_init (x INTEGER)")
            con.execute("DROP TABLE _kosha_init")
            con.executescript(_load_schema_sql())
            con.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            con.commit()
        except Exception:
            con.close()
            # Roll back a half-written file so state stays clean.
            self._db_path.unlink(missing_ok=True)
            raise
        # Only persist the salt once the DB is sound.
        crypto.write_salt_file(self._salt_path, salt, params)
        self._con = con

    def unlock(self, password: str) -> None:
        """Open an existing database, verifying ``password``."""
        if not self.exists:
            raise FileNotFoundError(f"no database at {self._db_path}")
        salt, params = crypto.read_salt_file(self._salt_path)
        key = crypto.derive_key(password, salt, params)

        con = sqlcipher3.connect(str(self._db_path))
        self._apply_key(con, key)
        try:
            # First read forces SQLCipher to decrypt page 1; wrong key raises here.
            con.execute("SELECT count(*) FROM sqlite_master")
        except sqlcipher3.DatabaseError as exc:
            con.close()
            raise WrongPasswordError("incorrect master password") from exc
        self._migrate(con)
        self._con = con

    def lock(self) -> None:
        """Close the connection and drop the key from memory."""
        if self._con is not None:
            self._con.close()
            self._con = None

    def change_password(self, old_password: str, new_password: str) -> None:
        """Re-key the database in place using SQLCipher's ``PRAGMA rekey``."""
        self._require_unlocked()
        # Verify the old password matches what unlocked us before re-keying.
        salt, params = crypto.read_salt_file(self._salt_path)
        if crypto.derive_key(old_password, salt, params) != self._current_key:
            raise WrongPasswordError("old password does not match")

        new_salt = crypto.new_salt()
        new_key = crypto.derive_key(new_password, new_salt, params)
        pragma = crypto.key_to_sqlcipher_pragma(new_key)
        self._con.execute(f"PRAGMA rekey = {pragma}")
        self._con.commit()
        crypto.write_salt_file(self._salt_path, new_salt, params)
        self._current_key = new_key

    # --- access --------------------------------------------------------------

    @property
    def connection(self) -> sqlcipher3.Connection:
        self._require_unlocked()
        return self._con

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *_exc) -> None:
        self.lock()

    # --- internals -----------------------------------------------------------

    def _apply_key(self, con: sqlcipher3.Connection, key: bytes) -> None:
        con.execute(f"PRAGMA key = {crypto.key_to_sqlcipher_pragma(key)}")
        self._current_key = key

    def _migrate(self, con: sqlcipher3.Connection) -> None:
        current = con.execute("PRAGMA user_version").fetchone()[0]
        if current > SCHEMA_VERSION:
            raise RuntimeError(
                f"database schema v{current} is newer than this app (v{SCHEMA_VERSION})"
            )
        if current < SCHEMA_VERSION:
            # Re-apply schema (idempotent tables; the view is dropped+recreated).
            con.executescript(_load_schema_sql())
            if current < 3:
                self._migrate_to_v3(con)
            con.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            con.commit()

    @staticmethod
    def _migrate_to_v3(con: sqlcipher3.Connection) -> None:
        """Normalize free-text categories to the Income/Expense/Savings set.

        Earlier builds let users type category names; fold known variants onto
        the canonical values so the new fixed-category model is consistent.
        """
        remap = [
            ("Savings", ("saving", "savings", "invest", "investment")),
            ("Income", ("income",)),
            ("Expense", ("expense",)),
        ]
        for canonical, variants in remap:
            marks = ",".join("?" * len(variants))
            con.execute(
                f"UPDATE category_rules SET category=? WHERE lower(category) IN ({marks})",
                (canonical, *variants),
            )
            con.execute(
                f"UPDATE transactions SET category_override=? "
                f"WHERE category_override IS NOT NULL AND lower(category_override) IN ({marks})",
                (canonical, *variants),
            )

    def _require_unlocked(self) -> None:
        if self._con is None:
            raise DatabaseLockedError("database is locked; call unlock() first")
