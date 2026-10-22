# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller recipe: run from the NetworkMonitor folder with `pyinstaller build.spec`."""
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

root = Path(SPECPATH)

# pysnmp loads its bundled MIB modules from disk at runtime (its own MibBuilder,
# not a Python import), so PyInstaller's static analysis can't see them; without
# collecting them as data files, any SNMP check crashes with MibNotFoundError.
snmp_datas = collect_data_files("pysnmp")

a = Analysis(
    [str(root / "app" / "main.py")],
    pathex=[str(root)],
    binaries=[],
    datas=snmp_datas,
    hiddenimports=["pystray._win32", "PIL.Image"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="NetworkMonitor",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
)
