# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Kosha (Windows, onedir).

onedir (not onefile) because QtWebEngine ships QtWebEngineProcess.exe plus a
tree of resources/translations that are far more reliable unpacked than
extracted to a temp dir on every launch.

Bundling risks handled explicitly:
  * kosha/schema.sql          - loaded via importlib.resources at runtime
  * sqlcipher3 (+ native DLL)  - compiled extension, collected whole
  * plotly package data        - the inlined plotly.min.js
  * PySide6 QtWebEngine        - via PyInstaller's PySide6 hooks + hidden imports
"""

from PyInstaller.utils.hooks import collect_all

datas = [("kosha/schema.sql", "kosha")]
binaries = []
hiddenimports = [
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineCore",
    "PySide6.QtPrintSupport",
    "PySide6.QtSvg",
    "et_xmlfile",            # openpyxl dependency, imported indirectly
]

# Collect data/binaries/submodules for packages PyInstaller can't fully trace.
# openpyxl/xlrd back the template importer (.xlsx/.xls) and are imported lazily,
# so PyInstaller's static scan can miss them without an explicit collect.
for pkg in ("plotly", "sqlcipher3", "argon2", "openpyxl", "xlrd"):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(pkg)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

block_cipher = None

a = Analysis(
    ["run_kosha.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "xlwt", "tkinter"],
    noarchive=False,
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Kosha",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # GUI app: no console window
    disable_windowed_traceback=False,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Kosha",
)
