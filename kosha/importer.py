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
from .parsers import REGISTRY
from .parsers.base import BaseParser

# Friendly account names auto-created when bulk-importing by institution.
INSTITUTION_LABELS = {
    "hdfc_bank": "HDFC Bank",
    "hdfc_card": "HDFC Card",
}


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


def detect_parser(path) -> BaseParser | None:
    """Return the first registered parser that recognizes ``path``."""
    path = Path(path)
    for parser_cls in REGISTRY.values():
        parser = parser_cls()
        try:
            if parser.can_parse(path):
                return parser
        except Exception:
            continue
    return None


@dataclass
class BulkResult:
    imported: list[tuple[str, ImportResult]]     # (filename, result)
    unrecognized: list[str]                      # filenames with no parser
    failed: list[tuple[str, str]]                # (filename, error message)

    @property
    def total_inserted(self) -> int:
        return sum(r.inserted for _f, r in self.imported)

    @property
    def total_skipped(self) -> int:
        return sum(r.skipped_duplicates for _f, r in self.imported)

    def summary(self) -> str:
        lines = [
            f"{len(self.imported)} file(s) imported: "
            f"{self.total_inserted} new, {self.total_skipped} duplicate(s) skipped."
        ]
        if self.unrecognized:
            lines.append(f"Unrecognized ({len(self.unrecognized)}): " + ", ".join(self.unrecognized))
        if self.failed:
            lines.append("Failed: " + "; ".join(f"{f}: {e}" for f, e in self.failed))
        return "\n".join(lines)


def clear_data(db: Database, scope: str = "transactions") -> int:
    """Wipe imported data for a fresh start. Returns the transaction count removed.

    ``scope``:
        * ``"transactions"`` — delete transactions + import batches, but keep your
          categorization rules and accounts, so a re-import re-applies them.
        * ``"all"`` — full reset: also drop accounts and category rules.

    Irreversible. Runs as one transaction, then reclaims file space with VACUUM.
    """
    if scope not in ("transactions", "all"):
        raise ValueError(f"unknown scope {scope!r}")
    con = db.connection
    n = con.execute("SELECT count(*) FROM transactions").fetchone()[0]
    try:
        con.execute("DELETE FROM transactions")
        con.execute("DELETE FROM import_batches")
        if scope == "all":
            con.execute("DELETE FROM category_rules")
            con.execute("DELETE FROM accounts")
        con.commit()
    except Exception:
        con.rollback()
        raise
    # VACUUM can't run inside a transaction; sqlcipher3 auto-opens one, so commit
    # first (done above) then reclaim space outside it.
    con.execute("VACUUM")
    return n


def import_paths(db: Database, paths) -> BulkResult:
    """Bulk-import many statement files, auto-filing each by institution.

    Each file's parser is auto-detected; transactions land in a per-institution
    account (created on first sight). Unrecognized or failing files are reported
    but don't abort the batch.
    """
    imported: list[tuple[str, ImportResult]] = []
    unrecognized: list[str] = []
    failed: list[tuple[str, str]] = []

    for raw_path in paths:
        path = Path(raw_path)
        parser = detect_parser(path)
        if parser is None:
            unrecognized.append(path.name)
            continue
        try:
            name = INSTITUTION_LABELS.get(parser.institution, parser.institution)
            account_id = get_or_create_account(db, name, parser.account_type, parser.institution)
            result = import_file(db, parser, path, account_id)
            imported.append((path.name, result))
        except Exception as exc:
            failed.append((path.name, str(exc)))

    return BulkResult(imported=imported, unrecognized=unrecognized, failed=failed)
