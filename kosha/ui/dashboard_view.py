"""Dashboard: filter controls, Plotly charts, and a drill-down table.

The web view (QWebEngineView) is created defensively — on machines where the
Chromium backend can't start we fall back to a message label so the rest of the
app still works. Chart/data construction is separated into ``build_html`` and
the analytics calls so it can be tested without rendering.
"""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDateEdit, QHBoxLayout, QLabel, QPushButton,
    QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .. import analytics, charts
from ..db import Database


class DashboardView(QWidget):
    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self._db = db
        self._web = None
        self._build()
        self.reset_filter_bounds()
        self.refresh()

    # --- construction --------------------------------------------------------

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.addLayout(self._build_filter_bar())

        splitter = QSplitter(Qt.Vertical)

        self._web = self._make_web_view()
        splitter.addWidget(self._web)

        self._table = QTableWidget(0, 6)
        self._table.setHorizontalHeaderLabels(
            ["Date", "Description", "Amount", "Dir", "Type", "Category"]
        )
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setStretchLastSection(True)
        splitter.addWidget(self._table)
        splitter.setSizes([420, 220])

        root.addWidget(splitter, stretch=1)

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

        self._category = QComboBox()   # 'All' + each category
        self._category.addItem("All categories")

        apply_btn = QPushButton("Apply")
        apply_btn.clicked.connect(self.refresh)

        bar.addWidget(QLabel("From")); bar.addWidget(self._start)
        bar.addWidget(QLabel("To")); bar.addWidget(self._end)
        bar.addWidget(QLabel("By")); bar.addWidget(self._granularity)
        bar.addWidget(self._category)
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

    def _reload_categories(self) -> None:
        current = self._category.currentText()
        self._category.blockSignals(True)
        self._category.clear()
        self._category.addItem("All categories")
        for cat, _total, _n in analytics.category_totals(self._db, analytics.Filter()):
            self._category.addItem(cat)
        idx = self._category.findText(current)
        self._category.setCurrentIndex(idx if idx >= 0 else 0)
        self._category.blockSignals(False)

    def current_filter(self) -> analytics.Filter:
        s = self._start.date(); e = self._end.date()
        categories: tuple[str, ...] = ()
        if self._category.currentIndex() > 0:
            categories = (self._category.currentText(),)
        return analytics.Filter(
            start=date(s.year(), s.month(), s.day()),
            end=date(e.year(), e.month(), e.day()),
            categories=categories,
        )

    def granularity(self) -> str:
        return self._granularity.currentText()

    # --- rendering -----------------------------------------------------------

    def build_html(self) -> str:
        """Build the dashboard's combined chart HTML for the current filter."""
        flt = self.current_filter()
        gran = self.granularity()
        figs = [
            charts.spend_stacked_bar(analytics.spend_by_period_category(self._db, flt, gran), gran),
            charts.income_expense_line(analytics.income_expense_savings(self._db, flt, gran)),
            charts.category_pie(analytics.category_totals(self._db, flt)),
            charts.top_merchants_bar(analytics.top_merchants(self._db, flt)),
        ]
        return charts.dashboard_html(figs)

    def refresh(self) -> None:
        html = self.build_html()
        if hasattr(self._web, "setHtml"):
            self._web.setHtml(html)
        self._load_table()

    def _load_table(self) -> None:
        rows = analytics.transactions(self._db, self.current_filter())
        self._table.setRowCount(len(rows))
        for r, (txn_date, desc, amount, direction, txn_type, _kw, category) in enumerate(rows):
            cells = [
                txn_date, desc, f"{amount:,.2f}", direction, txn_type or "", category,
            ]
            for c, val in enumerate(cells):
                item = QTableWidgetItem(str(val))
                if c == 2:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self._table.setItem(r, c, item)
