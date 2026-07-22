"""Small shared helpers for the Qt views."""

from __future__ import annotations

from PySide6.QtWidgets import QHeaderView, QTableWidget

_STRETCH_ATTR = "_kosha_stretch_col"


def fit_columns(table: QTableWidget, stretch_col: int | None = None) -> None:
    """Configure a table's columns for content-fit sizing — done cheaply.

    Persistent ``ResizeToContents`` is O(rows²) while a *visible* table is being
    filled (each ``setItem`` re-measures the whole column), which froze the app on
    large tables. Instead we make columns Interactive (one long-text column
    Stretches) and size them to content **once** after population via
    :func:`autosize`.
    """
    hh = table.horizontalHeader()
    for c in range(table.columnCount()):
        mode = QHeaderView.Stretch if c == stretch_col else QHeaderView.Interactive
        hh.setSectionResizeMode(c, mode)
    hh.setStretchLastSection(stretch_col is None)
    setattr(table, _STRETCH_ATTR, stretch_col)


def autosize(table: QTableWidget) -> None:
    """Size Interactive columns to their contents in a single pass (O(rows·cols)).

    Call after filling a table. Safe to call on tables set up with
    :func:`fit_columns`; the stretch column keeps stretching.
    """
    table.resizeColumnsToContents()
