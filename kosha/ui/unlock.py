"""Master-password dialog: create the vault on first run, unlock thereafter."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QVBoxLayout,
)

from ..db import Database, WrongPasswordError
from . import theme

MIN_PASSWORD_LEN = 8


class UnlockDialog(QDialog):
    """Blocking dialog that leaves ``db`` unlocked on accept.

    Shows a create-with-confirm form when no vault exists yet, otherwise a
    single password field with inline retry.
    """

    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self._db = db
        self._creating = not db.exists
        self.setWindowTitle("Kosha — Create Vault" if self._creating else "Kosha — Unlock")
        self.setMinimumWidth(360)
        self._build()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        theme.force_light_titlebar(self)   # keep the title bar light under OS dark mode

    def _build(self) -> None:
        layout = QVBoxLayout(self)

        intro = (
            "Choose a master password. It cannot be recovered — there is no "
            "cloud and no reset."
            if self._creating else
            "Enter your master password to unlock the vault."
        )
        label = QLabel(intro)
        label.setWordWrap(True)
        layout.addWidget(label)

        form = QFormLayout()
        self._pw = QLineEdit(); self._pw.setEchoMode(QLineEdit.Password)
        form.addRow("Password:", self._pw)
        if self._creating:
            self._confirm = QLineEdit(); self._confirm.setEchoMode(QLineEdit.Password)
            form.addRow("Confirm:", self._confirm)
        layout.addLayout(form)

        self._error = QLabel(""); self._error.setStyleSheet("color: #c0392b;")
        self._error.setWordWrap(True)
        layout.addWidget(self._error)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._pw.setFocus()

    def _fail(self, message: str) -> None:
        self._error.setText(message)
        self._pw.selectAll(); self._pw.setFocus()

    def _on_accept(self) -> None:
        pw = self._pw.text()
        if self._creating:
            if len(pw) < MIN_PASSWORD_LEN:
                return self._fail(f"Password must be at least {MIN_PASSWORD_LEN} characters.")
            if pw != self._confirm.text():
                return self._fail("Passwords do not match.")
            self._db.create(pw)
            self.accept()
            return
        try:
            self._db.unlock(pw)
        except WrongPasswordError:
            return self._fail("Incorrect password. Try again.")
        self.accept()
