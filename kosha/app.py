"""GUI entry point: unlock the vault, then show the main window.

Run with:  python -m kosha
"""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from .db import Database
from .ui.main_window import MainWindow
from .ui.unlock import UnlockDialog


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Kosha")

    db = Database()
    dialog = UnlockDialog(db)
    if dialog.exec() != UnlockDialog.Accepted:
        return 0  # user cancelled the unlock

    window = MainWindow(db)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
