# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller configuration for the pywebview music player."""

from pathlib import Path

# PyInstaller executes a spec file as a namespace without ``__file__``.
# The build command is run from the repository root, so resolve data from the
# current build root rather than relying on the packaged app's runtime CWD.
SPEC_ROOT = Path.cwd().resolve()

datas = [
    (str(SPEC_ROOT / "ui" / "index.html"), "ui"),
    (str(SPEC_ROOT / "ui" / "app.css"), "ui"),
    (str(SPEC_ROOT / "ui" / "app.js"), "ui"),
]
binaries = []
hiddenimports = [
    "webview.platforms.edgechromium",
    "webview.platforms.winforms",
]


a = Analysis(
    ["music.py"],
    pathex=[str(SPEC_ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="music",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
