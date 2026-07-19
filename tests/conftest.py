"""Shared test setup. Forces Qt to run headless before any QApplication."""

from __future__ import annotations

import os

# Must be set before PySide6 creates a QApplication anywhere in the suite.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app
