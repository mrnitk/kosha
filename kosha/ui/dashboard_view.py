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

from PySide6.QtCore import QDate, Qt, QUrl
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDateEdit, QHBoxLayout, QLabel, QPushButton,
    QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .. import analytics, categorization, charts
from ..db import Database
from ..format import format_inr


class DashboardView(QWidget):
    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self._db = db
        self._web = None
        self._html_file: Path | None = None
        self._dark = False
        self._build()
        self.reset_filter_bounds()
        self.refresh()

    # --- construction --------------------------------------------------------

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.addLayout(self._build_filter_bar())
        root.addWidget(self._build_stats())

        splitter = QSplitter(Qt.Vertical)

        self._web = self._make_web_view()
        splitter.addWidget(self._web)

        self._table = QTableWidget(0, 8)
        self._table.setHorizontalHeaderLabels(
            ["Date", "Description", "Amount", "Dir", "Type", "Source", "Category", "Sub-category"]
        )
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setStretchLastSection(True)
        splitter.addWidget(self._table)
        splitter.setSizes([420, 220])

        root.addWidget(splitter, stretch=1)

    def _build_stats(self) -> QTableWidget:
        """Compact per-month average/min/max/total for Income/Expense/Savings."""
        t = QTableWidget(3, 5)
        t.setHorizontalHeaderLabels(["Per month", "Average", "Min", "Max", "Total"])
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        t.verticalHeader().setVisible(False)
        t.horizontalHeader().setStretchLastSection(True)
        t.setFixedHeight(118)          # header + 3 compact rows; no scrollbar
        self._stats = t
        return t

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

        self._source = QComboBox()   # 'All' + each account (id in userData)
        self._source.addItem("All sources", None)

        apply_btn = QPushButton("Apply")
        apply_btn.clicked.connect(self.refresh)

        bar.addWidget(QLabel("From")); bar.addWidget(self._start)
        bar.addWidget(QLabel("To")); bar.addWidget(self._end)
        bar.addWidget(QLabel("By")); bar.addWidget(self._granularity)
        bar.addWidget(self._category)
        bar.addWidget(self._sub_category)
        bar.addWidget(self._source)
        bar.addWidget(apply_btn)
        bar.addStretch(1)
        return bar

    # --- filter state --------------------------------------------------------

    def reset_filter_bounds(self) -> None:
        lo, hi = analytics.date_bounds(self._db)
        lo = lo or date.today()
        hi = hi or date.today()
        self._start.setDate(QDate(lo.year, lo.month, lo.day))
        self._end.setDate(QDate(hi.year, hi.month, hi.day))
        self._reload_categories()
        self._reload_sources()

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
            account_ids=account_ids,
        )

    def granularity(self) -> str:
        return self._granularity.currentText()

    # --- rendering -----------------------------------------------------------

    def build_html(self) -> str:
        """Build the dashboard's combined chart HTML for the current filter."""
        flt = self.current_filter()
        gran = self.granularity()
        tmpl = "plotly_dark" if self._dark else "plotly_white"
        figs = [
            charts.income_expense_line(analytics.income_expense_savings(self._db, flt, gran), tmpl),
            charts.spend_stacked_bar(analytics.spend_by_period_subcategory(self._db, flt, gran), gran, tmpl),
            charts.category_pie(analytics.subcategory_totals(self._db, flt, "Expense"), "Expense share by sub-category", tmpl),
            charts.top_merchants_bar(analytics.top_merchants(self._db, flt), tmpl),
        ]
        return charts.dashboard_html(figs, dark=self._dark)

    def set_dark(self, dark: bool) -> None:
        """Switch chart theme to match the app; re-render."""
        self._dark = dark
        self.refresh()

    def refresh(self) -> None:
        html = self.build_html()
        self._render(html)
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

    def _render(self, html: str) -> None:
        """Load chart HTML into the web view.

        QWebEngineView.setHtml() caps content at ~2 MB (it encodes to a data:
        URL), and our inline-Plotly page is far larger, so we write it to a temp
        file and load it by file URL instead — no size limit.
        """
        if not hasattr(self._web, "setUrl"):
            return
        if self._html_file is None:
            fd, path = tempfile.mkstemp(prefix="kosha_dashboard_", suffix=".html")
            os.close(fd)
            self._html_file = Path(path)
        self._html_file.write_text(html, encoding="utf-8")
        self._web.setUrl(QUrl.fromLocalFile(str(self._html_file)))

    def _load_table(self) -> None:
        rows = analytics.transactions(self._db, self.current_filter())
        self._table.setRowCount(len(rows))
        for r, (txn_date, desc, amount, direction, txn_type, source, category, sub) in enumerate(rows):
            cells = [
                txn_date, desc, format_inr(amount), direction, txn_type or "",
                source or "", categorization.display_label(category), sub or "",
            ]
            for c, val in enumerate(cells):
                item = QTableWidgetItem(str(val))
                if c == 2:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self._table.setItem(r, c, item)
