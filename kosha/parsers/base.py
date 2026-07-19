"""Parser interface and the common transaction record parsers emit."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterator


@dataclass(frozen=True)
class RawTransaction:
    """A single statement line, normalized to Kosha's common shape.

    This is the *layout* output of a parser: dates, amounts and the verbatim
    description. Semantic enrichment (txn_type, merchant_keyword, dedup_hash)
    happens later in ``kosha.features`` / ``kosha.importer`` so that parsers
    stay focused on each institution's file format.
    """

    txn_date: date
    raw_description: str          # verbatim from the statement, never modified
    amount: float                 # always positive; sign lives in `direction`
    direction: str                # 'debit' | 'credit'


class BaseParser(ABC):
    """Base class for institution statement parsers.

    Subclasses set ``institution`` (stable key, matches the DB accounts row and
    the parser registry) and ``account_type`` ('bank' | 'credit_card').
    """

    institution: str = ""
    account_type: str = ""

    @abstractmethod
    def can_parse(self, path: Path) -> bool:
        """Cheap check that ``path`` looks like this institution's statement."""

    @abstractmethod
    def parse(self, path: Path) -> Iterator[RawTransaction]:
        """Yield one RawTransaction per statement line, in file order."""
