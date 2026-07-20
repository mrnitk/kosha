"""Main application window: dashboard + categorization, import, and theming."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QMainWindow, QMessageBox, QTabWidget,
)

from .. import importer
from ..db import Database
from ..parsers import REGISTRY
from . import theme
from .categorization_view import CategorizationView
from .dashboard_view import DashboardView

_IMPORT_FILTER = "Statements (*.xls *.xlsx *.csv *.pdf);;All files (*)"
_IMPORT_SUFFIXES = {".xls", ".xlsx", ".csv", ".pdf"}


class MainWindow(QMainWindow):
    def __init__(self, db: Database):
        super().__init__()
        self._db = db
        self._settings = QSettings("Kosha", "Kosha")
        self.setWindowTitle("Kosha — Expense Tracker")
        self.resize(1040, 660)
        self.setAcceptDrops(True)          # drag-and-drop statement import

        self._dashboard = DashboardView(db)
        self._view = CategorizationView(db)
        self._view.changed.connect(self._update_status)
        self._view.changed.connect(self._dashboard.refresh)

        self._tabs = QTabWidget()
        self._tabs.addTab(self._dashboard, "Dashboard")
        self._tabs.addTab(self._view, "Categorize")
        self.setCentralWidget(self._tabs)

        self._build_menu()
        self._apply_saved_theme()
        self._update_status()

    # --- menus ---------------------------------------------------------------

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("&File")

        import_action = QAction("&Import statements…", self)
        import_action.setShortcut("Ctrl+I")
        import_action.triggered.connect(self._import_dialog)
        file_menu.addAction(import_action)

        file_menu.addSeparator()
        quit_action = QAction("&Quit", self)
        quit_action.setShortcut("Ctrl+Q")
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        view_menu = self.menuBar().addMenu("&View")
        theme_menu = view_menu.addMenu("&Theme")
        self._theme_group = QActionGroup(self)
        self._theme_group.setExclusive(True)
        for mode in theme.MODES:
            act = QAction(mode.capitalize(), self, checkable=True)
            act.setData(mode)
            act.triggered.connect(lambda _checked, m=mode: self._set_theme(m))
            self._theme_group.addAction(act)
            theme_menu.addAction(act)

    # --- theming -------------------------------------------------------------

    def _apply_saved_theme(self) -> None:
        mode = self._settings.value("theme", "system")
        if mode not in theme.MODES:
            mode = "system"
        for act in self._theme_group.actions():
            act.setChecked(act.data() == mode)
        self._set_theme(mode, persist=False)

    def _set_theme(self, mode: str, persist: bool = True) -> None:
        app = QApplication.instance()
        dark = theme.apply_theme(app, mode)
        self._dashboard.set_dark(dark)
        if persist:
            self._settings.setValue("theme", mode)

    # --- import --------------------------------------------------------------

    def _import_dialog(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Select statements", "", _IMPORT_FILTER)
        if paths:
            self._import_paths(paths)

    def _import_paths(self, paths: list[str]) -> None:
        try:
            result = importer.import_paths(self._db, paths)
        except Exception as exc:
            QMessageBox.critical(self, "Import failed", str(exc))
            return

        self._view.refresh()
        self._dashboard.reset_filter_bounds()
        self._dashboard.refresh()
        self._update_status()

        box = QMessageBox.warning if (result.unrecognized or result.failed) else QMessageBox.information
        box(self, "Import complete", result.summary())

    # --- drag and drop -------------------------------------------------------

    def dragEnterEvent(self, event) -> None:
        if self._dropped_statements(event):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        paths = self._dropped_statements(event)
        if paths:
            event.acceptProposedAction()
            self._import_paths(paths)

    @staticmethod
    def _dropped_statements(event) -> list[str]:
        md = event.mimeData()
        if not md.hasUrls():
            return []
        out = []
        for url in md.urls():
            if url.isLocalFile():
                p = Path(url.toLocalFile())
                if p.suffix.lower() in _IMPORT_SUFFIXES:
                    out.append(str(p))
        return out

    # --- status --------------------------------------------------------------

    def _update_status(self) -> None:
        n = self._db.connection.execute("SELECT count(*) FROM transactions").fetchone()[0]
        rules = self._db.connection.execute("SELECT count(*) FROM category_rules").fetchone()[0]
        supported = ", ".join(sorted(REGISTRY))
        self.statusBar().showMessage(f"{n} transactions · {rules} rules · parsers: {supported}")

    def closeEvent(self, event) -> None:
        self._db.lock()          # drop the encryption key from memory
        super().closeEvent(event)
