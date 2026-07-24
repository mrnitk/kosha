"""Dashboard: filter controls, Plotly charts, and a drill-down table.

The web view (QWebEngineView) is created defensively — on machines where the
Chromium backend can't start we fall back to a message label so the rest of the
app still works. Chart/data construction is separated into ``build_html`` and
the analytics calls so it can be tested without rendering.
"""

from __future__ import annotations

import os
import tempfile
from datetime import date
from pathlib import Path

from PySide6.QtCore import QDate, Qt, QUrl, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDateEdit, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QPushButton, QSplitter, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from .. import analytics, categorization, charts
from ..db import Database
from ..format import format_inr
from .uihelp import autosize as _autosize, fit_columns as _fit_columns
from .widgets import CheckableComboBox


class DashboardView(QWidget):
    changed = Signal()          # emitted after a transaction edit/delete

    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self._db = db
        self._web = None
        self._assets_dir: Path | None = None
        self._html_file: Path | None = None
        self._plotlyjs: str = "inline"
        self._dirty = False
        self._build()
        self._setup_assets()          # cache plotly.js so the first build is cheap
        self.reset_filter_bounds()
        self.refresh()

    def _setup_assets(self) -> None:
        """Write plotly.min.js once so every build references it (small, fast)."""
        if not hasattr(self._web, "setUrl"):
            return                    # no web engine (e.g. tests) — never rendered
        try:
            self._assets_dir = Path(tempfile.mkdtemp(prefix="kosha_dashboard_"))
            self._html_file = self._assets_dir / "dashboard.html"
            self._plotlyjs = charts.write_plotlyjs(self._assets_dir)
        except Exception:
            self._plotlyjs = "inline"  # fall back to a self-contained page

    # --- lazy refresh --------------------------------------------------------

    def mark_dirty(self) -> None:
        """Note that the data changed without paying to rebuild the charts now.

        Rebuilding the Plotly page is ~1s, so we defer it until the dashboard is
        actually shown (see ``refresh_if_dirty``) rather than doing it on every
        categorize/rule edit made on another tab."""
        self._dirty = True

    def refresh_if_dirty(self) -> None:
        if self._dirty:
            self.refresh()

    # --- construction --------------------------------------------------------

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.addLayout(self._build_filter_bar())

        # Every block sits in a splitter so its borders can be dragged.
        splitter = QSplitter(Qt.Vertical)

        self._web = self._make_web_view()
        splitter.addWidget(self._web)

        self._table = QTableWidget(0, 11)
        self._table.setHorizontalHeaderLabels(
            ["Date", "Description", "Amount", "Dir", "Type", "Source",
             "Keyword", "Category", "Sub-category", "Tag", "Note"]
        )
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._table.setToolTip("Double-click a row to edit; select several to edit them together")
        self._table.doubleClicked.connect(self._on_edit_txn)
        self._table.verticalHeader().setVisible(False)
        _fit_columns(self._table, stretch_col=1)     # Description flexes; rest fit content

        # Bottom row: transactions (left, stretch) + compact monthly stats (right).
        bottom = QSplitter(Qt.Horizontal)
        bottom.addWidget(self._table)
        bottom.addWidget(self._build_stats())
        bottom.setStretchFactor(0, 4)
        bottom.setStretchFactor(1, 1)
        splitter.addWidget(bottom)
        splitter.setSizes([440, 240])

        root.addWidget(splitter, stretch=1)

    def _build_stats(self) -> QWidget:
        """Compact per-month average/min/max/total for Income/Expense/Savings."""
        box = QWidget(); lay = QVBoxLayout(box); lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(QLabel("<b>Monthly stats</b>"))
        t = QTableWidget(3, 5)
        t.setHorizontalHeaderLabels(["", "Average", "Min", "Max", "Total"])
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        t.verticalHeader().setVisible(False)
        _fit_columns(t, stretch_col=0)
        self._stats = t
        lay.addWidget(t)
        return box

    def _make_web_view(self):
        try:
            from PySide6.QtWebEngineWidgets import QWebEngineView
            return QWebEngineView()
        except Exception:
            label = QLabel("Charts unavailable: QtWebEngine could not start on this system.")
            label.setAlignment(Qt.AlignCenter)
            label.setWordWrap(True)
            return label

    def _build_filter_bar(self) -> QHBoxLayout:
        bar = QHBoxLayout()

        self._start = QDateEdit(); self._start.setCalendarPopup(True); self._start.setDisplayFormat("yyyy-MM-dd")
        self._end = QDateEdit(); self._end.setCalendarPopup(True); self._end.setDisplayFormat("yyyy-MM-dd")

        self._granularity = QComboBox()
        self._granularity.addItems(analytics.GRANULARITIES)

        self._category = QComboBox()   # 'All' + each category (canonical in userData)
        self._category.addItem("All categories", None)
        self._category.currentIndexChanged.connect(self._on_category_changed)

        self._sub_category = QComboBox()   # 'All' + sub-categories of the chosen category
        self._sub_category.addItem("All sub-categories", None)

        self._tags = CheckableComboBox("All tags")   # multi-select
        self._tags.setMinimumWidth(140)

        self._source = QComboBox()   # 'All' + each account (id in userData)
        self._source.addItem("All sources", None)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Search anything — description, keyword, category, tag, source, note, amount…")
        self._search.setClearButtonEnabled(True)
        self._search.returnPressed.connect(self.refresh)

        apply_btn = QPushButton("Apply")
        apply_btn.clicked.connect(self.refresh)

        bar.addWidget(QLabel("From")); bar.addWidget(self._start)
        bar.addWidget(QLabel("To")); bar.addWidget(self._end)
        bar.addWidget(QLabel("By")); bar.addWidget(self._granularity)
        bar.addWidget(self._category)
        bar.addWidget(self._sub_category)
        bar.addWidget(self._tags)
        bar.addWidget(self._source)
        bar.addWidget(self._search, stretch=1)
        bar.addWidget(apply_btn)
        return bar

    # --- filter state --------------------------------------------------------

    def reset_filter_bounds(self) -> None:
        # Default view: the last 6 months up to today (To defaults to today's date).
        end = date.today()
        start = _months_back(end, 6)
        self._start.setDate(QDate(start.year, start.month, start.day))
        self._end.setDate(QDate(end.year, end.month, end.day))
        self._reload_categories()
        self._reload_sources()
        self._reload_tags()

    def _reload_tags(self) -> None:
        # set_items keeps any still-present checked tags selected.
        self._tags.set_items(analytics.distinct_tags(self._db))

    def _reload_sources(self) -> None:
        current = self._source.currentData()
        self._source.blockSignals(True)
        self._source.clear()
        self._source.addItem("All sources", None)
        for acc_id, name in analytics.list_accounts(self._db):
            self._source.addItem(name, acc_id)
        idx = self._source.findData(current)
        self._source.setCurrentIndex(idx if idx >= 0 else 0)
        self._source.blockSignals(False)

    def _reload_categories(self) -> None:
        current = self._category.currentData()
        self._category.blockSignals(True)
        self._category.clear()
        self._category.addItem("All categories", None)
        for cat, _total, _n in analytics.category_totals(self._db, analytics.Filter()):
            self._category.addItem(categorization.display_label(cat), cat)
        idx = self._category.findData(current)
        self._category.setCurrentIndex(idx if idx >= 0 else 0)
        self._category.blockSignals(False)
        self._reload_sub_categories()

    def _reload_sub_categories(self) -> None:
        """Populate sub-categories for the chosen category (cascades)."""
        current = self._sub_category.currentData()
        category = self._category.currentData()
        self._sub_category.blockSignals(True)
        self._sub_category.clear()
        self._sub_category.addItem("All sub-categories", None)
        for sub in analytics.distinct_sub_categories(self._db, category):
            self._sub_category.addItem(sub, sub)
        idx = self._sub_category.findData(current)
        self._sub_category.setCurrentIndex(idx if idx >= 0 else 0)
        self._sub_category.blockSignals(False)

    def _on_category_changed(self, _idx: int) -> None:
        self._reload_sub_categories()

    def current_filter(self) -> analytics.Filter:
        s = self._start.date(); e = self._end.date()
        categories: tuple[str, ...] = ()
        if self._category.currentData() is not None:
            categories = (self._category.currentData(),)
        sub_categories: tuple[str, ...] = ()
        if self._sub_category.currentData() is not None:
            sub_categories = (self._sub_category.currentData(),)
        account_ids: tuple[int, ...] = ()
        if self._source.currentData() is not None:
            account_ids = (self._source.currentData(),)
        return analytics.Filter(
            start=date(s.year(), s.month(), s.day()),
            end=date(e.year(), e.month(), e.day()),
            categories=categories,
            sub_categories=sub_categories,
            tags=tuple(self._tags.checked_items()),
            account_ids=account_ids,
            search=self._search.text().strip(),
        )

    def granularity(self) -> str:
        return self._granularity.currentText()

    # --- rendering -----------------------------------------------------------

    def build_html(self) -> str:
        """Build the dashboard's combined chart HTML for the current filter."""
        flt = self.current_filter()
        gran = self.granularity()
        tmpl = "plotly_white"          # app is light-only
        figs = [
            charts.income_expense_line(analytics.income_expense_savings(self._db, flt, gran), tmpl),
            charts.spend_stacked_bar(analytics.spend_by_period_subcategory(self._db, flt, gran), gran, tmpl),
            charts.category_pie(analytics.subcategory_totals(self._db, flt, "Expense"), "Expense share by sub-category", tmpl),
            charts.spend_stacked_bar(analytics.spend_by_period_tag(self._db, flt, gran), gran, tmpl, label="tag"),
            charts.top_merchants_bar(analytics.top_merchants(self._db, flt), tmpl),
        ]
        return charts.dashboard_html(figs, dark=False, plotlyjs=self._plotlyjs)

    def refresh(self) -> None:
        self._dirty = False
        self._reload_tags()          # keep the tag filter current as tags change
        self._render(self.build_html())
        self._load_stats()
        self._load_table()

    def _load_stats(self) -> None:
        stats = analytics.monthly_stats(self._db, self.current_filter())
        labels = [
            ("Income", categorization.INCOME),
            ("Expense", categorization.EXPENSE),
            ("Savings / Investments", categorization.SAVINGS),
        ]
        for r, (label, key) in enumerate(labels):
            s = stats[key]
            self._stats.setItem(r, 0, QTableWidgetItem(label))
            for c, field in enumerate(("avg", "min", "max", "total"), start=1):
                item = QTableWidgetItem(format_inr(s[field]))
                item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self._stats.setItem(r, c, item)
        _autosize(self._stats)

    def _render(self, html: str) -> None:
        """Write the chart page and load it by file URL.

        QWebEngineView.setHtml() caps content at ~2 MB (it data-URL encodes it),
        so we write to a temp file beside the cached plotly.min.js and load that.
        """
        if not hasattr(self._web, "setUrl") or self._html_file is None:
            return
        self._html_file.write_text(html, encoding="utf-8")
        self._web.setUrl(QUrl.fromLocalFile(str(self._html_file)))

    def _load_table(self) -> None:
        rows = analytics.transactions(self._db, self.current_filter())
        self._table.setRowCount(len(rows))
        for r, (txn_date, desc, amount, direction, txn_type, source, keyword,
                category, sub, tag, note, txn_id) in enumerate(rows):
            cells = [
                txn_date, desc, format_inr(amount), direction, txn_type or "",
                source or "", keyword or "", categorization.display_label(category),
                sub or "", tag or "", note or "",
            ]
            for c, val in enumerate(cells):
                item = QTableWidgetItem(str(val))
                if c == 2:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if c == 0:
                    item.setData(Qt.UserRole, txn_id)     # remember which row is which txn
                self._table.setItem(r, c, item)
        _autosize(self._table)

    def _selected_txn_ids(self) -> list[int]:
        rows = sorted({i.row() for i in self._table.selectedItems()})
        ids = []
        for r in rows:
            item = self._table.item(r, 0)
            if item is not None and item.data(Qt.UserRole) is not None:
                ids.append(int(item.data(Qt.UserRole)))
        return ids

    def _on_edit_txn(self, *_args) -> None:
        ids = self._selected_txn_ids()
        if not ids:
            return
        from .transaction_editor import TransactionEditor
        dlg = TransactionEditor(self._db, ids, self)
        dlg.exec()
        if dlg.changed:          # set only when an edit/delete was applied
            self.refresh()
            self.changed.emit()


def _months_back(d: date, months: int) -> date:
    """The date ``months`` calendar months before ``d`` (clamped day-of-month)."""
    total = (d.year * 12 + (d.month - 1)) - months
    year, month = divmod(total, 12)
    month += 1
    # Clamp the day so e.g. 31 Aug - 6 months lands on a valid February day.
    day = min(d.day, [31, 29 if year % 4 == 0 and (year % 100 or not year % 400) else 28,
                      31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])
    return date(year, month, day)
