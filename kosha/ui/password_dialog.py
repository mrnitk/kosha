"""Change the master password, with a strength meter and a clear warning.

Re-keying is done by :meth:`kosha.db.Database.change_password`, which verifies the
old password and then uses SQLCipher's ``PRAGMA rekey`` — the vault is re-encrypted
in place, so existing backups still need the *old* password.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit,
    QMessageBox, QProgressBar, QVBoxLayout,
)

from ..db import Database, WrongPasswordError
from ..security import MIN_PASSWORD_LEN, password_strength


class ChangePasswordDialog(QDialog):
    """Old password + new password (twice). ``changed`` is True on success."""

    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self._db = db
        self.changed = False
        self.setWindowTitle("Change master password")
        self.setMinimumWidth(460)
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        intro = QLabel(
            "Your master password protects the whole vault. It cannot be recovered — "
            "if you forget it, the data is gone. Choose something long.")
        intro.setWordWrap(True)
        root.addWidget(intro)

        form = QFormLayout()
        self._old = QLineEdit(); self._old.setEchoMode(QLineEdit.Password)
        form.addRow("Current password:", self._old)

        self._new = QLineEdit(); self._new.setEchoMode(QLineEdit.Password)
        self._new.textChanged.connect(self._update_strength)
        form.addRow("New password:", self._new)

        self._confirm = QLineEdit(); self._confirm.setEchoMode(QLineEdit.Password)
        self._confirm.textChanged.connect(self._update_strength)
        form.addRow("Confirm new:", self._confirm)
        root.addLayout(form)

        self._meter = QProgressBar()
        self._meter.setRange(0, 4)
        self._meter.setTextVisible(False)
        self._meter.setFixedHeight(8)
        root.addWidget(self._meter)

        self._feedback = QLabel(" ")
        self._feedback.setWordWrap(True)
        root.addWidget(self._feedback)

        self._show = QCheckBox("Show passwords")
        self._show.toggled.connect(self._on_show_toggled)
        root.addWidget(self._show)

        self._error = QLabel("")
        self._error.setStyleSheet("color: #c0392b;")
        self._error.setWordWrap(True)
        root.addWidget(self._error)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Change password")
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self._old.setFocus()
        self._update_strength()

    def _on_show_toggled(self, on: bool) -> None:
        mode = QLineEdit.Normal if on else QLineEdit.Password
        for field in (self._old, self._new, self._confirm):
            field.setEchoMode(mode)

    def _update_strength(self) -> None:
        score, label, hint = password_strength(self._new.text())
        self._meter.setValue(score)
        colours = {0: "#c0392b", 1: "#c0392b", 2: "#d68910", 3: "#7d9a2f", 4: "#1a7f37"}
        self._meter.setStyleSheet(
            "QProgressBar{background:#eee;border:none;}"
            f"QProgressBar::chunk{{background:{colours.get(score, '#ccc')};}}")
        text = f"<b>{label}</b>"
        if hint:
            text += f" — {hint}"
        if self._confirm.text() and self._new.text() != self._confirm.text():
            text += " · <span style='color:#c0392b'>passwords don't match</span>"
        self._feedback.setText(text)

    def _fail(self, message: str) -> None:
        self._error.setText(message)

    def _on_accept(self) -> None:
        old, new, confirm = self._old.text(), self._new.text(), self._confirm.text()
        if not old:
            return self._fail("Enter your current password.")
        if len(new) < MIN_PASSWORD_LEN:
            return self._fail(f"New password must be at least {MIN_PASSWORD_LEN} characters.")
        if new != confirm:
            return self._fail("New passwords do not match.")
        if new == old:
            return self._fail("The new password is the same as the current one.")
        score, _label, _hint = password_strength(new)
        if score <= 1 and QMessageBox.warning(
            self, "Weak password",
            "That password is weak and would be quick to guess.\n\nUse it anyway?",
            QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel) != QMessageBox.Yes:
            return
        try:
            self._db.change_password(old, new)
        except WrongPasswordError:
            return self._fail("Current password is incorrect.")
        except Exception as exc:
            return self._fail(f"Could not change the password: {exc}")
        self.changed = True
        self.accept()
