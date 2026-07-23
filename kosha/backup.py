"""Encrypted vault backup / restore.

The whole vault is just two files — the SQLCipher database and its salt — so a
backup is a zip of both, and a restore replaces them. The backup stays encrypted
(it's a copy of the encrypted DB); it can only be opened with the master password
it was created under. Restore is atomic (extract to temp, then os.replace).
"""

from __future__ import annotations

import os
import zipfile
from pathlib import Path

DB_NAME = "kosha.db"
SALT_NAME = "kosha.salt"


def create_backup(zip_path, db_path, salt_path) -> None:
    """Write an encrypted backup zip containing the DB and its salt."""
    db_path, salt_path = Path(db_path), Path(salt_path)
    if not (db_path.exists() and salt_path.exists()):
        raise FileNotFoundError("no vault found to back up")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(db_path, DB_NAME)
        z.write(salt_path, SALT_NAME)


def is_valid_backup(zip_path) -> bool:
    try:
        with zipfile.ZipFile(zip_path) as z:
            return {DB_NAME, SALT_NAME} <= set(z.namelist())
    except (zipfile.BadZipFile, OSError):
        return False


def restore_backup(zip_path, db_path, salt_path) -> None:
    """Replace the vault with the backup's contents (atomic per file).

    Raises ValueError if the zip isn't a Kosha backup. The caller must ensure no
    connection is open to the target DB first.
    """
    db_path, salt_path = Path(db_path), Path(salt_path)
    with zipfile.ZipFile(zip_path) as z:
        names = set(z.namelist())
        if not {DB_NAME, SALT_NAME} <= names:
            raise ValueError("not a valid Kosha backup (missing kosha.db / kosha.salt)")
        db_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_db = db_path.with_suffix(db_path.suffix + ".restore")
        tmp_salt = salt_path.with_suffix(salt_path.suffix + ".restore")
        with z.open(DB_NAME) as src:
            tmp_db.write_bytes(src.read())
        with z.open(SALT_NAME) as src:
            tmp_salt.write_bytes(src.read())
    os.replace(tmp_db, db_path)
    os.replace(tmp_salt, salt_path)
