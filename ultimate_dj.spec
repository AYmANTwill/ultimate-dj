# PyInstaller build spec for Ultimate DJ
# -----------------------------------------------------------------
# Build a single-folder Windows executable bundling Python + every
# pure-Python dep. External binaries (FFmpeg, Node, rubberband.exe)
# stay system-side — the deps.py auto-installer handles them at first
# launch via winget so we keep the bundle under ~250 MB.
#
# Build:    python -m PyInstaller --clean ultimate_dj.spec
# Output:   dist/UltimateDJ/UltimateDJ.exe
# Test on a clean VM: copy the whole dist/UltimateDJ folder, run the
# .exe — splash should auto-install missing deps then start.
# -----------------------------------------------------------------
# pylint: disable=undefined-variable

import sys
from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_submodules,
    copy_metadata,
)


# librosa, customtkinter, sounddevice ship resources outside .py files
# (filter banks, themes, native DLLs). PyInstaller doesn't pull them
# automatically — collect_* helpers grab the whole tree.
datas = []
datas += collect_data_files("customtkinter")
datas += collect_data_files("librosa")
datas += collect_data_files("sounddevice")
datas += collect_data_files("soundfile")
# Some libs ship importlib-metadata they look up at runtime
datas += copy_metadata("yt_dlp")
datas += copy_metadata("spotipy")

# Submodules that PyInstaller's static analysis misses
hiddenimports = []
hiddenimports += collect_submodules("librosa")
hiddenimports += collect_submodules("customtkinter")
hiddenimports += collect_submodules("yt_dlp")
hiddenimports += collect_submodules("mutagen")
hiddenimports += collect_submodules("cloudscraper")
hiddenimports += [
    "app.engine.embeddings",
    "app.engine.segmentation",
    "app.engine.cooccurrence",
    "app.engine.tasks",
    "app.engine.backup",
    "app.engine.repair",
    "app.engine.tracklists",
    "app.ui.activity_tray",
    "app.ui.toast",
    "app.ui.deck",
    "app.ui.fastlist",
    "app.ui.track_editor",
    "app.ui.export_dialog",
    "app.secrets_store",
]

# Optional deps — load if present, else skipped (no hard failure)
for opt in ("transformers", "torch", "panns_inference", "pyrubberband"):
    try:
        __import__(opt)
        hiddenimports.append(opt)
    except Exception:
        pass


a = Analysis(
    ["run.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Drop the heaviest stuff we definitely never load. Trims 80-100 MB.
    excludes=[
        "matplotlib", "pandas", "scipy.spatial.qhull",
        "tornado", "IPython", "jedi", "PyQt5", "PyQt6", "PySide2",
        "PySide6", "wx", "tkinter.test",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="UltimateDJ",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,           # No console window — pure GUI app
    icon=None,               # add docs/icon.ico when designed
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="UltimateDJ",
)
