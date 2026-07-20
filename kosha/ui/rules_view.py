"""Rules editor: view, edit, and delete existing keyword mappings.

The Categorize tab is for *first-time* review (keywords with no decision yet);
once a keyword is mapped it drops off that list. This tab is the place to change
a mapping afterwards — rename the keyword, retag its sub-category or category,
flip the exclude flag, adjust priority or direction scope, or delete it entirely.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QFormLayout, QGroupBox, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton, QSpinBox,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .. import categorization as cat
from ..db import Database

_DIRECTIONS = [("Both", None), ("Debit", "debit"), ("Credit", "credit")]


class RulesView(QWidget):
    """Manage existing category rules (edit / delete)."""

    changed = Signal()

    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self._db = db
        self._rules: list[cat.Rule] = []
        self._build()
        self.refresh()

    # --- construction --------------------------------------------------------

    def _build(self) -> None:
        root = QHBoxLayout(self)

        left = QVBoxLayout()
        left.addWidget(QLabel("<b>Mapped keywords</b> — select one to edit or delete"))

        bar = QHBoxLayout()
        self._filter = QLineEdit(); self._filter.setPlaceholderText("Filter by keyword…")
        self._filter.setClearButtonEnabled(True)
        self._filter.textChanged.connect(self._load_rules)
        bar.addWidget(QLabel("Filter:")); bar.addWidget(self._filter, stretch=1)
        left.addLayout(bar)

        self._table = QTableWidget(0, 6)
        self._table.setHorizontalHeaderLabels(
            ["Keyword", "Dir", "Category", "Sub-category", "Excluded", "Priority"]
        )
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        for c in (1, 2, 3, 4, 5):
            hh.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        self._table.itemSelectionChanged.connect(self._on_select)
        left.addWidget(self._table, stretch=1)
        root.addLayout(left, stretch=3)

        root.addWidget(self._build_editor(), stretch=1)

    def _build_editor(self) -> QGroupBox:
        box = QGroupBox("Edit rule")
        form = QFormLayout(box)

        self._keyword = QLineEdit()
        form.addRow("Keyword:", self._keyword)

        self._direction = QComboBox()
        for label, val in _DIRECTIONS:
            self._direction.addItem(label, val)
        form.addRow("Direction:", self._direction)

        self._category = QComboBox()
        for c in cat.CATEGORIES:
            self._category.addItem(cat.display_label(c), c)
        form.addRow("Category:", self._category)

        self._sub_category = QLineEdit()
        self._sub_category.setPlaceholderText("e.g. Food, Rent, Investments")
        form.addRow("Sub-category:", self._sub_category)

        self._excluded = QCheckBox("Exclude from all visuals and the table")
        form.addRow("", self._excluded)

        self._priority = QSpinBox(); self._priority.setRange(-100, 100)
        form.addRow("Priority:", self._priority)

        btns = QHBoxLayout()
        self._save_btn = QPushButton("Save"); self._save_btn.clicked.connect(self._on_save)
        self._delete_btn = QPushButton("Delete"); self._delete_btn.clicked.connect(self._on_delete)
        btns.addWidget(self._save_btn); btns.addWidget(self._delete_btn)
        form.addRow(btns)

        self._set_editor_enabled(False)
        return box

    # --- data flow -----------------------------------------------------------

    def refresh(self) -> None:
        self._load_rules()

    def _load_rules(self) -> None:
        text = self._filter.text().strip().upper() if hasattr(self, "_filter") else ""
        self._rules = [r for r in cat.list_rules(self._db) if not text or text in r.keyword.upper()]
        self._table.setRowCount(len(self._rules))
        for row, r in enumerate(self._rules):
            self._table.setItem(row, 0, QTableWidgetItem(r.keyword))
            self._table.setItem(row, 1, _centered(r.direction or "both"))
            self._table.setItem(row, 2, QTableWidgetItem(cat.display_label(r.category)))
            self._table.setItem(row, 3, QTableWidgetItem(r.sub_category or ""))
            self._table.setItem(row, 4, _centered("yes" if r.excluded else ""))
            self._table.setItem(row, 5, _centered(str(r.priority)))
        self._set_editor_enabled(False)

    def _current_rule(self):
        rows = {i.row() for i in self._table.selectedItems()}
        if len(rows) != 1:
            return None
        row = next(iter(rows))
        return self._rules[row] if row < len(self._rules) else None

    def _on_select(self) -> None:
        r = self._current_rule()
        if r is None:
            self._set_editor_enabled(False)
            return
        self._set_editor_enabled(True)
        self._keyword.setText(r.keyword)
        self._direction.setCurrentIndex(max(0, self._direction.findData(r.direction)))
        self._category.setCurrentIndex(max(0, self._category.findData(r.category)))
        self._sub_category.setText(r.sub_category or "")
        self._excluded.setChecked(bool(r.excluded))
        self._priority.setValue(r.priority)

    def _on_save(self) -> None:
        r = self._current_rule()
        if r is None:
            return
        try:
            cat.edit_rule(
                self._db, r.id,
                keyword=self._keyword.text(),
                category=self._category.currentData(),
                sub_category=self._sub_category.text().strip() or None,
                priority=self._priority.value(),
                excluded=self._excluded.isChecked(),
                direction=self._direction.currentData(),
            )
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid rule", str(exc))
            return
        self.refresh()
        self.changed.emit()

    def _on_delete(self) -> None:
        r = self._current_rule()
        if r is None:
            return
        if QMessageBox.question(
            self, "Delete rule",
            f"Delete the mapping for “{r.keyword}”"
            f"{f' ({r.direction})' if r.direction else ''}?",
        ) != QMessageBox.Yes:
            return
        cat.delete_rule(self._db, r.id)
        self.refresh()
        self.changed.emit()

    def _set_editor_enabled(self, on: bool) -> None:
        for w in (self._keyword, self._direction, self._category, self._sub_category,
                  self._excluded, self._priority, self._save_btn, self._delete_btn):
            w.setEnabled(on)


def _centered(text: str) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    item.setTextAlignment(Qt.AlignCenter)
    return item
