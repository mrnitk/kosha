"""Parser for HDFC Bank credit-card statements (.xls export).

Layout differs entirely from the bank account statement (see
Sample/*Billedstatements*.xls): a metadata + account-summary block, then a
header row at ``Transaction type | ... | Date & Time | ... | Description | ...
| AMT | ... | Debit / Credit``. A transaction row has ``Domestic`` or
``International`` in column 0 and a ``DD/MM/YYYY / HH:MM`` timestamp in column 9,
which cleanly excludes the reward/GST summary sections below the transactions.

Credit-card sign convention mapped to Kosha's: a purchase increases what you owe
== spending == ``debit``; a payment/refund is flagged ``Cr`` == ``credit``.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path
from typing import Iterator

import xlrd

from .base import BaseParser, RawTransaction

_DATE_RE = re.compile(r"^\d{2}/\d{2}/\d{4}")
_TXN_MARKERS = {"Domestic", "International"}

_COL_TYPE = 0
_COL_DATETIME = 9
_COL_DESCRIPTION = 12
_COL_AMOUNT = 20
_COL_DEBIT_CREDIT = 23


class HdfcCardParser(BaseParser):
    institution = "hdfc_card"
    account_type = "credit_card"

    def can_parse(self, path: Path) -> bool:
        if path.suffix.lower() != ".xls":
            return False
        try:
            sheet = xlrd.open_workbook(str(path)).sheet_by_index(0)
        except Exception:
            return False
        # Card statements carry a "Credit Card No." label in the header block.
        for r in range(min(6, sheet.nrows)):
            for c in range(sheet.ncols):
                if "CREDIT CARD NO" in str(sheet.cell_value(r, c)).upper():
                    return True
        return False

    def parse(self, path: Path) -> Iterator[RawTransaction]:
        sheet = xlrd.open_workbook(str(path)).sheet_by_index(0)
        for r in range(sheet.nrows):
            if str(sheet.cell_value(r, _COL_TYPE)).strip() not in _TXN_MARKERS:
                continue
            raw_dt = str(sheet.cell_value(r, _COL_DATETIME)).strip()
            if not _DATE_RE.match(raw_dt):
                continue

            description = str(sheet.cell_value(r, _COL_DESCRIPTION)).strip()
            amount = self._num(sheet.cell_value(r, _COL_AMOUNT))
            if amount <= 0:
                continue

            flag = str(sheet.cell_value(r, _COL_DEBIT_CREDIT)).strip().lower()
            direction = "credit" if flag == "cr" else "debit"

            yield RawTransaction(
                txn_date=self._parse_date(raw_dt),
                raw_description=description,
                amount=amount,
                direction=direction,
            )

    @staticmethod
    def _parse_date(value: str) -> date:
        # 'DD/MM/YYYY / HH:MM' -> take the date portion.
        return datetime.strptime(value[:10], "%d/%m/%Y").date()

    @staticmethod
    def _num(value) -> float:
        if value in ("", None):
            return 0.0
        try:
            return float(value)
        except (TypeError, ValueError):
            return float(str(value).replace(",", "").strip() or 0.0)
