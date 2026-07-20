"""Generate a synthetic HDFC credit-card .xls fixture for parser tests.

Fabricated data only — safe to commit. Mirrors the real layout: metadata block,
header at row 18, transactions with 'Domestic'/'International' in col 0, date in
col 9, description in col 12, amount in col 20, 'Cr' flag in col 23.
Re-run with:  python tests/fixtures/make_hdfc_card_fixture.py
"""

from __future__ import annotations

from pathlib import Path

import xlwt

OUT = Path(__file__).parent / "hdfc_card_sample.xls"

# (type, 'DD/MM/YYYY / HH:MM', description, amount, cr_flag) — fabricated.
TXNS = [
    ("Domestic", "14/03/2026 / 10:47", "WWW SWIGGY IN BANGALORE", "1,346.00", ""),
    ("Domestic", "14/03/2026 / 00:00", "WWW SWIGGY IN BANGALORE", "42.00", "Cr"),
    ("Domestic", "15/03/2026 / 18:26", "CRED ECOM GURGAON", "849.00", ""),
    ("Domestic", "15/03/2026 / 15:49", "PYU*Swiggy Food Bangalore", "196.00", ""),
    ("Domestic", "16/03/2026 / 20:34", "BUNDL TECHNOLOGIES BENGALURU", "2,011.00", ""),
    ("Domestic", "19/03/2026 / 11:26", "RAZ*Tijori Advisory Se Bangalore", "3,150.00", ""),
    ("International", "20/03/2026 / 09:15", "AMAZON WEB SERVICES USD", "1,500.00", ""),
    ("Domestic", "25/03/2026 / 00:00", "PAYMENT RECEIVED THANK YOU", "5,000.00", "Cr"),
]


def build() -> None:
    book = xlwt.Workbook()
    sh = book.add_sheet("Statement")

    sh.write(0, 4, "TEST USER")
    sh.write(2, 13, "Credit Card No.: 552260XXXXXX9999")
    sh.write(6, 4, "14 Apr, 2026")             # statement date
    sh.write(17, 0, "Domestic/ International Transactions")
    # header row 18
    sh.write(18, 0, "Transaction type")
    sh.write(18, 4, "Primary / Addon Customer Name")
    sh.write(18, 9, "Date & Time")
    sh.write(18, 12, "Description")
    sh.write(18, 20, "AMT")
    sh.write(18, 23, "Debit / Credit")

    row = 19
    for ttype, dt, desc, amt, cr in TXNS:
        sh.write(row, 0, ttype)
        sh.write(row, 4, "TEST USER")
        sh.write(row, 9, dt)
        sh.write(row, 12, desc)
        sh.write(row, 20, amt)
        if cr:
            sh.write(row, 23, cr)
        row += 1

    # A summary section that must NOT be picked up as transactions.
    sh.write(row + 1, 0, "Reward Points Summary")
    sh.write(row + 2, 0, "Opening Balance")
    book.save(str(OUT))
    print(f"wrote {OUT} ({len(TXNS)} transactions)")


if __name__ == "__main__":
    build()
