# -*- mode: python ; coding: utf-8 -*-
import os
from pathlib import Path

block_cipher = None
APP_ICON = os.path.join(SPECPATH, "..", "src", "spd_model_injector", "ui", "app.ico")

a = Analysis(
    ["../src/spd_model_injector/app.py"],
    pathex=["..", "../src"],
    binaries=[],
    datas=[
        ("../src/spd_model_injector/ui/app.ico", "spd_model_injector/ui"),
        ("../src/spd_model_injector/ui/app.png", "spd_model_injector/ui"),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
# Qt uses Windows' ICU ABI; Poppler's same-named DLL on PATH is incompatible.
a.binaries = [
    entry for entry in a.binaries
    if Path(entry[0]).name.lower() != "icuuc.dll"
    and not Path(entry[0]).name.lower().startswith("icudt")
]
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SPD Model Injector",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon=APP_ICON,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="SPD Model Injector",
)
