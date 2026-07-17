"""
Auto-dependency checker and installer.
Runs before the main app starts — ensures everything is in place.
Uses only stdlib (tkinter) for the splash screen so it works before pip install.
"""
from __future__ import annotations

import importlib
import importlib.util
import os
import shutil
import subprocess
import sys
import tkinter as tk
from tkinter import ttk
from pathlib import Path

# ── What we need ────────────────────────────────────────────────
REQUIREMENTS = [
    # (import_name, pip_name, friendly_label)
    ("customtkinter",  "customtkinter>=5.2.0",  "CustomTkinter (UI)"),
    ("yt_dlp",         "yt-dlp[default]",       "yt-dlp (downloader)"),
    ("spotipy",        "spotipy>=2.23.0",        "Spotipy (Spotify API)"),
    ("librosa",        "librosa>=0.10.0",        "Librosa (audio analysis)"),
    ("numpy",          "numpy>=1.24.0",          "NumPy"),
    ("soundfile",      "soundfile>=0.12.0",      "SoundFile"),
    ("mutagen",        "mutagen>=1.47.0",        "Mutagen (ID3 tags)"),
    ("PIL",            "Pillow>=10.0.0",         "Pillow (images)"),
    ("sounddevice",    "sounddevice>=0.4.6",     "sounddevice (lecture audio frame-precise)"),
    ("webview",        "pywebview>=6.0",         "pywebview (browser embarqué)"),
    ("cloudscraper",   "cloudscraper>=1.2.71",   "cloudscraper (1001tracklists)"),
    # Playwright + stealth handle the JS-rendered shell that
    # 1001tracklists serves to non-browser clients. Heavy install
    # (~150 MB chromium) — done on first launch via deps.py install path
    # below. App still works without these; the corpus enrichment
    # pipeline just degrades gracefully and reports the failure.
    ("playwright",        "playwright>=1.50.0",      "Playwright (JS scraping)"),
    ("playwright_stealth","playwright-stealth>=2.0", "Playwright Stealth (bot evasion)"),
    ("bs4",            "beautifulsoup4>=4.12",   "BeautifulSoup (HTML parsing)"),
    ("lxml",           "lxml>=4.9",              "lxml (XML/HTML fast parser)"),
    ("keyring",        "keyring>=24.0",          "keyring (Windows Credential Manager)"),
    ("pyrekordbox",    "pyrekordbox>=0.4",       "pyrekordbox (pont Rekordbox : sets + Live)"),
]

EXTERNAL_TOOLS = [
    # (exe_name, common_install_path, winget_id, label)
    ("ffmpeg", r"C:\ffmpeg\bin\ffmpeg.exe", "Gyan.FFmpeg", "FFmpeg (audio conversion)"),
    ("node",   r"C:\Program Files\nodejs\node.exe", "OpenJS.NodeJS.LTS", "Node.js (YouTube auth)"),
]


def _find_python() -> str:
    return sys.executable


def _find_exe(name: str, fallback_path: str) -> str | None:
    # Packaged build ships ffmpeg/node in <exe_dir>/bin — check there
    # first so friends' machines never fall through to winget.
    if getattr(sys, "frozen", False):
        bundled = os.path.join(os.path.dirname(sys.executable),
                               "bin", f"{name}.exe")
        if os.path.isfile(bundled):
            return bundled
    exe = shutil.which(name) or shutil.which(f"{name}.exe")
    if exe:
        return exe
    if os.path.isfile(fallback_path):
        return fallback_path
    return None


def is_frozen() -> bool:
    """True when running as the PyInstaller-packaged .exe. In that mode
    every Python dependency is bundled — we only ever check the external
    binaries (FFmpeg / Node), never pip-install anything (there is no
    pip and sys.executable is the app, not python)."""
    return bool(getattr(sys, "frozen", False))


def check_all() -> dict:
    """Return status dict: {label: (ok: bool, detail: str)}."""
    status = {}

    if not is_frozen():
        for imp_name, pip_name, label in REQUIREMENTS:
            # Use find_spec — orders of magnitude faster than full import
            # (avoids loading librosa/numpy/etc. at every startup)
            spec = importlib.util.find_spec(imp_name)
            if spec is not None:
                status[label] = (True, "installed")
            else:
                status[label] = (False, pip_name)

    for exe_name, fallback, winget_id, label in EXTERNAL_TOOLS:
        path = _find_exe(exe_name, fallback)
        if path:
            status[label] = (True, path)
        else:
            status[label] = (False, winget_id)

    return status


def install_missing(status: dict, progress_cb=None) -> list[str]:
    """Install missing deps. Returns list of errors (empty = all good)."""
    errors = []
    missing = [(label, detail) for label, (ok, detail) in status.items() if not ok]
    total = len(missing)

    for i, (label, detail) in enumerate(missing):
        if progress_cb:
            progress_cb(label, i, total)

        if detail.startswith("OpenJS") or detail.startswith("Gyan"):
            # External tool — try winget
            try:
                subprocess.run(
                    ["winget", "install", "--accept-source-agreements",
                     "--accept-package-agreements", detail],
                    check=True, capture_output=True, timeout=300,
                )
            except Exception as e:
                errors.append(f"{label}: winget install failed — {e}")
        elif is_frozen():
            # Should never happen (all Python deps are bundled), but never
            # attempt pip against the frozen exe — sys.executable is the
            # app, not python. Surface it instead of crashing.
            errors.append(f"{label}: manquant dans le build figé "
                          f"(signale ce message au développeur)")
        else:
            # Python package — pip install
            try:
                subprocess.run(
                    [sys.executable, "-m", "pip", "install", "-q", detail],
                    check=True, capture_output=True, timeout=300,
                )
            except Exception as e:
                errors.append(f"{label}: pip install failed — {e}")
                continue
            # Post-install: playwright also needs its Chromium binary
            # (~150 MB). Don't fail the whole setup if Chromium download
            # struggles — playwright code still works for non-protected
            # pages, and the user can re-run `playwright install
            # chromium` manually.
            if detail.startswith("playwright>"):
                if progress_cb:
                    progress_cb(
                        f"{label} — téléchargement Chromium (~150 MB)",
                        i, total)
                try:
                    subprocess.run(
                        [sys.executable, "-m", "playwright",
                         "install", "chromium"],
                        check=False, capture_output=True, timeout=900,
                    )
                except Exception as e:
                    errors.append(
                        f"{label}: chromium install failed (non-fatal) — "
                        f"run `python -m playwright install chromium` "
                        f"manually. Error: {e}")

    if progress_cb:
        progress_cb("Done", total, total)
    return errors


# ── Splash screen (pure tkinter, no deps needed) ───────────────

class SetupSplash:
    """Minimal splash window for first-run dependency install."""

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Ultimate DJ — Setup")
        self.root.geometry("480x320")
        self.root.resizable(False, False)
        self.root.configure(bg="#1a1a2e")

        # Center on screen
        self.root.update_idletasks()
        x = (self.root.winfo_screenwidth() - 480) // 2
        y = (self.root.winfo_screenheight() - 320) // 2
        self.root.geometry(f"+{x}+{y}")

        # Title
        tk.Label(self.root, text="ULTIMATE DJ", font=("Segoe UI", 22, "bold"),
                 fg="#00d4ff", bg="#1a1a2e").pack(pady=(30, 5))
        tk.Label(self.root, text="First-run setup — installing dependencies...",
                 font=("Segoe UI", 10), fg="#888", bg="#1a1a2e").pack()

        # Status label
        self.status_var = tk.StringVar(value="Checking...")
        tk.Label(self.root, textvariable=self.status_var,
                 font=("Segoe UI", 10), fg="#ccc", bg="#1a1a2e").pack(pady=(20, 5))

        # Progress bar
        style = ttk.Style()
        style.theme_use("default")
        style.configure("cyan.Horizontal.TProgressbar",
                        troughcolor="#16213e", background="#00d4ff",
                        thickness=18)
        self.progress = ttk.Progressbar(
            self.root, length=380, mode="determinate",
            style="cyan.Horizontal.TProgressbar")
        self.progress.pack(pady=10)

        # Detail label
        self.detail_var = tk.StringVar(value="")
        tk.Label(self.root, textvariable=self.detail_var,
                 font=("Segoe UI", 9), fg="#666", bg="#1a1a2e").pack()

    def update_progress(self, label: str, current: int, total: int):
        if total > 0:
            pct = int((current / total) * 100)
            self.progress["value"] = pct
        self.status_var.set(f"Installing: {label}")
        self.detail_var.set(f"{current}/{total}")
        self.root.update()

    def show_done(self, errors: list[str]):
        if errors:
            self.status_var.set("Setup completed with warnings")
            self.detail_var.set("\n".join(errors[:3]))
        else:
            self.status_var.set("All dependencies installed!")
            self.detail_var.set("Launching app...")
        self.progress["value"] = 100
        self.root.update()
        self.root.after(1200, self.root.destroy)
        self.root.mainloop()

    def destroy(self):
        try:
            self.root.destroy()
        except Exception:
            pass


def ensure_deps() -> bool:
    """Check and install dependencies. Returns True if ready to launch."""
    status = check_all()
    missing = [k for k, (ok, _) in status.items() if not ok]

    if not missing:
        # Ensure node is on PATH for yt-dlp
        _ensure_node_path()
        return True

    # Show splash and install
    splash = SetupSplash()
    splash.root.update()
    errors = install_missing(status, progress_cb=splash.update_progress)
    splash.show_done(errors)

    _ensure_node_path()
    return True


def _ensure_node_path():
    """Make sure Node.js is on PATH for yt-dlp subprocess calls."""
    node = _find_exe("node", r"C:\Program Files\nodejs\node.exe")
    if node:
        node_dir = os.path.dirname(node)
        if node_dir not in os.environ.get("PATH", ""):
            os.environ["PATH"] = node_dir + os.pathsep + os.environ.get("PATH", "")
