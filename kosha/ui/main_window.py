"""Main application window: hosts the categorization review and file import."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QFileDialog, QInputDialog, QMainWindow, QMessageBox,
)

from .. import importer
from ..db import Database
from ..parsers import REGISTRY
from .categorization_view import CategorizationView


class MainWindow(QMainWindow):
    def __init__(self, db: Database):
        super().__init__()
        self._db = db
        self.setWindowTitle("Kosha — Expense Tracker")
        self.resize(1000, 640)

        self._view = CategorizationView(db)
        self._view.changed.connect(self._update_status)
        self.setCentralWidget(self._view)

        self._build_menu()
        self._update_status()

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("&File")

        import_action = QAction("&Import statement…", self)
        import_action.setShortcut("Ctrl+I")
        import_action.triggered.connect(self._import_statement)
        file_menu.addAction(import_action)

        file_menu.addSeparator()
        quit_action = QAction("&Quit", self)
        quit_action.setShortcut("Ctrl+Q")
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

    def _import_statement(self) -> None:
        path_str, _ = QFileDialog.getOpenFileName(
            self, "Select statement", "", "Statements (*.xls *.xlsx *.csv *.pdf);;All files (*)"
        )
        if not path_str:
            return
        path = Path(path_str)

        parser = self._detect_parser(path)
        if parser is None:
            QMessageBox.warning(
                self, "Unrecognized statement",
                f"No parser recognized {path.name}. Supported: "
                + ", ".join(sorted(REGISTRY)),
            )
            return

        name, ok = QInputDialog.getText(
            self, "Account", "Account name for this statement:",
            text=f"{parser.institution}",
        )
        if not ok or not name.strip():
            return

        try:
            account_id = importer.get_or_create_account(
                self._db, name.strip(), parser.account_type, parser.institution
            )
            result = importer.import_file(self._db, parser, path, account_id)
        except Exception as exc:  # surface parse/import failures to the user
            QMessageBox.critical(self, "Import failed", str(exc))
            return

        self._view.refresh()
        QMessageBox.information(self, "Import complete", str(result))

    def _detect_parser(self, path: Path):
        for parser_cls in REGISTRY.values():
            parser = parser_cls()
            try:
                if parser.can_parse(path):
                    return parser
            except Exception:
                continue
        return None

    def _update_status(self) -> None:
        n = self._db.connection.execute("SELECT count(*) FROM transactions").fetchone()[0]
        rules = self._db.connection.execute("SELECT count(*) FROM category_rules").fetchone()[0]
        self.statusBar().showMessage(f"{n} transactions · {rules} rules")

    def closeEvent(self, event) -> None:
        # Drop the encryption key from memory when the window closes.
        self._db.lock()
        super().closeEvent(event)
