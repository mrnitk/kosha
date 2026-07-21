"""Force a consistent light theme, independent of the OS colour scheme.

Kosha always renders light — there is no dark/system option. Fusion is used with
its standard (light) palette so every widget looks the same on every machine,
whatever the Windows theme is set to.
"""

from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QStyleFactory


def _light_palette() -> QPalette:
    """An explicit light palette, built by hand.

    We can't use Fusion's ``standardPalette()`` — on Qt 6.5+ it is dark-aware and
    returns a *dark* palette when Windows is in dark mode, which is exactly what
    we're overriding.
    """
    p = QPalette()
    black = QColor(0, 0, 0)
    p.setColor(QPalette.Window, QColor(240, 240, 240))
    p.setColor(QPalette.WindowText, black)
    p.setColor(QPalette.Base, QColor(255, 255, 255))
    p.setColor(QPalette.AlternateBase, QColor(245, 245, 245))
    p.setColor(QPalette.Text, black)
    p.setColor(QPalette.Button, QColor(240, 240, 240))
    p.setColor(QPalette.ButtonText, black)
    p.setColor(QPalette.ToolTipBase, QColor(255, 255, 255))
    p.setColor(QPalette.ToolTipText, black)
    p.setColor(QPalette.PlaceholderText, QColor(120, 120, 120))
    p.setColor(QPalette.Highlight, QColor(76, 120, 168))
    p.setColor(QPalette.HighlightedText, QColor(255, 255, 255))
    p.setColor(QPalette.Link, QColor(0, 90, 190))
    disabled = QColor(120, 120, 120)
    for role in (QPalette.Text, QPalette.WindowText, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, disabled)
    return p


def apply_light(app) -> None:
    """Force a light theme app-wide, whatever the OS colour scheme is.

    Pinning the style-hint colour scheme to Light (Qt 6.8+/PySide6) is what stops
    Qt from picking dark palettes and dark native title bars under Windows dark
    mode; the explicit Fusion light palette covers older/edge cases too.
    """
    try:
        from PySide6.QtCore import Qt
        app.styleHints().setColorScheme(Qt.ColorScheme.Light)
    except Exception:
        pass
    app.setStyle(QStyleFactory.create("Fusion"))
    app.setPalette(_light_palette())


def force_light_titlebar(widget) -> None:
    """Make a window's native Windows title bar light, ignoring OS dark mode.

    The title bar is non-client area drawn by Windows, so the Qt palette can't
    reach it — it follows the system theme and shows up black under Windows dark
    mode. This clears the DWM 'immersive dark mode' attribute for the window.
    No-op off Windows or if the call fails.
    """
    try:
        import ctypes
        hwnd = int(widget.winId())
        off = ctypes.c_int(0)   # 0 = light title bar
        dwm = ctypes.windll.dwmapi
        for attr in (20, 19):   # 20 = Win10 2004+, 19 = older builds
            dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(off), ctypes.sizeof(off))
    except Exception:
        pass
