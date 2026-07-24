"""Reusable small widgets."""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import QComboBox


class CheckableComboBox(QComboBox):
    """A combo box whose items have checkboxes — for multi-select filters.

    The line edit shows the checked items (or a placeholder when none are), and
    the popup stays open while you toggle, since you usually pick several.
    """

    def __init__(self, placeholder: str = "All", parent=None):
        super().__init__(parent)
        self._placeholder = placeholder
        self.setEditable(True)
        self.lineEdit().setReadOnly(True)
        self.lineEdit().setPlaceholderText(placeholder)
        self._model = QStandardItemModel(self)
        self.setModel(self._model)
        self._model.itemChanged.connect(self._refresh_text)
        self.view().viewport().installEventFilter(self)

    def set_items(self, items) -> None:
        """Replace the choices, preserving any that were checked and still exist."""
        checked = set(self.checked_items())
        self._model.blockSignals(True)
        self._model.clear()
        for text in items:
            item = QStandardItem(text)
            item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            item.setData(Qt.Checked if text in checked else Qt.Unchecked, Qt.CheckStateRole)
            self._model.appendRow(item)
        self._model.blockSignals(False)
        self._refresh_text()

    def checked_items(self) -> list[str]:
        return [self._model.item(i).text() for i in range(self._model.rowCount())
                if self._model.item(i).checkState() == Qt.Checked]

    def clear_checked(self) -> None:
        for i in range(self._model.rowCount()):
            self._model.item(i).setCheckState(Qt.Unchecked)

    def eventFilter(self, obj, event):  # noqa: N802 (Qt signature)
        if obj is self.view().viewport() and event.type() == QEvent.MouseButtonRelease:
            index = self.view().indexAt(event.position().toPoint())
            item = self._model.itemFromIndex(index)
            if item is not None:
                item.setCheckState(Qt.Unchecked if item.checkState() == Qt.Checked else Qt.Checked)
            return True   # consume so the popup stays open for more picks
        return super().eventFilter(obj, event)

    def _refresh_text(self, *_a) -> None:
        checked = self.checked_items()
        self.lineEdit().setText(", ".join(checked))
