"""Review screen: give merchant keywords a sub-category (and reclassify).

Every transaction already has a category by direction (credit->Income,
debit->Expense), so this screen is about the descriptive detail: select one or
more keywords ranked by amount, optionally inspect the transactions inside a
keyword, then assign a sub-category (Food, Rent, ...) and, if needed, a category
(e.g. mark an investment as Savings). Assignment writes rules that resolve
retroactively, and reviewed keywords drop off the list.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QCompleter, QFormLayout, QGroupBox,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton, QSpinBox,
    QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .. import categorization as cat
from ..db import Database
from ..format import format_inr


class CategorizationView(QWidget):
    """Keyword review, bulk assignment, and per-keyword drill-in."""

    changed = Signal()

    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self._db = db
        self._build()
        self.refresh()

    # --- construction --------------------------------------------------------

    def _build(self) -> None:
        root = QHBoxLayout(self)

        left = QVBoxLayout()
        left.addWidget(QLabel("<b>Keywords to review</b> (no sub-category yet — highest amount first)"))
        left.addLayout(self._build_filter_bar())

        split = QSplitter(Qt.Vertical)

        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels(["Keyword", "Dir", "Txns", "Amount", "Category"])
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.ExtendedSelection)  # multi-select
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        for c in (1, 2, 3, 4):
            hh.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        self._table.itemSelectionChanged.connect(self._on_select)
        split.addWidget(self._table)

        # Drill-in: transactions inside the selected keyword.
        detail = QWidget(); dl = QVBoxLayout(detail); dl.setContentsMargins(0, 0, 0, 0)
        self._detail_label = QLabel("Select a keyword to see its transactions")
        dl.addWidget(self._detail_label)
        self._detail = QTableWidget(0, 4)
        self._detail.setHorizontalHeaderLabels(["Date", "Description", "Amount", "Dir"])
        self._detail.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._detail.verticalHeader().setVisible(False)
        self._detail.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        dl.addWidget(self._detail)
        split.addWidget(detail)
        split.setSizes([340, 240])

        left.addWidget(split, stretch=1)
        left.addWidget(self._build_assign_box())
        root.addLayout(left, stretch=3)

        # Right: data coverage + live category / sub-category totals.
        right = QVBoxLayout()
        self._data_summary = QLabel("—")
        self._data_summary.setWordWrap(True)
        right.addWidget(self._data_summary)

        right.addWidget(QLabel("<b>Category totals</b>"))
        self._totals = QTableWidget(0, 3)
        self._totals.setHorizontalHeaderLabels(["Category", "Total", "Txns"])
        self._totals.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._totals.verticalHeader().setVisible(False)
        self._totals.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        right.addWidget(self._totals, stretch=1)

        right.addWidget(QLabel("<b>Sub-category totals</b>"))
        self._sub_totals = QTableWidget(0, 4)
        self._sub_totals.setHorizontalHeaderLabels(["Category", "Sub-category", "Total", "Txns"])
        self._sub_totals.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._sub_totals.verticalHeader().setVisible(False)
        self._sub_totals.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        right.addWidget(self._sub_totals, stretch=1)

        refresh = QPushButton("Refresh"); refresh.clicked.connect(self.refresh)
        right.addWidget(refresh)
        root.addLayout(right, stretch=1)

    def _build_filter_bar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        self._filter_text = QLineEdit()
        self._filter_text.setPlaceholderText("Filter by keyword…")
        self._filter_text.setClearButtonEnabled(True)
        self._filter_text.textChanged.connect(self._load_keywords)

        self._filter_category = QComboBox()
        self._filter_category.addItem("All categories", None)
        for c in cat.CATEGORIES:
            self._filter_category.addItem(cat.display_label(c), c)
        self._filter_category.currentIndexChanged.connect(self._load_keywords)

        bar.addWidget(QLabel("Filter:"))
        bar.addWidget(self._filter_text, stretch=1)
        bar.addWidget(self._filter_category)
        return bar

    def _build_assign_box(self) -> QGroupBox:
        box = QGroupBox("Assign selected keyword(s)")
        form = QFormLayout(box)

        self._selected_label = QLabel("—")
        form.addRow("Selected:", self._selected_label)

        self._category = QComboBox()
        for c in cat.CATEGORIES:
            self._category.addItem(cat.display_label(c), c)
        form.addRow("Category:", self._category)

        self._sub_category = QLineEdit()
        self._sub_category.setPlaceholderText("e.g. Food, Rent, Investments")
        self._completer = QCompleter([])
        self._completer.setCaseSensitivity(Qt.CaseInsensitive)
        self._sub_category.setCompleter(self._completer)
        form.addRow("Sub-category:", self._sub_category)

        self._priority = QSpinBox(); self._priority.setRange(-100, 100)
        form.addRow("Priority:", self._priority)

        self._exclude = QCheckBox("Exclude from all visuals and the table")
        form.addRow("", self._exclude)

        self._assign_btn = QPushButton("Assign")
        self._assign_btn.clicked.connect(self._on_assign)
        self._assign_btn.setEnabled(False)
        form.addRow(self._assign_btn)
        return box

    # --- data flow -----------------------------------------------------------

    def refresh(self) -> None:
        self._load_keywords()
        self._load_totals()
        self._load_sub_totals()
        self._load_data_summary()
        self._sub_category.completer().setModel(
            _string_model(cat.distinct_sub_categories(self._db))
        )
        self.changed.emit()

    def _load_keywords(self) -> None:
        all_keywords = cat.unreviewed_keywords(self._db)
        text = self._filter_text.text().strip().upper() if hasattr(self, "_filter_text") else ""
        want_cat = self._filter_category.currentData() if hasattr(self, "_filter_category") else None
        self._keywords = [
            ks for ks in all_keywords
            if (not text or text in ks.keyword.upper())
            and (want_cat is None or ks.suggested_category == want_cat)
        ]
        self._table.setRowCount(len(self._keywords))
        for row, ks in enumerate(self._keywords):
            kw = QTableWidgetItem(ks.keyword); kw.setData(Qt.UserRole, ks.keyword)
            dr = QTableWidgetItem(ks.direction); dr.setTextAlignment(Qt.AlignCenter)
            n = QTableWidgetItem(str(ks.txn_count)); n.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            amt = QTableWidgetItem(format_inr(ks.total_amount)); amt.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            category = QTableWidgetItem(cat.display_label(ks.suggested_category))
            self._table.setItem(row, 0, kw)
            self._table.setItem(row, 1, dr)
            self._table.setItem(row, 2, n)
            self._table.setItem(row, 3, amt)
            self._table.setItem(row, 4, category)

    def _load_totals(self) -> None:
        totals = cat.category_totals(self._db)
        self._totals.setRowCount(len(totals))
        for row, (category, total, n) in enumerate(totals):
            self._totals.setItem(row, 0, QTableWidgetItem(cat.display_label(category)))
            amt = QTableWidgetItem(format_inr(total)); amt.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self._totals.setItem(row, 1, amt)
            ni = QTableWidgetItem(str(n)); ni.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self._totals.setItem(row, 2, ni)

    def _load_sub_totals(self) -> None:
        rows = cat.subcategory_totals(self._db)
        self._sub_totals.setRowCount(len(rows))
        for row, (category, sub, total, n) in enumerate(rows):
            self._sub_totals.setItem(row, 0, QTableWidgetItem(cat.display_label(category)))
            self._sub_totals.setItem(row, 1, QTableWidgetItem(sub))
            amt = QTableWidgetItem(format_inr(total)); amt.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self._sub_totals.setItem(row, 2, amt)
            ni = QTableWidgetItem(str(n)); ni.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self._sub_totals.setItem(row, 3, ni)

    def _load_data_summary(self) -> None:
        lo, hi, n = cat.data_summary(self._db)
        if n:
            self._data_summary.setText(
                f"<b>Data:</b> {lo} → {hi} · <b>{n}</b> transactions"
            )
        else:
            self._data_summary.setText("<b>No data yet</b> — import statements to begin.")

    # --- interaction ---------------------------------------------------------

    def selected_rows(self):
        """The selected (keyword, direction) review slices, top-to-bottom."""
        rows = sorted({i.row() for i in self._table.selectedItems()})
        return [self._keywords[r] for r in rows if r < len(self._keywords)]

    def selected_keywords(self) -> list[str]:
        return [ks.keyword for ks in self.selected_rows()]

    def _on_select(self) -> None:
        sel = self.selected_rows()
        self._assign_btn.setEnabled(bool(sel))
        if not sel:
            self._selected_label.setText("—")
            self._assign_btn.setText("Assign")
            self._detail.setRowCount(0)
            self._detail_label.setText("Select a keyword to see its transactions")
            return
        if len(sel) == 1:
            ks = sel[0]
            self._selected_label.setText(f"{ks.keyword} ({ks.direction})")
            self._assign_btn.setText("Assign")
            # Default the category from this slice's direction.
            idx = self._category.findData(ks.suggested_category)
            if idx >= 0:
                self._category.setCurrentIndex(idx)
            self._load_detail(ks.keyword, ks.direction)
        else:
            self._selected_label.setText(f"{len(sel)} slices")
            self._assign_btn.setText(f"Assign {len(sel)} slices")
            self._detail.setRowCount(0)
            self._detail_label.setText("Multiple selected — assign applies to each (per its direction)")

    def _load_detail(self, keyword: str, direction: str | None = None) -> None:
        rows = cat.transactions_for_keyword(self._db, keyword, direction=direction)
        scope = f"{keyword} ({direction})" if direction else keyword
        self._detail_label.setText(f"<b>{len(rows)}</b> transaction(s) in <b>{scope}</b>")
        self._detail.setRowCount(len(rows))
        for r, (txn_date, desc, amount, txn_direction, _cat, _sub) in enumerate(rows):
            self._detail.setItem(r, 0, QTableWidgetItem(str(txn_date)))
            self._detail.setItem(r, 1, QTableWidgetItem(desc))
            amt = QTableWidgetItem(format_inr(amount)); amt.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self._detail.setItem(r, 2, amt)
            self._detail.setItem(r, 3, QTableWidgetItem(txn_direction))

    def assign(self, keywords, category: str, sub_category: str = "",
               priority: int = 0, excluded: bool = False, direction: str | None = None) -> None:
        """Assign a category/sub-category to one or many keywords (also for tests).

        ``direction`` None writes a rule that applies to both sides.
        """
        if isinstance(keywords, str):
            keywords = [keywords]
        cat.assign_many(self._db, keywords, category, sub_category or None, priority, excluded, direction)
        self.refresh()

    def _on_assign(self) -> None:
        sel = self.selected_rows()
        if not sel:
            return
        category = self._category.currentData()
        sub = self._sub_category.text().strip()
        priority = self._priority.value()
        excluded = self._exclude.isChecked()
        # Each selected slice is written scoped to its own direction.
        for ks in sel:
            cat.add_rule(self._db, ks.keyword, category, sub or None, priority, excluded, ks.direction)
        self._sub_category.clear()
        self._exclude.setChecked(False)
        self.refresh()


def _string_model(items):
    from PySide6.QtCore import QStringListModel
    return QStringListModel(list(items))
