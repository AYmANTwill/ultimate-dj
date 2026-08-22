"""
Consented auto-updater — pulls new builds from the OWNER's GitHub
Releases and applies them with the user's click.

This is a normal app updater (like Chrome/Discord): it checks one
pinned, owner-controlled repo, shows the user what's available, and
only downloads/installs when they accept. It is NOT a hidden remote
channel — nothing runs code the user didn't agree to, and the source
is fixed at build time.

Flow
----
    info = check_for_update()          # None if up to date / offline
    zip_path = download_update(info, progress_cb)
    apply_update(zip_path)             # swaps files, relaunches, quits

Everything is fail-soft: any network / parse / IO problem means
"no update right now", never a crash.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path
from urllib.error import URLError

from app.config import DATA_DIR
from app.logger import log_error, log_info, log_warning
from app.version import __version__

# Pinned to the owner's own repository — the only source we ever fetch
# from. Change here (not at runtime) to point elsewhere.
REPO = "AYmANTwill/ultimate-dj"
_API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
_TIMEOUT = 30
_UA = "UltimateDJ-Updater"
_STAGE = DATA_DIR / "_update"


def _version_tuple(v: str) -> tuple[int, ...]:
    v = (v or "").strip().lstrip("vV")
    parts = []
    for chunk in v.split("."):
        num = "".join(c for c in chunk if c.isdigit())
        parts.append(int(num) if num else 0)
    return tuple(parts) or (0,)


def is_newer(remote: str, local: str = __version__) -> bool:
    return _version_tuple(remote) > _version_tuple(local)


def check_for_update() -> dict | None:
    """Return {version, notes, asset_url, asset_name, asset_size, html}
    if a newer release with a downloadable .zip asset exists, else None.
    Never raises."""
    try:
        req = urllib.request.Request(
            _API_LATEST,
            headers={"User-Agent": _UA,
                     "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            rel = json.loads(resp.read().decode("utf-8"))
    except (URLError, OSError, ValueError) as e:
        log_warning(f"updater: check failed (offline?): {e}")
        return None

    tag = rel.get("tag_name") or rel.get("name") or ""
    if not tag or not is_newer(tag):
        return None
    asset = next((a for a in rel.get("assets", [])
                  if str(a.get("name", "")).lower().endswith(".zip")), None)
    if not asset or not asset.get("browser_download_url"):
        log_warning(f"updater: release {tag} has no .zip asset")
        return None
    return {
        "version":    tag.lstrip("vV"),
        "notes":      (rel.get("body") or "").strip(),
        "asset_url":  asset["browser_download_url"],
        "asset_name": asset.get("name", "update.zip"),
        "asset_size": int(asset.get("size", 0) or 0),
        "html":       rel.get("html_url", ""),
    }


def download_update(info: dict, progress_cb=None) -> Path | None:
    """Download the release zip into a staging folder. progress_cb(
    fraction) is called during the download. Returns the zip path or
    None on failure."""
    try:
        _STAGE.mkdir(parents=True, exist_ok=True)
        dest = _STAGE / info["asset_name"]
        req = urllib.request.Request(info["asset_url"],
                                     headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            total = int(resp.headers.get("Content-Length",
                                         info.get("asset_size", 0)) or 0)
            done = 0
            with open(dest, "wb") as f:
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
                    done += len(chunk)
                    if progress_cb and total:
                        progress_cb(min(1.0, done / total))
        return dest
    except (URLError, OSError) as e:
        log_error("updater: download failed", e)
        return None


def apply_update(zip_path: Path) -> bool:
    """Extract the update and hand off to a helper that swaps the files
    once this process exits, then relaunches. Windows-only, frozen-only
    (a running .exe can't overwrite itself). Returns True if the
    handoff started (the app should then quit)."""
    if not getattr(sys, "frozen", False):
        log_warning("updater: apply skipped — not a frozen build")
        return False
    if sys.platform != "win32":
        log_warning("updater: apply skipped — Windows-only")
        return False
    try:
        staged = _STAGE / "unpacked"
        if staged.exists():
            _rmtree(staged)
        staged.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(staged)
        # The zip may wrap everything in one top folder; if so, descend
        # into it so we copy the payload, not the wrapper.
        entries = [p for p in staged.iterdir()]
        src = entries[0] if (len(entries) == 1 and entries[0].is_dir()) \
            else staged

        install_dir = Path(sys.executable).parent
        exe_name = Path(sys.executable).name
        bat = _STAGE / "apply_update.bat"
        _write_apply_script(bat, os.getpid(), src, install_dir, exe_name)
        subprocess.Popen(
            ["cmd", "/c", str(bat)],
            creationflags=0x00000008 | 0x00000200,  # DETACHED | NEW_GROUP
            close_fds=True)
        log_info(f"updater: handoff started, swapping into {install_dir}")
        return True
    except (OSError, zipfile.BadZipFile) as e:
        log_error("updater: apply failed", e)
        return False


def _write_apply_script(bat: Path, pid: int, src: Path,
                        install_dir: Path, exe_name: str) -> None:
    # Wait for THIS process to exit, mirror the new files over the
    # install dir (robocopy handles locked/updated files and retries),
    # relaunch, then clean the staging area.
    bat.write_text(
        "@echo off\r\n"
        "setlocal\r\n"
        ":wait\r\n"
        f'tasklist /FI "PID eq {pid}" | find "{pid}" >nul\r\n'
        'if not errorlevel 1 ( timeout /t 1 /nobreak >nul & goto wait )\r\n'
        f'robocopy "{src}" "{install_dir}" /E /IS /IT /R:3 /W:2 >nul\r\n'
        f'start "" "{install_dir}\\{exe_name}"\r\n'
        f'rmdir /S /Q "{src.parent}" >nul 2>&1\r\n',
        encoding="utf-8")


def _rmtree(p: Path) -> None:
    import shutil
    shutil.rmtree(p, ignore_errors=True)
