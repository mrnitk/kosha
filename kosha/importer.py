"""Import pipeline: parser output -> enrichment -> deduplicated insert.

Ties the pieces together for Phase 2:

    parser.parse(file)  ->  RawTransaction stream
    features.derive_*   ->  txn_type, merchant_keyword
    dedup_hash + UNIQUE ->  re-importing overlapping statements skips duplicates

Everything runs inside one transaction per file so a failed import leaves no
half-written batch.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import features
from .db import Database
from .parsers.base import BaseParser


@dataclass
class ImportResult:
    account_id: int
    batch_id: int
    parsed: int          # rows the parser produced
    inserted: int        # new rows written
    skipped_duplicates: int

    def __str__(self) -> str:
        return (
            f"parsed {self.parsed}, inserted {self.inserted}, "
            f"skipped {self.skipped_duplicates} duplicate(s)"
        )


def get_or_create_account(db: Database, name: str, account_type: str, institution: str) -> int:
    """Return the id of the matching account, creating it if absent."""
    con = db.connection
    row = con.execute(
        "SELECT id FROM accounts WHERE name = ? AND institution = ?",
        (name, institution),
    ).fetchone()
    if row:
        return row[0]
    cur = con.execute(
        "INSERT INTO accounts(name, account_type, institution) VALUES (?,?,?)",
        (name, account_type, institution),
    )
    return cur.lastrowid


def import_file(db: Database, parser: BaseParser, path: Path, account_id: int) -> ImportResult:
    """Parse ``path`` with ``parser`` and insert new transactions for ``account_id``."""
    path = Path(path)
    con = db.connection

    parsed = 0
    inserted = 0
    skipped = 0

    # The sqlcipher3 DB-API driver opens transactions implicitly; we commit or
    # roll back the whole file as one unit.
    try:
        cur = con.execute(
            "INSERT INTO import_batches(source_file, account_id, row_count) VALUES (?,?,0)",
            (path.name, account_id),
        )
        batch_id = cur.lastrowid

        # On a credit-card statement every line is a card transaction; bank
        # narrations carry their own UPI/NEFT/etc. prefixes to classify.
        card_account = parser.account_type == "credit_card"
        for raw in parser.parse(path):
            parsed += 1
            txn_type = features.CARD if card_account else features.derive_txn_type(raw.raw_description)
            keyword = features.derive_merchant_keyword(raw.raw_description, txn_type)
            dhash = features.dedup_hash(
                raw.txn_date, raw.amount, raw.direction, raw.raw_description, account_id
            )
            result = con.execute(
                """INSERT OR IGNORE INTO transactions
                   (txn_date, raw_description, amount, direction, account_id,
                    txn_type, merchant_keyword, import_batch_id, dedup_hash)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    raw.txn_date.isoformat(), raw.raw_description, raw.amount,
                    raw.direction, account_id, txn_type, keyword, batch_id, dhash,
                ),
            )
            if result.rowcount == 1:
                inserted += 1
            else:
                skipped += 1

        con.execute("UPDATE import_batches SET row_count = ? WHERE id = ?", (inserted, batch_id))
        con.commit()
    except Exception:
        con.rollback()
        raise

    return ImportResult(
        account_id=account_id,
        batch_id=batch_id,
        parsed=parsed,
        inserted=inserted,
        skipped_duplicates=skipped,
    )
