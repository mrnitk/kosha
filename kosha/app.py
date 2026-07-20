"""GUI entry point: unlock the vault, then show the main window.

Run with:  python -m kosha
"""

from __future__ import annotations

import sys

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from .db import Database
from .ui import theme
from .ui.main_window import MainWindow
from .ui.unlock import UnlockDialog


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Kosha")
    app.setOrganizationName("Kosha")

    # Apply the saved theme before any window so the unlock dialog matches too.
    theme.apply_theme(app, QSettings("Kosha", "Kosha").value("theme", "system"))

    db = Database()
    dialog = UnlockDialog(db)
    if dialog.exec() != UnlockDialog.Accepted:
        return 0  # user cancelled the unlock

    window = MainWindow(db)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
