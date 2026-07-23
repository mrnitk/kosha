"""Merge similar merchant keywords into one canonical keyword.

Lists every distinct keyword with its transaction count; the user multi-selects
the variants (e.g. NEST, A S, A S NEST — or SWIGGY, SWIGGY LIMITED) and types the
one keyword they should all become. The merge rewrites existing transactions and
rules and records an alias so future imports fold automatically.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from .. import categorization as cat
from ..db import Database
from ..format import format_inr
from .uihelp import autosize as _autosize, fit_columns as _fit_columns


class MergeKeywordsDialog(QDialog):
    def __init__(self, db: Database, preselect=None, parent=None):
        super().__init__(parent)
        self._db = db
        self._preselect = {cat.features.normalize_keyword(k) for k in (preselect or [])}
        self.merged = 0
        self.setWindowTitle("Merge keywords")
        self.setMinimumSize(560, 560)
        self._build()
        self._load()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.addWidget(QLabel(
            "<b>Select the keyword variants</b> that are the same merchant, then set "
            "the single keyword they should all become."))

        bar = QHBoxLayout()
        self._filter = QLineEdit(); self._filter.setPlaceholderText("Filter keywords…")
        self._filter.setClearButtonEnabled(True)
        self._filter.textChanged.connect(self._load)
        bar.addWidget(QLabel("Filter:")); bar.addWidget(self._filter, stretch=1)
        root.addLayout(bar)

        self._table = QTableWidget(0, 2)
        self._table.setHorizontalHeaderLabels(["Keyword", "Txns"])
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        _fit_columns(self._table, stretch_col=0)
        self._table.itemSelectionChanged.connect(self._on_select)
        root.addWidget(self._table, stretch=1)

        cbar = QHBoxLayout()
        cbar.addWidget(QLabel("Merge into:"))
        self._canonical = QLineEdit(); self._canonical.setPlaceholderText("canonical keyword, e.g. SWIGGY")
        cbar.addWidget(self._canonical, stretch=1)
        root.addLayout(cbar)

        self._summary = QLabel(" ")
        root.addWidget(self._summary)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Merge")
        buttons.accepted.connect(self._on_merge)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _load(self) -> None:
        text = self._filter.text().strip().upper()
        rows = [(k, n) for k, n in cat.all_keywords(self._db) if not text or text in k.upper()]
        self._table.setRowCount(len(rows))
        for r, (kw, n) in enumerate(rows):
            item = QTableWidgetItem(kw)
            self._table.setItem(r, 0, item)
            ni = QTableWidgetItem(str(n)); ni.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self._table.setItem(r, 1, ni)
            if kw in self._preselect:
                item.setSelected(True)
                self._table.item(r, 1).setSelected(True)
        _autosize(self._table)
        self._on_select()

    def _selected(self) -> list[str]:
        rows = sorted({i.row() for i in self._table.selectedItems()})
        return [self._table.item(r, 0).text() for r in rows]

    def _on_select(self) -> None:
        sel = self._selected()
        # Default the canonical to the longest selected variant (a good guess for
        # partial-extraction cases like NEST / A S / A S NEST -> A S NEST).
        if sel and not self._canonical.text().strip():
            self._canonical.setText(max(sel, key=len))
        self._summary.setText(f"{len(sel)} keyword(s) selected" if sel else " ")

    def _on_merge(self) -> None:
        sel = self._selected()
        canonical = self._canonical.text().strip()
        if len(sel) < 1 or not canonical:
            QMessageBox.warning(self, "Nothing to merge",
                                "Select the keyword variants and enter the keyword to merge into.")
            return
        try:
            self.merged = cat.merge_keywords(self._db, sel, canonical)
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid keyword", str(exc))
            return
        self.accept()
