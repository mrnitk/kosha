"""Light / dark theming via Qt Fusion palettes.

Fusion is used because it honours a custom QPalette consistently across widgets,
so switching the palette recolours every table, label, and control (keeping text
readable) without per-widget stylesheets.
"""

from __future__ import annotations

from PySide6.QtGui import QColor, QPalette

MODES = ("system", "light", "dark")


def _dark_palette() -> QPalette:
    p = QPalette()
    window = QColor(30, 30, 30)
    base = QColor(37, 37, 37)
    text = QColor(224, 224, 224)
    disabled = QColor(120, 120, 120)

    p.setColor(QPalette.Window, window)
    p.setColor(QPalette.WindowText, text)
    p.setColor(QPalette.Base, base)
    p.setColor(QPalette.AlternateBase, QColor(45, 45, 45))
    p.setColor(QPalette.Text, text)
    p.setColor(QPalette.Button, QColor(45, 45, 45))
    p.setColor(QPalette.ButtonText, text)
    p.setColor(QPalette.ToolTipBase, QColor(45, 45, 45))
    p.setColor(QPalette.ToolTipText, text)
    p.setColor(QPalette.PlaceholderText, QColor(150, 150, 150))
    p.setColor(QPalette.Highlight, QColor(76, 120, 168))
    p.setColor(QPalette.HighlightedText, QColor(255, 255, 255))
    p.setColor(QPalette.Link, QColor(94, 160, 220))

    for role in (QPalette.Text, QPalette.WindowText, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, disabled)
    return p


def is_system_dark(app) -> bool:
    """Best-effort detection of the OS colour scheme (Qt 6.5+)."""
    try:
        from PySide6.QtCore import Qt
        return app.styleHints().colorScheme() == Qt.ColorScheme.Dark
    except Exception:
        return False


def apply_theme(app, mode: str) -> bool:
    """Apply ``mode`` ('system'|'light'|'dark') to the app. Returns is_dark."""
    app.setStyle("Fusion")
    dark = is_system_dark(app) if mode == "system" else (mode == "dark")
    app.setPalette(_dark_palette() if dark else app.style().standardPalette())
    return dark
