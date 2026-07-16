"""
Central configuration for Ultimate DJ.
Persisted as JSON in the app directory.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = APP_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

CONFIG_FILE = DATA_DIR / "config.json"
DB_FILE = DATA_DIR / "dj_library.db"

DEFAULTS = {
    "music_root": str(APP_DIR.parent / "Music"),
    "music_roots_extra": [],   # additional music folders (strings)
    "download_folder": str(APP_DIR / "downloads"),
    "ffmpeg_path": r"C:\ffmpeg\bin\ffmpeg.exe",
    "mp3_quality": "320",
    "analysis_duration": 90,
    "spotify_client_id": "",
    "spotify_client_secret": "",
    "theme": "Cyan Night",
    # Off by default — Rekordbox / Engine DJ / Serato do their own
    # BPM/key analysis on import. Writing TBPM/TKEY tags into the file
    # could override (or seed) their analysis with our values. The DJ
    # opts in via Settings if they specifically want this.
    "write_tags_to_files": False,
}


def should_write_tags() -> bool:
    """Single source of truth for "is the app allowed to mutate the
    user's audio files with TBPM/TKEY tags?". Read each call so a
    Settings toggle takes effect immediately."""
    cfg = load_config()
    return bool(cfg.get("write_tags_to_files", False))


# Per-format opt-in on top of the master toggle. After the 2026-06 WAV
# corruption regression, non-MP3 containers stay read-only unless the
# user flips the matching flag explicitly — even via the force path.
_TAG_FORMAT_KEYS: dict[str, tuple[str, bool]] = {
    ".mp3":  ("write_tags_mp3", True),
    ".wav":  ("write_tags_wav", False),
    ".flac": ("write_tags_flac", False),
    ".m4a":  ("write_tags_m4a", False),
    ".mp4":  ("write_tags_m4a", False),
    ".aac":  ("write_tags_m4a", False),
    ".ogg":  ("write_tags_ogg", False),
    ".oga":  ("write_tags_ogg", False),
    ".opus": ("write_tags_ogg", False),
}


def should_write_tags_for(ext: str) -> bool:
    """Per-format gate for tag writes. Unknown extensions are refused."""
    key, default = _TAG_FORMAT_KEYS.get(ext.lower(), (None, False))
    if key is None:
        return False
    return bool(load_config().get(key, default))


def get_music_roots() -> list[str]:
    """All configured music folders (primary + extras + download folder).

    Order matters: primary first, then user-added extras, then the
    download folder (so freshly-downloaded tracks are picked up by Sync).
    Empty / non-existent paths are dropped silently.
    """
    cfg = load_config()
    raw = [cfg.get("music_root", ""),
           *list(cfg.get("music_roots_extra") or []),
           cfg.get("download_folder", "")]
    seen: set[str] = set()
    out: list[str] = []
    for p in raw:
        p = (p or "").strip()
        if not p or p in seen:
            continue
        seen.add(p)
        out.append(p)
    return out

# ── Camelot wheel constants ─────────────────────────────────────

CAMELOT_MAP = {
    "C major": "8B",  "A minor": "8A",
    "G major": "9B",  "E minor": "9A",
    "D major": "10B", "B minor": "10A",
    "A major": "11B", "F# minor": "11A",
    "E major": "12B", "C# minor": "12A",
    "B major": "1B",  "G# minor": "1A",
    "F# major": "6B", "D# minor": "6A",
    "Db major": "3B", "Bb minor": "3A",
    "Ab major": "4B", "F minor": "4A",
    "Eb major": "5B", "C minor": "5A",
    "Bb major": "6B", "G minor": "6A",
    "F major": "7B",  "D minor": "7A",
}

CAMELOT_KEYS = {v: k for k, v in CAMELOT_MAP.items()}

# ── Theme colours ────────────────────────────────────────────────

THEMES: dict[str, dict[str, str]] = {
    "Cyan Night": {
        "bg_dark":     "#0f0f1a",
        "bg_sidebar":  "#141428",
        "bg_card":     "#1a1a35",
        "bg_input":    "#22224a",
        "accent":      "#00d4ff",
        "accent_hover": "#00b8e6",
        "accent2":     "#ff3e8a",
        "text":        "#e0e0e0",
        "text_dim":    "#777",
        "success":     "#00e676",
        "warning":     "#ffab00",
        "error":       "#ff5252",
        # Foreground colour to use on colored backgrounds — keeps
        # contrast safe across themes (white-on-white in Mono killed
        # readability for cue chips and accent2 buttons)
        "on_accent":   "#0f0f1a",   # accent is light cyan → dark bg
        "on_accent2":  "#ffffff",   # accent2 is dark pink → white
        "on_warning":  "#0f0f1a",   # warning is yellow → dark bg
    },
    "Mono": {
        "bg_dark":     "#0d0d0d",
        "bg_sidebar":  "#161616",
        "bg_card":     "#1c1c1c",
        "bg_input":    "#262626",
        "accent":      "#cccccc",
        "accent_hover": "#aaaaaa",
        "accent2":     "#ffffff",
        "text":        "#e8e8e8",
        "text_dim":    "#777",
        "success":     "#a0e0a0",
        "warning":     "#e0c060",
        "error":       "#e08080",
        "on_accent":   "#0d0d0d",
        "on_accent2":  "#0d0d0d",   # ← was "white", now dark = readable
        "on_warning":  "#0d0d0d",
    },
}


class _ColorProxy(dict):
    """Mutable colours bag — `apply_theme()` swaps its content in place."""
    def reload(self) -> None:
        cfg = load_config()
        name = cfg.get("theme", "Cyan Night")
        palette = THEMES.get(name, THEMES["Cyan Night"])
        self.clear()
        self.update(palette)


COLORS: _ColorProxy = _ColorProxy()


def apply_theme(name: str | None = None) -> dict:
    """Apply a theme by name (or read from config). Returns the palette dict."""
    if name is not None:
        cfg = load_config()
        cfg["theme"] = name
        save_config(cfg)
    COLORS.reload()
    # Switch CustomTkinter appearance mode based on background luminance
    try:
        import customtkinter as ctk
        bg = COLORS.get("bg_dark", "#000000")
        # crude luminance check on hex #rrggbb
        r, g, b = int(bg[1:3], 16), int(bg[3:5], 16), int(bg[5:7], 16)
        lum = 0.299 * r + 0.587 * g + 0.114 * b
        ctk.set_appearance_mode("light" if lum > 160 else "dark")
    except Exception:
        pass
    return dict(COLORS)


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r") as f:
                cfg.update(json.load(f))
        except Exception:
            pass
    return cfg


def save_config(cfg: dict):
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)


def _bundled_bin(name: str) -> str | None:
    """<exe_dir>/bin/<name>.exe in the packaged build — the share bundle
    ships ffmpeg/node so friends install nothing. None outside frozen
    mode or when the file isn't there (winget path still applies)."""
    if not getattr(sys, "frozen", False):
        return None
    p = os.path.join(os.path.dirname(sys.executable), "bin", f"{name}.exe")
    return p if os.path.isfile(p) else None


def get_ffmpeg() -> str | None:
    bundled = _bundled_bin("ffmpeg")
    if bundled:
        return bundled
    cfg = load_config()
    p = cfg["ffmpeg_path"]
    if os.path.isfile(p):
        return p
    found = shutil.which("ffmpeg")
    return found


def get_node() -> str | None:
    bundled = _bundled_bin("node")
    if bundled:
        return bundled
    node = shutil.which("node") or shutil.which("node.exe")
    if node:
        return node
    candidate = r"C:\Program Files\nodejs\node.exe"
    if os.path.isfile(candidate):
        return candidate
    return None


# Initialise palette from disk at import time.
COLORS.reload()
