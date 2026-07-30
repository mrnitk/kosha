"""Master-password dialog: create the vault on first run, unlock thereafter.

Security behaviour:

* wrong attempts are counted in a file beside the vault, and after a few tries an
  escalating delay is enforced (and survives restarting the app), so guessing the
  master password is slow;
* creating a vault shows a strength meter and requires a reasonable length,
  because this password is the only thing protecting the data.
"""

from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit,
    QMessageBox, QProgressBar, QVBoxLayout,
)

from .. import config
from ..db import Database, WrongPasswordError
from ..security import MIN_PASSWORD_LEN, AttemptTracker, password_strength
from . import theme


class UnlockDialog(QDialog):
    """Blocking dialog that leaves ``db`` unlocked on accept.

    Shows a create-with-confirm form when no vault exists yet, otherwise a
    single password field with inline retry.
    """

    def __init__(self, db: Database, parent=None, params=None):
        """``params`` overrides the Argon2 cost when creating a vault (tests)."""
        super().__init__(parent)
        self._db = db
        self._params = params
        self._creating = not db.exists
        self._tracker = AttemptTracker(_attempts_path(db))
        self.setWindowTitle("Kosha — Create Vault" if self._creating else "Kosha — Unlock")
        self.setMinimumWidth(400)
        self._build()
        self._countdown = QTimer(self)
        self._countdown.setInterval(1000)
        self._countdown.timeout.connect(self._tick)
        self._apply_lockout()

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
        self._pw.returnPressed.connect(self._on_accept)
        form.addRow("Password:", self._pw)
        if self._creating:
            self._pw.textChanged.connect(self._update_strength)
            self._confirm = QLineEdit(); self._confirm.setEchoMode(QLineEdit.Password)
            self._confirm.textChanged.connect(self._update_strength)
            self._confirm.returnPressed.connect(self._on_accept)
            form.addRow("Confirm:", self._confirm)
        layout.addLayout(form)

        if self._creating:
            self._meter = QProgressBar()
            self._meter.setRange(0, 4); self._meter.setTextVisible(False)
            self._meter.setFixedHeight(8)
            layout.addWidget(self._meter)
            self._feedback = QLabel(" ")
            self._feedback.setWordWrap(True)
            layout.addWidget(self._feedback)

        self._show = QCheckBox("Show password")
        self._show.toggled.connect(self._on_show_toggled)
        layout.addWidget(self._show)

        self._error = QLabel(""); self._error.setStyleSheet("color: #c0392b;")
        self._error.setWordWrap(True)
        layout.addWidget(self._error)

        self._buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self._buttons.accepted.connect(self._on_accept)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)
        self._pw.setFocus()
        if self._creating:
            self._update_strength()

    def _on_show_toggled(self, on: bool) -> None:
        mode = QLineEdit.Normal if on else QLineEdit.Password
        self._pw.setEchoMode(mode)
        if self._creating:
            self._confirm.setEchoMode(mode)

    def _update_strength(self) -> None:
        score, label, hint = password_strength(self._pw.text())
        self._meter.setValue(score)
        colours = {0: "#c0392b", 1: "#c0392b", 2: "#d68910", 3: "#7d9a2f", 4: "#1a7f37"}
        self._meter.setStyleSheet(
            "QProgressBar{background:#eee;border:none;}"
            f"QProgressBar::chunk{{background:{colours.get(score, '#ccc')};}}")
        text = f"<b>{label}</b>"
        if hint:
            text += f" — {hint}"
        if self._confirm.text() and self._pw.text() != self._confirm.text():
            text += " · <span style='color:#c0392b'>passwords don't match</span>"
        self._feedback.setText(text)

    # --- lockout -------------------------------------------------------------

    def _apply_lockout(self) -> None:
        """Disable entry while a backoff delay from earlier failures is pending."""
        remaining = self._tracker.seconds_remaining()
        ok = self._buttons.button(QDialogButtonBox.Ok)
        if remaining > 0:
            ok.setEnabled(False)
            self._pw.setEnabled(False)
            self._error.setText(
                f"Too many incorrect attempts ({self._tracker.failures}). "
                f"Try again in {remaining}s.")
            if not self._countdown.isActive():
                self._countdown.start()
        else:
            ok.setEnabled(True)
            self._pw.setEnabled(True)
            self._countdown.stop()

    def _tick(self) -> None:
        remaining = self._tracker.seconds_remaining()
        if remaining > 0:
            self._error.setText(
                f"Too many incorrect attempts ({self._tracker.failures}). "
                f"Try again in {remaining}s.")
        else:
            self._countdown.stop()
            self._error.setText("")
            self._buttons.button(QDialogButtonBox.Ok).setEnabled(True)
            self._pw.setEnabled(True)
            self._pw.setFocus()

    def _fail(self, message: str) -> None:
        self._error.setText(message)
        self._pw.selectAll(); self._pw.setFocus()

    # --- accept --------------------------------------------------------------

    def _on_accept(self) -> None:
        if self._tracker.seconds_remaining() > 0:
            self._apply_lockout()
            return
        pw = self._pw.text()
        if self._creating:
            if len(pw) < MIN_PASSWORD_LEN:
                return self._fail(f"Password must be at least {MIN_PASSWORD_LEN} characters.")
            if pw != self._confirm.text():
                return self._fail("Passwords do not match.")
            score, _label, _hint = password_strength(pw)
            if score <= 1 and QMessageBox.warning(
                self, "Weak password",
                "That password is weak and would be quick to guess. There is no "
                "recovery if you lose it, and no second line of defence if it's "
                "guessed.\n\nUse it anyway?",
                QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel) != QMessageBox.Yes:
                return
            self._db.create(pw, params=self._params)
            self._tracker.record_success()
            self.accept()
            return
        try:
            self._db.unlock(pw)
        except WrongPasswordError:
            delay = self._tracker.record_failure()
            if delay:
                self._apply_lockout()
            else:
                self._fail("Incorrect password. Try again.")
            return
        self._tracker.record_success()
        self.accept()


def _attempts_path(db: Database):
    """Where to keep the failed-attempt counter (beside the vault)."""
    try:
        return db.db_path.with_name("kosha.attempts")
    except Exception:
        return config.data_dir() / "kosha.attempts"
