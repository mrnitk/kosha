"""Generate a synthetic HDFC-format .xls fixture for parser tests.

All data here is fabricated — no real accounts, names, or transactions — so the
resulting fixture is safe to commit (unlike the real files under Sample/).
Re-run with:  python tests/fixtures/make_hdfc_fixture.py
"""

from __future__ import annotations

from pathlib import Path

import xlwt

OUT = Path(__file__).parent / "hdfc_bank_sample.xls"

# (date, narration, withdrawal, deposit, closing) — fabricated.
TXNS = [
    ("01/04/26", "UPI-SWIGGY LIMITED-SWIGGY@YBL-YESB0YBLUPI-100000000001-PAID VIA CRED", 275.00, "", 9725.00),
    ("02/04/26", "UPI-JANE DOE-JANEDOE@OKICICI-ICIC0000001-100000000002-UPI", 500.00, "", 9225.00),
    ("03/04/26", "NEFT CR-YESB0000001-ACME BROKING LTD-SETTLEMENT-JOHN SMITH-REF12345", "", 15000.00, 24225.00),
    ("04/04/26", "ACH D- SOME MUTUAL FUND-0000ABCD1234", 2000.00, "", 22225.00),
    ("05/04/26", "IMPS-410512345678-RAHUL VERMA-HDFC0000123-SENTVIAPHONE", 1200.00, "", 21025.00),
    ("06/04/26", "IB BILLPAY DR-HDFCWI-552260XXXXXX1234", 5000.00, "", 16025.00),
    ("07/04/26", "NWD-ATM CASH-DELHI-410599", 3000.00, "", 13025.00),
    ("08/04/26", "POS 552260XXXXXX1234 AMAZON RETAIL", 899.00, "", 12126.00),
    ("09/04/26", "UPI-SWIGGY LIMITED-SWIGGY@YBL-YESB0YBLUPI-100000000009-PAID VIA CRED", 349.00, "", 11777.00),
]


def build() -> None:
    book = xlwt.Workbook()
    sh = book.add_sheet("Sheet 1")

    sh.write(0, 0, "HDFC BANK Ltd.                Page No.: 1        Statement of accounts")
    sh.write(5, 0, "MR. TEST USER")
    sh.write(14, 4, "Account No :50100000000000   CLASSIC")
    sh.write(15, 0, "Statement From : 01/04/2026   To : 30/04/2026")
    sh.write(19, 0, "*" * 120)
    headers = ["Date", "Narration", "Chq./Ref.No.", "Value Dt",
               "Withdrawal Amt.", "Deposit Amt.", "Closing Balance"]
    for c, h in enumerate(headers):
        sh.write(20, c, h)
    sh.write(21, 0, "*" * 8)

    row = 22
    for d, narration, wd, dep, close in TXNS:
        sh.write(row, 0, d)
        sh.write(row, 1, narration)
        sh.write(row, 2, "0000000000000000")
        sh.write(row, 3, d)
        if wd != "":
            sh.write(row, 4, wd)
        if dep != "":
            sh.write(row, 5, dep)
        sh.write(row, 6, close)
        row += 1

    sh.write(row + 2, 0, "---  End Of Statement ---")
    book.save(str(OUT))
    print(f"wrote {OUT} ({len(TXNS)} transactions)")


if __name__ == "__main__":
    build()
