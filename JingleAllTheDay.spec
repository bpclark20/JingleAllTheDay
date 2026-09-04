# -*- mode: python ; coding: utf-8 -*-

import re
import sys
from pathlib import Path

# Version is owned by app.py (APP_VERSION); this spec re-exports it so the
# EXE's Windows version resource can never drift from the app's version.
# SPECPATH is injected by PyInstaller at spec-execution time.
_spec_dir = Path(globals().get("SPECPATH", "."))
_version_match = re.search(
    r'APP_VERSION\s*=\s*["\']([^"\']+)["\']',
    (_spec_dir / "app.py").read_text(encoding="utf-8"),
)
if not _version_match:
    raise RuntimeError("Unable to parse APP_VERSION from app.py")
APP_VERSION = _version_match.group(1)

# Windows version resource. The numeric fields are 16-bit (max 65535), which
# cannot represent the date-based build component (e.g. 90426), so the numeric
# quad keeps the first three components and uses 0 for the fourth; the exact
# version is carried in the FileVersion/ProductVersion strings (what Explorer's
# Details tab displays).
_version_info = None
if sys.platform == "win32":
    from PyInstaller.utils.win32.versioninfo import (
        FixedFileInfo,
        StringFileInfo,
        StringStruct,
        StringTable,
        VarFileInfo,
        VarStruct,
        VSVersionInfo,
    )

    _numeric = [int(p) for p in APP_VERSION.split(".")[:3] if p.isdigit()]
    while len(_numeric) < 3:
        _numeric.append(0)
    _numeric_quad = tuple(min(p, 65535) for p in _numeric) + (0,)

    _version_info = VSVersionInfo(
        ffi=FixedFileInfo(
            filevers=_numeric_quad,
            prodvers=_numeric_quad,
            mask=0x3F,
            flags=0x0,
            OS=0x40004,
            fileType=0x1,
            subtype=0x0,
            date=(0, 0),
        ),
        kids=[
            StringFileInfo(
                [
                    StringTable(
                        "040904B0",
                        [
                            StringStruct("CompanyName", "Brian Clark"),
                            StringStruct("FileDescription", "JingleAllTheDay"),
                            StringStruct("FileVersion", APP_VERSION),
                            StringStruct("InternalName", "JingleAllTheDay"),
                            StringStruct("OriginalFilename", "JingleAllTheDay.exe"),
                            StringStruct("ProductName", "JingleAllTheDay"),
                            StringStruct("ProductVersion", APP_VERSION),
                        ],
                    )
                ]
            ),
            VarFileInfo([VarStruct("Translation", [0x0409, 0x04B0])]),
        ],
    )


a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('icon.ico', '.'),
        ('icon.png', '.'),  # ico for bootloader/EXE; png for Qt setWindowIcon
    ],
    hiddenimports=[
        'sounddevice',
        'soundfile',
        'numpy',
        'sample_pad_audio_engine',
        'remote_server',
        'mainwindow_server_mixin',
        'websockets',
        'websockets.sync',
        'websockets.sync.client',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Server-side code is deployed manually to the relay host and must
        # never be bundled into the desktop app. Nothing imports these today;
        # this fails safe against future accidental imports.
        'jsrv',
        'jingleserver',
        'fastapi',
        'uvicorn',
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='JingleAllTheDay',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=_version_info,
    icon='icon.ico',
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='JingleAllTheDay',
)
