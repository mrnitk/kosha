"""Editor for one or more transactions.

Opened by double-clicking a row (or with several rows selected) in the dashboard
drill-down. Sets per-transaction overrides — category, sub-category, tag, exclude
— that win over keyword rules, plus a free-form note, and can delete the
transaction(s). A blank/"(from rule)" field means "inherit the keyword rule".
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QCompleter, QDialog, QDialogButtonBox, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton,
)

from .. import categorization as cat
from ..db import Database
from ..format import format_inr

_INHERIT = "(from rule)"
# excluded override: label -> value (None = inherit, True/False explicit)
_EXCLUDE_CHOICES = [("Inherit from rule", None), ("Include", False), ("Exclude", True)]


class TransactionEditor(QDialog):
    """Edit overrides/note for ``txn_ids`` (1..n). ``changed`` True if applied."""

    def __init__(self, db: Database, txn_ids: list[int], parent=None):
        super().__init__(parent)
        self._db = db
        self._ids = list(txn_ids)
        self.changed = False
        self._single = len(self._ids) == 1
        self.setWindowTitle("Edit transaction" if self._single else f"Edit {len(self._ids)} transactions")
        self.setMinimumWidth(440)
        self._build()
        if self._single:
            self._load_single()

    def _build(self) -> None:
        form = QFormLayout(self)

        self._context = QLabel("")
        self._context.setWordWrap(True)
        form.addRow(self._context)
        if not self._single:
            self._context.setText(f"<b>{len(self._ids)}</b> transactions selected — "
                                  "changes apply to all of them.")

        self._category = QComboBox()
        self._category.addItem(_INHERIT, None)
        for c in cat.CATEGORIES:
            self._category.addItem(cat.display_label(c), c)
        form.addRow("Category:", self._category)

        self._sub_category = QLineEdit()
        self._sub_category.setPlaceholderText(f"{_INHERIT} — blank inherits the rule")
        self._sub_category.setCompleter(_completer(cat.distinct_sub_categories(self._db)))
        form.addRow("Sub-category:", self._sub_category)

        self._tag = QLineEdit()
        self._tag.setPlaceholderText(f"comma-separated; blank {_INHERIT}")
        self._tag.setCompleter(_completer(cat.distinct_tags(self._db)))
        form.addRow("Tags:", self._tag)

        self._exclude = QComboBox()
        for label, val in _EXCLUDE_CHOICES:
            self._exclude.addItem(label, val)
        form.addRow("Exclude:", self._exclude)

        if self._single:
            self._note = QPlainTextEdit()
            self._note.setPlaceholderText("Optional note for this transaction")
            self._note.setFixedHeight(70)
            form.addRow("Note:", self._note)
        else:
            self._note = None

        buttons = QDialogButtonBox()
        save = buttons.addButton("Apply", QDialogButtonBox.AcceptRole)
        delete = buttons.addButton("Delete", QDialogButtonBox.DestructiveRole)
        buttons.addButton(QDialogButtonBox.Cancel)
        save.clicked.connect(self._on_apply)
        delete.clicked.connect(self._on_delete)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _load_single(self) -> None:
        t = cat.get_transaction(self._db, self._ids[0])
        if not t:
            return
        self._context.setText(
            f"<b>{t['txn_date']}</b> · {format_inr(t['amount'])} {t['direction']} · "
            f"{t['account_name'] or ''}<br><span style='color:gray'>{t['raw_description']}</span>")
        # Prefill with the current overrides (only what's explicitly set).
        if t["category_override"]:
            self._category.setCurrentIndex(max(0, self._category.findData(t["category_override"])))
        self._sub_category.setText(t["sub_category_override"] or "")
        self._tag.setText(t["tag_override"] or "")
        exc = t["excluded_override"]
        want = None if exc is None else bool(exc)
        self._exclude.setCurrentIndex(max(0, self._exclude.findData(want)))
        self._note.setPlainText(t["note"] or "")

    def _on_apply(self) -> None:
        cat.set_transaction_overrides(
            self._db, self._ids,
            category=self._category.currentData(),
            sub_category=self._sub_category.text(),
            tag=self._tag.text(),
            excluded=self._exclude.currentData(),
            note=(self._note.toPlainText() if self._single else None),
            set_note=self._single,
        )
        self.changed = True
        self.accept()

    def _on_delete(self) -> None:
        n = len(self._ids)
        if QMessageBox.warning(
            self, "Delete transaction(s)",
            f"Permanently delete {n} transaction(s)? This cannot be undone.",
            QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel) != QMessageBox.Yes:
            return
        cat.delete_transactions(self._db, self._ids)
        self.changed = True
        self.accept()


def _completer(items) -> QCompleter:
    c = QCompleter(list(items))
    c.setCaseSensitivity(Qt.CaseInsensitive)
    return c
