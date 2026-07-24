"""Main application window: dashboard + categorization + rules, and import."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QFileDialog, QMainWindow, QMessageBox, QTabWidget,
)

from .. import importer
from ..db import Database
from ..parsers import REGISTRY
from . import theme
from .categorization_view import CategorizationView
from .dashboard_view import DashboardView
from .recurring_view import RecurringView
from .rules_view import RulesView

_IMPORT_FILTER = "Statements (*.xls *.xlsx *.csv *.pdf);;All files (*)"
_IMPORT_SUFFIXES = {".xls", ".xlsx", ".csv", ".pdf"}


class MainWindow(QMainWindow):
    def __init__(self, db: Database):
        super().__init__()
        self._db = db
        self.setWindowTitle("Kosha — Expense Tracker")
        self.resize(1040, 660)
        self.setAcceptDrops(True)          # drag-and-drop statement import

        self._dashboard = DashboardView(db)
        self._view = CategorizationView(db)
        self._rules = RulesView(db)
        self._recurring = RecurringView(db)
        # Rebuilding the dashboard's Plotly page is ~1s, so cross-tab edits only
        # flag it dirty; it re-renders when the user actually opens it (below).
        self._view.changed.connect(self._update_status)
        self._view.changed.connect(self._dashboard.mark_dirty)
        self._view.changed.connect(self._rules.refresh)
        # Editing a rule re-resolves history, so refresh the other views.
        self._rules.changed.connect(self._dashboard.mark_dirty)
        self._rules.changed.connect(self._view.refresh)
        self._rules.changed.connect(self._update_status)
        # A per-transaction edit/delete changes totals everywhere.
        self._dashboard.changed.connect(self._view.refresh)
        self._dashboard.changed.connect(self._recurring.refresh)
        self._dashboard.changed.connect(self._update_status)

        self._tabs = QTabWidget()
        self._tabs.addTab(self._dashboard, "Dashboard")
        self._tabs.addTab(self._view, "Categorize")
        self._tabs.addTab(self._rules, "Rules")
        self._tabs.addTab(self._recurring, "Recurring")
        self._tabs.currentChanged.connect(self._on_tab_changed)
        self.setCentralWidget(self._tabs)

        self._build_menu()
        self._update_status()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        theme.force_light_titlebar(self)   # keep the title bar light under OS dark mode

    def _on_tab_changed(self, _index: int) -> None:
        # Refresh a tab's data when it's brought to the front.
        current = self._tabs.currentWidget()
        if current is self._dashboard:
            self._dashboard.refresh_if_dirty()   # ~1s Plotly rebuild — only when shown
        elif current is self._recurring:
            self._recurring.refresh()

    # --- menus ---------------------------------------------------------------

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("&File")

        import_action = QAction("&Import statements…", self)
        import_action.setShortcut("Ctrl+I")
        import_action.triggered.connect(self._import_dialog)
        file_menu.addAction(import_action)

        template_import_action = QAction("Import from &template…", self)
        template_import_action.triggered.connect(self._import_template)
        file_menu.addAction(template_import_action)

        template_dl_action = QAction("Download blank &template…", self)
        template_dl_action.triggered.connect(self._download_template)
        file_menu.addAction(template_dl_action)

        clear_action = QAction("&Clear all data…", self)
        clear_action.triggered.connect(self._clear_data_dialog)
        file_menu.addAction(clear_action)

        file_menu.addSeparator()
        backup_action = QAction("&Backup vault…", self)
        backup_action.triggered.connect(self._backup_vault)
        file_menu.addAction(backup_action)

        restore_action = QAction("&Restore from backup…", self)
        restore_action.triggered.connect(self._restore_vault)
        file_menu.addAction(restore_action)

        file_menu.addSeparator()
        quit_action = QAction("&Quit", self)
        quit_action.setShortcut("Ctrl+Q")
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

    # --- import --------------------------------------------------------------

    def _import_dialog(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Select statements", "", _IMPORT_FILTER)
        if paths:
            self._import_paths(paths)

    def _import_template(self) -> None:
        """Import a filled standard template (any bank; Source column per row)."""
        from .. import template_import
        path, _ = QFileDialog.getOpenFileName(
            self, "Select a filled template", "",
            "Template (*.xlsx *.xls *.csv);;All files (*)")
        if not path:
            return
        try:
            result = importer.import_template(self._db, Path(path))
        except template_import.TemplateError as exc:
            QMessageBox.warning(self, "Not a valid template", str(exc))
            return
        except Exception as exc:
            QMessageBox.critical(self, "Import failed", str(exc))
            return
        self._refresh_after_import()
        box = QMessageBox.warning if result.has_problems else QMessageBox.information
        box(self, "Import complete", result.summary())

    def _download_template(self) -> None:
        """Save a blank standard template for the user to fill in."""
        from .. import template_import
        path, _ = QFileDialog.getSaveFileName(
            self, "Save blank template", "Kosha_import_template.xlsx",
            "Excel workbook (*.xlsx)")
        if not path:
            return
        if not path.lower().endswith(".xlsx"):
            path += ".xlsx"
        try:
            template_import.write_template(path)
        except Exception as exc:
            QMessageBox.critical(self, "Could not save template", str(exc))
            return
        QMessageBox.information(
            self, "Template saved",
            f"Saved to:\n{path}\n\nFill in the 'Transactions' sheet, then use "
            "File ▸ Import from template.")

    def _refresh_after_import(self) -> None:
        self._view.refresh()
        self._rules.refresh()
        self._recurring.refresh()
        self._dashboard.reset_filter_bounds()
        self._dashboard.refresh()
        self._update_status()

    # --- backup / restore ----------------------------------------------------

    def _backup_vault(self) -> None:
        """Save an encrypted backup (the DB + salt) to a zip the user chooses."""
        from datetime import date as _date
        from .. import backup
        default = f"kosha-backup-{_date.today().isoformat()}.zip"
        path, _ = QFileDialog.getSaveFileName(self, "Save encrypted backup", default,
                                              "Backup archive (*.zip)")
        if not path:
            return
        if not path.lower().endswith(".zip"):
            path += ".zip"
        try:
            backup.create_backup(path, self._db.db_path, self._db.salt_path)
        except Exception as exc:
            QMessageBox.critical(self, "Backup failed", str(exc))
            return
        QMessageBox.information(
            self, "Backup saved",
            f"Encrypted backup saved to:\n{path}\n\nIt opens only with this vault's "
            "master password. Keep it somewhere safe.")

    def _restore_vault(self) -> None:
        """Replace the current vault with a backup, then close (reopen to unlock)."""
        from .. import backup
        path, _ = QFileDialog.getOpenFileName(self, "Select a backup to restore", "",
                                              "Backup archive (*.zip);;All files (*)")
        if not path:
            return
        if not backup.is_valid_backup(path):
            QMessageBox.warning(self, "Not a valid backup",
                                "That file isn't a Kosha backup (needs kosha.db and kosha.salt).")
            return
        if QMessageBox.warning(
            self, "Restore backup",
            "This REPLACES your current vault with the backup and cannot be undone.\n\n"
            "Kosha will close afterwards — reopen it and unlock with the backup's "
            "master password.\n\nContinue?",
            QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel) != QMessageBox.Yes:
            return
        try:
            self._db.lock()   # close the connection before overwriting the files
            backup.restore_backup(path, self._db.db_path, self._db.salt_path)
        except Exception as exc:
            QMessageBox.critical(self, "Restore failed", str(exc))
            return
        QMessageBox.information(self, "Restored",
                                "Vault restored. Kosha will now close — reopen it to unlock.")
        self.close()

    def _import_paths(self, paths: list[str]) -> None:
        try:
            result = importer.import_paths(self._db, paths)
        except Exception as exc:
            QMessageBox.critical(self, "Import failed", str(exc))
            return

        self._view.refresh()
        self._rules.refresh()
        self._dashboard.reset_filter_bounds()
        self._dashboard.refresh()
        self._update_status()

        box = QMessageBox.warning if (result.unrecognized or result.failed) else QMessageBox.information
        box(self, "Import complete", result.summary())

    def _clear_data_dialog(self) -> None:
        """Confirm and wipe data for a fresh start. Irreversible."""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("Clear all data")
        box.setText("This permanently deletes data from this vault and cannot be undone.")
        box.setInformativeText(
            "Delete transactions — removes all transactions and import history, but "
            "keeps your categorization rules and accounts (a re-import re-applies them).\n\n"
            "Full reset — also deletes all categorization rules and accounts."
        )
        txn_btn = box.addButton("Delete transactions", QMessageBox.DestructiveRole)
        all_btn = box.addButton("Full reset", QMessageBox.DestructiveRole)
        cancel_btn = box.addButton(QMessageBox.Cancel)
        box.setDefaultButton(cancel_btn)
        box.exec()

        clicked = box.clickedButton()
        if clicked is None or clicked is cancel_btn:
            return
        scope = "transactions" if clicked is txn_btn else "all"
        try:
            n = importer.clear_data(self._db, scope)
        except Exception as exc:
            QMessageBox.critical(self, "Clear failed", str(exc))
            return

        self._view.refresh()
        self._rules.refresh()
        self._recurring.refresh()
        self._dashboard.reset_filter_bounds()
        self._dashboard.refresh()
        self._update_status()
        QMessageBox.information(self, "Data cleared", f"Removed {n} transaction(s).")

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
