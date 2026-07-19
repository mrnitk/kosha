"""Parser for HDFC Bank savings/current account statements (.xls export).

Layout (see Sample/Acct_Statement_*.xls): a block of account-metadata rows,
then a header row ``Date | Narration | Chq./Ref.No. | Value Dt | Withdrawal Amt.
| Deposit Amt. | Closing Balance``, then transaction rows. A transaction row is
identified by a ``DD/MM/YY`` date in column 0, which cleanly skips headers,
page breaks and the footer.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path
from typing import Iterator

import xlrd

from .base import BaseParser, RawTransaction

_DATE_RE = re.compile(r"^\d{2}/\d{2}/\d{2}$")

# Column indices in the HDFC export.
_COL_DATE = 0
_COL_NARRATION = 1
_COL_WITHDRAWAL = 4
_COL_DEPOSIT = 5


class HdfcBankParser(BaseParser):
    institution = "hdfc_bank"
    account_type = "bank"

    def can_parse(self, path: Path) -> bool:
        if path.suffix.lower() != ".xls":
            return False
        try:
            sheet = xlrd.open_workbook(str(path)).sheet_by_index(0)
        except Exception:
            return False
        # HDFC statements announce themselves in the first cell.
        top = str(sheet.cell_value(0, 0)) if sheet.nrows else ""
        return "HDFC BANK" in top.upper()

    def parse(self, path: Path) -> Iterator[RawTransaction]:
        sheet = xlrd.open_workbook(str(path)).sheet_by_index(0)
        for r in range(sheet.nrows):
            raw_date = str(sheet.cell_value(r, _COL_DATE)).strip()
            if not _DATE_RE.match(raw_date):
                continue

            narration = str(sheet.cell_value(r, _COL_NARRATION)).strip()
            withdrawal = self._num(sheet.cell_value(r, _COL_WITHDRAWAL))
            deposit = self._num(sheet.cell_value(r, _COL_DEPOSIT))

            if withdrawal > 0:
                amount, direction = withdrawal, "debit"
            elif deposit > 0:
                amount, direction = deposit, "credit"
            else:
                # Zero-value / informational line — skip.
                continue

            yield RawTransaction(
                txn_date=self._parse_date(raw_date),
                raw_description=narration,
                amount=amount,
                direction=direction,
            )

    @staticmethod
    def _parse_date(value: str) -> date:
        # DD/MM/YY, 2-digit year in the 2000s.
        return datetime.strptime(value, "%d/%m/%y").date()

    @staticmethod
    def _num(value) -> float:
        """Coerce a withdrawal/deposit cell to a float; blanks become 0.0."""
        if value in ("", None):
            return 0.0
        try:
            return float(value)
        except (TypeError, ValueError):
            # Some exports carry comma-grouped strings ("1,234.50").
            return float(str(value).replace(",", "").strip() or 0.0)
