"""Review screen: bucket uncategorized merchant keywords into categories.

Left panel lists uncategorized keywords ranked by total spend (the biggest
unbucketed money first). Selecting one and assigning a category writes a rule
via ``categorization.add_rule`` — which resolves retroactively — and the row
drops out of the list. The right panel shows live category totals.

The widget keeps no state of its own beyond the current selection; every action
re-reads from the database so it always reflects the true resolution.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QFormLayout, QGroupBox, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QPushButton, QSpinBox, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from .. import categorization as cat
from ..db import Database


class CategorizationView(QWidget):
    """Uncategorized-keyword review and rule assignment."""

    #: emitted after any change that affects resolution (assignment, refresh)
    changed = Signal()

    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self._db = db
        self._build()
        self.refresh()

    # --- construction --------------------------------------------------------

    def _build(self) -> None:
        root = QHBoxLayout(self)

        # Left: uncategorized keywords + assignment form.
        left = QVBoxLayout()
        left.addWidget(QLabel("<b>Uncategorized keywords</b> (highest spend first)"))

        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(["Keyword", "Txns", "Total spend"])
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        hh.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self._table.itemSelectionChanged.connect(self._on_select)
        left.addWidget(self._table, stretch=1)

        left.addWidget(self._build_assign_box())
        root.addLayout(left, stretch=2)

        # Right: live category totals.
        right = QVBoxLayout()
        right.addWidget(QLabel("<b>Category totals</b> (spending)"))
        self._totals = QTableWidget(0, 3)
        self._totals.setHorizontalHeaderLabels(["Category", "Total", "Txns"])
        self._totals.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._totals.verticalHeader().setVisible(False)
        self._totals.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        right.addWidget(self._totals, stretch=1)

        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh)
        right.addWidget(refresh)
        root.addLayout(right, stretch=1)

    def _build_assign_box(self) -> QGroupBox:
        box = QGroupBox("Assign selected keyword")
        form = QFormLayout(box)

        self._selected_label = QLabel("—")
        form.addRow("Keyword:", self._selected_label)

        self._category = QComboBox(); self._category.setEditable(True)
        self._category.setInsertPolicy(QComboBox.NoInsert)
        form.addRow("Category:", self._category)

        self._sub_category = QLineEdit()
        form.addRow("Sub-category:", self._sub_category)

        self._priority = QSpinBox(); self._priority.setRange(-100, 100)
        form.addRow("Priority:", self._priority)

        self._assign_btn = QPushButton("Assign")
        self._assign_btn.clicked.connect(self._on_assign)
        self._assign_btn.setEnabled(False)
        form.addRow(self._assign_btn)
        return box

    # --- data flow -----------------------------------------------------------

    def refresh(self) -> None:
        self._load_keywords()
        self._load_totals()
        self._load_categories()
        self.changed.emit()

    def _load_keywords(self) -> None:
        keywords = cat.uncategorized_keywords(self._db)
        self._table.setRowCount(len(keywords))
        for row, ks in enumerate(keywords):
            kw_item = QTableWidgetItem(ks.keyword)
            kw_item.setData(Qt.UserRole, ks.keyword)
            n_item = QTableWidgetItem(str(ks.txn_count))
            n_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            amt_item = QTableWidgetItem(f"{ks.total_amount:,.2f}")
            amt_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self._table.setItem(row, 0, kw_item)
            self._table.setItem(row, 1, n_item)
            self._table.setItem(row, 2, amt_item)

    def _load_totals(self) -> None:
        totals = cat.category_totals(self._db)
        self._totals.setRowCount(len(totals))
        for row, (category, total, n) in enumerate(totals):
            self._totals.setItem(row, 0, QTableWidgetItem(category))
            amt = QTableWidgetItem(f"{total:,.2f}")
            amt.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self._totals.setItem(row, 1, amt)
            n_item = QTableWidgetItem(str(n))
            n_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self._totals.setItem(row, 2, n_item)

    def _load_categories(self) -> None:
        current = self._category.currentText()
        self._category.clear()
        self._category.addItems(cat.distinct_categories(self._db))
        self._category.setCurrentText(current)

    # --- interaction ---------------------------------------------------------

    def selected_keyword(self) -> str | None:
        items = self._table.selectedItems()
        if not items:
            return None
        return self._table.item(items[0].row(), 0).data(Qt.UserRole)

    def _on_select(self) -> None:
        kw = self.selected_keyword()
        self._selected_label.setText(kw or "—")
        self._assign_btn.setEnabled(kw is not None)

    def assign(self, keyword: str, category: str, sub_category: str = "", priority: int = 0) -> None:
        """Programmatic assignment (also used by the Assign button and tests)."""
        cat.add_rule(self._db, keyword, category, sub_category or None, priority)
        self.refresh()

    def _on_assign(self) -> None:
        kw = self.selected_keyword()
        category = self._category.currentText().strip()
        if not kw or not category:
            return
        self.assign(kw, category, self._sub_category.text().strip(), self._priority.value())
        self._sub_category.clear()
