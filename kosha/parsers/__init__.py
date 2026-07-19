"""Statement parsers.

One parser class per institution, each producing the common ``RawTransaction``
schema. Registration lets the importer pick a parser by institution key.
"""

from __future__ import annotations

from .base import BaseParser, RawTransaction
from .hdfc_bank import HdfcBankParser

# institution key -> parser class
REGISTRY: dict[str, type[BaseParser]] = {
    HdfcBankParser.institution: HdfcBankParser,
}


def get_parser(institution: str) -> BaseParser:
    try:
        return REGISTRY[institution]()
    except KeyError as exc:
        raise KeyError(f"no parser registered for institution {institution!r}") from exc


__all__ = ["BaseParser", "RawTransaction", "HdfcBankParser", "REGISTRY", "get_parser"]
