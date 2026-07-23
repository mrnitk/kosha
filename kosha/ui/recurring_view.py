"""Recurring / subscriptions view: merchants that repeat on a regular cadence.

Surfaces likely subscriptions, EMIs, SIPs and rent — anything paid on a steady
schedule — with an estimated next date and a per-month figure, plus the total
monthly committed outflow across all of them.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from .. import analytics
from .. import categorization as cat
from ..db import Database
from ..format import format_inr
from .uihelp import autosize as _autosize, fit_columns as _fit_columns

_HEADERS = ["Merchant", "Cadence", "Count", "Avg amount", "Last seen",
            "Next (est.)", "Per month", "Category"]


class RecurringView(QWidget):
    """Detected recurring merchants and total monthly commitment."""

    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self._db = db
        self._build()
        self.refresh()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.addWidget(QLabel(
            "<b>Recurring merchants</b> — subscriptions, EMIs, SIPs, rent and other "
            "regular payments detected from your history."))

        self._total = QLabel("—")
        self._total.setStyleSheet("font-size: 14px;")
        root.addWidget(self._total)

        self._table = QTableWidget(0, len(_HEADERS))
        self._table.setHorizontalHeaderLabels(_HEADERS)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.verticalHeader().setVisible(False)
        _fit_columns(self._table, stretch_col=0)
        root.addWidget(self._table, stretch=1)

        bar = QHBoxLayout(); bar.addStretch(1)
        refresh = QPushButton("Refresh"); refresh.clicked.connect(self.refresh)
        bar.addWidget(refresh)
        root.addLayout(bar)

    def refresh(self) -> None:
        rows, total_monthly = analytics.recurring_merchants(self._db)
        self._total.setText(
            f"Estimated monthly committed outflow: <b>₹{format_inr(total_monthly)}</b> "
            f"across <b>{len(rows)}</b> recurring merchant(s)")
        self._table.setRowCount(len(rows))
        for r, rec in enumerate(rows):
            self._set(r, 0, rec.keyword)
            self._set(r, 1, rec.cadence, center=True)
            self._set(r, 2, str(rec.count), right=True)
            self._set(r, 3, format_inr(rec.avg_amount), right=True)
            self._set(r, 4, str(rec.last_date), center=True)
            self._set(r, 5, str(rec.next_estimate), center=True)
            self._set(r, 6, format_inr(rec.monthly_amount), right=True)
            self._set(r, 7, cat.display_label(rec.category))
        _autosize(self._table)

    def _set(self, row: int, col: int, text: str, right: bool = False, center: bool = False) -> None:
        item = QTableWidgetItem(text)
        if right:
            item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
        elif center:
            item.setTextAlignment(Qt.AlignCenter)
        self._table.setItem(row, col, item)
