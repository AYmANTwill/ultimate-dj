# PyInstaller build spec for Ultimate DJ **LITE**
# -----------------------------------------------------------------
# Same codebase as the full build, but a runtime hook sets
# ULTIMATEDJ_LITE=1 so the app shows only Library + Download. Meant
# for a non-technical friend: unzip, double-click, done.
#
# Build:  python -m PyInstaller --clean ultimate_dj_lite.spec
# Output: dist/UltimateDJ-Lite/UltimateDJ-Lite.exe
# (build_share_lite.py wraps this + bundles ffmpeg/node + LISEZ-MOI)
# -----------------------------------------------------------------
# pylint: disable=undefined-variable

from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_submodules,
    copy_metadata,
)

datas = []
datas += collect_data_files("customtkinter")
datas += collect_data_files("librosa")
datas += collect_data_files("sounddevice")
datas += collect_data_files("soundfile")
datas += copy_metadata("yt_dlp")
datas += copy_metadata("spotipy")

hiddenimports = []
hiddenimports += collect_submodules("librosa")
hiddenimports += collect_submodules("customtkinter")
hiddenimports += collect_submodules("yt_dlp")
hiddenimports += collect_submodules("mutagen")
hiddenimports += collect_submodules("cloudscraper")
# Pages are string-loaded via importlib (app.ui.app._LazyPage) — grab
# the whole app tree so no page is missing in the frozen build.
hiddenimports += collect_submodules("app")

for opt in ("pyrubberband",):
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
    runtime_hooks=["rthook_lite.py"],
    excludes=[
        "matplotlib", "pandas", "scipy.spatial.qhull",
        "tornado", "IPython", "jedi", "PyQt5", "PyQt6", "PySide2",
        "PySide6", "wx", "tkinter.test",
        # Heavy opt-in AI stack — never in the shared build.
        "torch", "torchaudio", "torchvision", "transformers",
        "panns_inference", "tokenizers", "safetensors", "sympy",
        # Lite has no Live mode / Rekordbox bridge — its module is
        # bundled (app.*) but only imports these inside functions the
        # Lite sidebar never reaches, so the binding itself is dropped.
        "pyrekordbox", "sqlcipher3",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="UltimateDJ-Lite",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon="assets/icon.ico",
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
    name="UltimateDJ-Lite",
)
