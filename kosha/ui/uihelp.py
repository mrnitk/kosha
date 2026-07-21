"""Small shared helpers for the Qt views."""

from __future__ import annotations

from PySide6.QtWidgets import QHeaderView, QTableWidget


def fit_columns(table: QTableWidget, stretch_col: int | None = None) -> None:
    """Size every column to its content; optionally stretch one long-text column.

    Keeps column widths tight to the data (per the 'columns as per content size'
    request) while letting a chosen column (e.g. Description/Keyword) absorb the
    remaining width so the table fills its space without a horizontal scrollbar.
    """
    hh = table.horizontalHeader()
    for c in range(table.columnCount()):
        mode = QHeaderView.Stretch if c == stretch_col else QHeaderView.ResizeToContents
        hh.setSectionResizeMode(c, mode)
    hh.setStretchLastSection(stretch_col is None)
