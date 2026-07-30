"""Net-worth section: holdings, dated value updates, and the wealth dashboard.

Three sub-tabs mirror how the user tracked this in a spreadsheet:

    Holdings       — assets, liabilities and insurance policies (the rows)
    Update values  — record every holding's value on a date (a new column)
    Net worth      — trends, allocations and gains derived from those snapshots

Everything is manual input; nothing here touches the internet.
"""

from __future__ import annotations

import tempfile
from datetime import date
from pathlib import Path

from PySide6.QtCore import QDate, Qt, QUrl, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QDateEdit, QDoubleSpinBox, QHBoxLayout, QLabel,
    QMessageBox, QPushButton, QSplitter, QTabWidget, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from .. import charts, wealth
from ..db import Database
from ..format import format_inr
from .uihelp import autosize as _autosize, fit_columns as _fit_columns
from .wealth_dialogs import AssetDialog, InsuranceDialog, LiabilityDialog

_ASSET_HEADERS = ["Name", "Category", "Type", "Liquidity", "Owner",
                  "Invested", "Current value", "Gain", "Active"]
_LIAB_HEADERS = ["Name", "Kind", "Owner", "Principal", "Rate", "EMI",
                 "Outstanding", "Active"]
_INS_HEADERS = ["Policy", "Kind", "Owner", "Premium/year", "Coverage"]


class WealthView(QWidget):
    """The Net worth section (holdings + updates + dashboard)."""

    changed = Signal()

    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self._db = db
        self._web = None
        self._assets_dir: Path | None = None
        self._html_file: Path | None = None
        self._plotlyjs = "inline"
        self._build()
        self._setup_assets()
        self.refresh()

    # --- construction --------------------------------------------------------

    def _build(self) -> None:
        root = QVBoxLayout(self)
        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_holdings_tab(), "Holdings")
        self._tabs.addTab(self._build_update_tab(), "Update values")
        self._tabs.addTab(self._build_dashboard_tab(), "Net worth")
        self._tabs.currentChanged.connect(self._on_subtab_changed)
        root.addWidget(self._tabs)

    def _build_holdings_tab(self) -> QWidget:
        page = QWidget(); outer = QVBoxLayout(page)
        split = QSplitter(Qt.Vertical)

        # Assets
        assets_box = QWidget(); al = QVBoxLayout(assets_box); al.setContentsMargins(0, 0, 0, 0)
        al.addWidget(QLabel("<b>Assets</b> — where your money sits"))
        self._assets_table = _table(_ASSET_HEADERS, stretch_col=0)
        self._assets_table.doubleClicked.connect(self._on_edit_asset)
        al.addWidget(self._assets_table)
        al.addLayout(_button_row([
            ("Add asset", self._on_add_asset),
            ("Edit", self._on_edit_asset),
            ("Delete", self._on_delete_asset),
        ]))
        split.addWidget(assets_box)

        # Liabilities
        liab_box = QWidget(); ll = QVBoxLayout(liab_box); ll.setContentsMargins(0, 0, 0, 0)
        ll.addWidget(QLabel("<b>Liabilities</b> — loans and EMIs (subtracted from net worth)"))
        self._liab_table = _table(_LIAB_HEADERS, stretch_col=0)
        self._liab_table.doubleClicked.connect(self._on_edit_liability)
        ll.addWidget(self._liab_table)
        ll.addLayout(_button_row([
            ("Add liability", self._on_add_liability),
            ("Edit", self._on_edit_liability),
            ("Delete", self._on_delete_liability),
        ]))
        split.addWidget(liab_box)

        # Insurance
        ins_box = QWidget(); il = QVBoxLayout(ins_box); il.setContentsMargins(0, 0, 0, 0)
        self._ins_label = QLabel("<b>Insurance</b> — cover and premiums (not counted in net worth)")
        il.addWidget(self._ins_label)
        self._ins_table = _table(_INS_HEADERS, stretch_col=0)
        il.addWidget(self._ins_table)
        il.addLayout(_button_row([
            ("Add policy", self._on_add_insurance),
            ("Delete", self._on_delete_insurance),
        ]))
        split.addWidget(ins_box)

        split.setSizes([320, 200, 160])
        outer.addWidget(split)
        return page

    def _build_update_tab(self) -> QWidget:
        page = QWidget(); outer = QVBoxLayout(page)
        outer.addWidget(QLabel(
            "<b>Record a snapshot</b> — set each holding's value on a date. Values are "
            "pre-filled with the last known figure, so you only change what moved."))

        bar = QHBoxLayout()
        bar.addWidget(QLabel("As of:"))
        self._as_of = QDateEdit(); self._as_of.setCalendarPopup(True)
        self._as_of.setDisplayFormat("yyyy-MM-dd")
        self._as_of.setDate(QDate.currentDate())
        self._as_of.dateChanged.connect(self._load_update_grid)
        bar.addWidget(self._as_of)
        reload_btn = QPushButton("Reload values")
        reload_btn.setToolTip("Re-fill the grid from the last known values on or before this date")
        reload_btn.clicked.connect(self._load_update_grid)
        bar.addWidget(reload_btn)
        bar.addStretch(1)
        self._update_status = QLabel("")
        bar.addWidget(self._update_status)
        outer.addLayout(bar)

        self._update_table = QTableWidget(0, 4)
        self._update_table.setHorizontalHeaderLabels(["Holding", "Kind", "Previous", "New value"])
        self._update_table.verticalHeader().setVisible(False)
        _fit_columns(self._update_table, stretch_col=0)
        outer.addWidget(self._update_table, stretch=1)

        save_row = QHBoxLayout(); save_row.addStretch(1)
        self._save_btn = QPushButton("Save snapshot")
        self._save_btn.clicked.connect(self._on_save_snapshot)
        save_row.addWidget(self._save_btn)
        delete_snap = QPushButton("Delete this snapshot")
        delete_snap.clicked.connect(self._on_delete_snapshot)
        save_row.addWidget(delete_snap)
        outer.addLayout(save_row)
        return page

    def _build_dashboard_tab(self) -> QWidget:
        page = QWidget(); outer = QVBoxLayout(page)

        self._headline = QLabel("—")
        self._headline.setTextFormat(Qt.RichText)
        self._headline.setWordWrap(True)
        outer.addWidget(self._headline)

        split = QSplitter(Qt.Vertical)
        self._web = self._make_web_view()
        split.addWidget(self._web)

        matrix_box = QWidget(); ml = QVBoxLayout(matrix_box); ml.setContentsMargins(0, 0, 0, 0)
        ml.addWidget(QLabel("<b>Snapshot history</b> — holdings by date (liabilities negative)"))
        self._matrix = QTableWidget(0, 1)
        self._matrix.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._matrix.verticalHeader().setVisible(False)
        ml.addWidget(self._matrix)
        split.addWidget(matrix_box)
        split.setSizes([460, 240])
        outer.addWidget(split, stretch=1)
        return page

    def _make_web_view(self):
        try:
            from PySide6.QtWebEngineWidgets import QWebEngineView
            return QWebEngineView()
        except Exception:
            label = QLabel("Charts unavailable: QtWebEngine could not start on this system.")
            label.setAlignment(Qt.AlignCenter)
            label.setWordWrap(True)
            return label

    def _setup_assets(self) -> None:
        """Cache plotly.min.js once so each re-render writes only a small page."""
        if not hasattr(self._web, "setUrl"):
            return
        try:
            self._assets_dir = Path(tempfile.mkdtemp(prefix="kosha_wealth_"))
            self._html_file = self._assets_dir / "wealth.html"
            self._plotlyjs = charts.write_plotlyjs(self._assets_dir)
        except Exception:
            self._plotlyjs = "inline"

    # --- data flow -----------------------------------------------------------

    def refresh(self) -> None:
        self._load_assets()
        self._load_liabilities()
        self._load_insurance()
        self._load_update_grid()
        self._load_dashboard()

    def _on_subtab_changed(self, _index: int) -> None:
        # The dashboard is the expensive one; rebuild it when it comes forward.
        if self._tabs.currentIndex() == 2:
            self._load_dashboard()

    def _load_assets(self) -> None:
        self._assets = wealth.list_assets(self._db)
        values, _ = wealth.latest_values(self._db)
        t = self._assets_table
        t.setRowCount(len(self._assets))
        for r, a in enumerate(self._assets):
            current = values.get(a.id, 0.0)
            gain = current - a.invested if a.invested else 0.0
            cells = [a.name, a.category, a.asset_type, a.liquidity, a.owner,
                     format_inr(a.invested), format_inr(current),
                     format_inr(gain) if a.invested else "",
                     "yes" if a.is_active else ""]
            for c, text in enumerate(cells):
                item = QTableWidgetItem(str(text))
                if c in (5, 6, 7):
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                elif c == 8:
                    item.setTextAlignment(Qt.AlignCenter)
                if c == 0:
                    item.setData(Qt.UserRole, a.id)
                t.setItem(r, c, item)
        _autosize(t)

    def _load_liabilities(self) -> None:
        self._liabilities = wealth.list_liabilities(self._db)
        _, outstanding = wealth.latest_values(self._db)
        t = self._liab_table
        t.setRowCount(len(self._liabilities))
        for r, l in enumerate(self._liabilities):
            cells = [l.name, l.kind, l.owner, format_inr(l.principal),
                     f"{l.interest_rate:.2f}%" if l.interest_rate else "",
                     format_inr(l.emi_amount), format_inr(outstanding.get(l.id, 0.0)),
                     "yes" if l.is_active else ""]
            for c, text in enumerate(cells):
                item = QTableWidgetItem(str(text))
                if c in (3, 5, 6):
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                elif c in (4, 7):
                    item.setTextAlignment(Qt.AlignCenter)
                if c == 0:
                    item.setData(Qt.UserRole, l.id)
                t.setItem(r, c, item)
        _autosize(t)

    def _load_insurance(self) -> None:
        self._insurance = wealth.list_insurance(self._db)
        premium, coverage = wealth.insurance_summary(self._db)
        self._ins_label.setText(
            "<b>Insurance</b> — not counted in net worth · "
            f"premium ₹{format_inr(premium)}/yr · cover ₹{format_inr(coverage)}")
        t = self._ins_table
        t.setRowCount(len(self._insurance))
        for r, p in enumerate(self._insurance):
            cells = [p.name, p.kind or "", p.owner,
                     format_inr(p.premium_per_year), format_inr(p.coverage)]
            for c, text in enumerate(cells):
                item = QTableWidgetItem(str(text))
                if c in (3, 4):
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if c == 0:
                    item.setData(Qt.UserRole, p.id)
                t.setItem(r, c, item)
        _autosize(t)

    # --- holdings actions ----------------------------------------------------

    def _selected_id(self, table: QTableWidget):
        rows = {i.row() for i in table.selectedItems()}
        if len(rows) != 1:
            return None
        item = table.item(next(iter(rows)), 0)
        return item.data(Qt.UserRole) if item else None

    def _on_add_asset(self) -> None:
        dlg = AssetDialog(self._db, parent=self)
        dlg.exec()
        if dlg.saved:
            self.refresh(); self.changed.emit()

    def _on_edit_asset(self, *_a) -> None:
        aid = self._selected_id(self._assets_table)
        if aid is None:
            return
        asset = next((a for a in self._assets if a.id == aid), None)
        dlg = AssetDialog(self._db, asset, parent=self)
        dlg.exec()
        if dlg.saved:
            self.refresh(); self.changed.emit()

    def _on_delete_asset(self) -> None:
        aid = self._selected_id(self._assets_table)
        if aid is None:
            return
        asset = next((a for a in self._assets if a.id == aid), None)
        if QMessageBox.warning(
            self, "Delete asset",
            f"Delete “{asset.name}” and its entire value history?\n\n"
            "To keep the history, edit it and uncheck Active instead.",
            QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel) != QMessageBox.Yes:
            return
        wealth.delete_asset(self._db, aid)
        self.refresh(); self.changed.emit()

    def _on_add_liability(self) -> None:
        dlg = LiabilityDialog(self._db, parent=self)
        dlg.exec()
        if dlg.saved:
            self.refresh(); self.changed.emit()

    def _on_edit_liability(self, *_a) -> None:
        lid = self._selected_id(self._liab_table)
        if lid is None:
            return
        liab = next((l for l in self._liabilities if l.id == lid), None)
        dlg = LiabilityDialog(self._db, liab, parent=self)
        dlg.exec()
        if dlg.saved:
            self.refresh(); self.changed.emit()

    def _on_delete_liability(self) -> None:
        lid = self._selected_id(self._liab_table)
        if lid is None:
            return
        if QMessageBox.warning(
            self, "Delete liability", "Delete this liability and its history?",
            QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel) != QMessageBox.Yes:
            return
        wealth.delete_liability(self._db, lid)
        self.refresh(); self.changed.emit()

    def _on_add_insurance(self) -> None:
        dlg = InsuranceDialog(self._db, parent=self)
        dlg.exec()
        if dlg.saved:
            self.refresh()

    def _on_delete_insurance(self) -> None:
        iid = self._selected_id(self._ins_table)
        if iid is None:
            return
        wealth.delete_insurance(self._db, iid)
        self.refresh()

    # --- snapshot entry ------------------------------------------------------

    def _current_as_of(self) -> date:
        d = self._as_of.date()
        return date(d.year(), d.month(), d.day())

    def _load_update_grid(self) -> None:
        """Fill the grid with each holding and its last known value."""
        as_of = self._current_as_of()
        assets = wealth.list_assets(self._db, active_only=True)
        liabs = wealth.list_liabilities(self._db, active_only=True)
        prev_assets, prev_liabs = wealth.latest_values(self._db, as_of)

        t = self._update_table
        t.setRowCount(len(assets) + len(liabs))
        self._update_rows: list[tuple[str, int]] = []
        row = 0
        for a in assets:
            self._add_update_row(row, a.name, "Asset", prev_assets.get(a.id, 0.0))
            self._update_rows.append(("asset", a.id))
            row += 1
        for l in liabs:
            self._add_update_row(row, l.name, "Liability", prev_liabs.get(l.id, 0.0))
            self._update_rows.append(("liability", l.id))
            row += 1
        _autosize(t)

        existing = wealth.snapshot_dates(self._db)
        iso = as_of.isoformat()
        if iso in existing:
            self._update_status.setText(
                f"<span style='color:#b8860b'>A snapshot already exists for {iso} — "
                "saving will replace it.</span>")
        elif existing:
            self._update_status.setText(f"Last snapshot: {existing[-1]}")
        else:
            self._update_status.setText("No snapshots yet — this will be the first.")

    def _add_update_row(self, row: int, name: str, kind: str, previous: float) -> None:
        t = self._update_table
        name_item = QTableWidgetItem(name)
        name_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        t.setItem(row, 0, name_item)
        kind_item = QTableWidgetItem(kind)
        kind_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        kind_item.setTextAlignment(Qt.AlignCenter)
        t.setItem(row, 1, kind_item)
        prev_item = QTableWidgetItem(format_inr(previous))
        prev_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        prev_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
        t.setItem(row, 2, prev_item)

        spin = QDoubleSpinBox()
        spin.setRange(0.0, 10_000_000_000.0)
        spin.setDecimals(2)
        spin.setGroupSeparatorShown(True)
        spin.setValue(previous or 0.0)
        t.setCellWidget(row, 3, spin)

    def _on_save_snapshot(self) -> None:
        if not getattr(self, "_update_rows", None):
            QMessageBox.information(self, "Nothing to record",
                                    "Add some assets or liabilities first.")
            return
        asset_values: dict[int, float] = {}
        liab_values: dict[int, float] = {}
        for row, (kind, holding_id) in enumerate(self._update_rows):
            spin = self._update_table.cellWidget(row, 3)
            if spin is None:
                continue
            if kind == "asset":
                asset_values[holding_id] = spin.value()
            else:
                liab_values[holding_id] = spin.value()
        wealth.record_snapshot(self._db, self._current_as_of(), asset_values, liab_values)
        self.refresh()
        self.changed.emit()
        point = wealth.current_networth(self._db)
        QMessageBox.information(
            self, "Snapshot saved",
            f"Recorded {len(asset_values)} asset(s) and {len(liab_values)} liability(ies) "
            f"for {self._current_as_of().isoformat()}.\n\n"
            f"Net worth: ₹{format_inr(point.net_worth)}")

    def _on_delete_snapshot(self) -> None:
        as_of = self._current_as_of()
        if as_of.isoformat() not in wealth.snapshot_dates(self._db):
            QMessageBox.information(self, "No snapshot",
                                    f"There's no snapshot recorded for {as_of.isoformat()}.")
            return
        if QMessageBox.warning(
            self, "Delete snapshot",
            f"Delete all values recorded on {as_of.isoformat()}?",
            QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel) != QMessageBox.Yes:
            return
        wealth.delete_snapshot(self._db, as_of)
        self.refresh()
        self.changed.emit()

    # --- dashboard -----------------------------------------------------------

    def _load_dashboard(self) -> None:
        self._load_headline()
        self._load_matrix()
        self._render(self.build_html())

    def _load_headline(self) -> None:
        point = wealth.current_networth(self._db)
        total_growth = wealth.total_growth_pct(self._db)
        emi = wealth.monthly_obligations(self._db)
        ratio = wealth.debt_to_asset(self._db)
        if not point.as_of:
            self._headline.setText(
                "<h3>No snapshots yet</h3>Add holdings, then record values on the "
                "<b>Update values</b> tab to start tracking net worth.")
            return
        bits = [
            f"<h2 style='margin:2px 0'>Net worth: ₹{format_inr(point.net_worth)}</h2>",
            f"<span style='color:gray'>as of {point.as_of}</span> &nbsp;·&nbsp; "
            f"Assets ₹{format_inr(point.assets)}",
        ]
        if point.liabilities:
            bits.append(f" &nbsp;·&nbsp; Liabilities ₹{format_inr(point.liabilities)}")
        if point.growth_pct is not None:
            colour = "#1a7f37" if point.growth_pct >= 0 else "#c0392b"
            bits.append(f" &nbsp;·&nbsp; <span style='color:{colour}'>"
                        f"{point.growth_pct:+.1f}% vs previous</span>")
        if total_growth is not None:
            bits.append(f" &nbsp;·&nbsp; {total_growth:+.1f}% overall")
        if emi:
            bits.append(f"<br>Monthly EMI ₹{format_inr(emi)}")
        if ratio:
            bits.append(f" &nbsp;·&nbsp; Debt/assets {ratio:.1f}%")
        self._headline.setText("".join(bits))

    def _load_matrix(self) -> None:
        dates, rows = wealth.snapshot_matrix(self._db)
        t = self._matrix
        t.clear()
        t.setColumnCount(1 + len(dates))
        t.setHorizontalHeaderLabels(["Holding"] + dates)
        t.setRowCount(len(rows))
        for r, (name, values) in enumerate(rows):
            t.setItem(r, 0, QTableWidgetItem(name))
            for c, value in enumerate(values, start=1):
                item = QTableWidgetItem("" if value is None else format_inr(value))
                item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                t.setItem(r, c, item)
        _fit_columns(t, stretch_col=0)
        _autosize(t)

    def build_html(self) -> str:
        """Chart page for the wealth dashboard."""
        series = wealth.networth_series(self._db)
        figs = []
        if series:
            figs.append(charts.networth_trend(series))
        for dimension, title in (("liquidity", "Assets by liquidity"),
                                 ("asset_type", "Assets by type"),
                                 ("owner", "Assets by owner"),
                                 ("category", "Assets by category")):
            rows = wealth.allocation(self._db, dimension)
            if rows:
                figs.append(charts.category_pie(rows, title))
        gains = wealth.invested_vs_current(self._db)
        gains = [g for g in gains if g[1]]          # only holdings with a cost basis
        if gains:
            figs.append(charts.invested_vs_current_bar(gains))
        if not figs:
            return ("<!doctype html><html><body style='font-family:system-ui;padding:16px'>"
                    "<p>No net-worth data yet — add holdings and record a snapshot.</p>"
                    "</body></html>")
        return charts.dashboard_html(figs, dark=False, plotlyjs=self._plotlyjs)

    def _render(self, html: str) -> None:
        if not hasattr(self._web, "setUrl") or self._html_file is None:
            return
        self._html_file.write_text(html, encoding="utf-8")
        self._web.setUrl(QUrl.fromLocalFile(str(self._html_file)))


# --- small helpers -----------------------------------------------------------

def _table(headers: list[str], stretch_col: int = 0) -> QTableWidget:
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.setEditTriggers(QAbstractItemView.NoEditTriggers)
    t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.setSelectionMode(QAbstractItemView.SingleSelection)
    t.verticalHeader().setVisible(False)
    _fit_columns(t, stretch_col=stretch_col)
    return t


def _button_row(buttons) -> QHBoxLayout:
    row = QHBoxLayout()
    for label, handler in buttons:
        btn = QPushButton(label)
        btn.clicked.connect(handler)
        row.addWidget(btn)
    row.addStretch(1)
    return row
