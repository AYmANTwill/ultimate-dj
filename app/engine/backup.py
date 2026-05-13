"""
DB safety net — automatic snapshots of dj_library.db.

Why this exists:
    The whole app's state — library metadata, ratings, cues, beat grids,
    setlists, playlist sync caches — sits in a single SQLite file. A bad
    bulk delete, a corrupted write, a disk error, anything can wipe it.
    Auto-backup means the user can recover from any catastrophe with at
    most ~24h of lost work.

How it works:
    - On app launch: snapshot if no backup exists or the latest one is
      older than `_MAX_AGE_HOURS`.
    - Before destructive ops (bulk delete, sync orphan removal, drop
      duplicates): force a snapshot regardless of age.
    - Snapshots live in `data/db_backups/dj_library_<utc_iso>.db`.
    - Auto-prune keeps the most recent `_KEEP` files.

The snapshot uses SQLite's ``VACUUM INTO`` (atomic, no concurrent-write
issues, smaller file size). Falls back to a plain file copy if VACUUM
isn't available.
"""
from __future__ import annotations

import shutil
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

from app.config import DATA_DIR, DB_FILE
from app.logger import log_info, log_warning


_BACKUP_DIR = DATA_DIR / "db_backups"
_MAX_AGE_HOURS = 24
_KEEP = 10
_PREFIX = "dj_library_"


def _ensure_dir() -> Path:
    _BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    return _BACKUP_DIR


def list_backups() -> list[Path]:
    """Snapshots, newest first."""
    if not _BACKUP_DIR.exists():
        return []
    return sorted(_BACKUP_DIR.glob(f"{_PREFIX}*.db"),
                   key=lambda p: p.stat().st_mtime, reverse=True)


def _latest() -> Path | None:
    snaps = list_backups()
    return snaps[0] if snaps else None


def _age_hours(p: Path) -> float:
    return (time.time() - p.stat().st_mtime) / 3600


def _slug() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def snapshot(*, reason: str = "") -> Path | None:
    """Create a fresh snapshot. Returns the new file's path on success.

    Uses ``VACUUM INTO`` so the snapshot is consistent without holding
    a write lock on the live DB. If that fails (older sqlite, exotic
    file system) we fall back to ``shutil.copy2``.
    """
    try:
        _ensure_dir()
        out = _BACKUP_DIR / f"{_PREFIX}{_slug()}.db"
        if not DB_FILE.exists():
            log_warning("backup: source DB doesn't exist yet")
            return None
        try:
            # VACUUM INTO is atomic + ignores transactions in flight
            with sqlite3.connect(str(DB_FILE), timeout=10.0) as src:
                src.execute(f"VACUUM INTO '{out.as_posix()}'")
        except sqlite3.DatabaseError:
            shutil.copy2(DB_FILE, out)
        log_info(f"backup created: {out.name} ({reason or 'auto'})")
        _prune()
        return out
    except Exception as e:
        log_warning(f"backup snapshot failed: {e}")
        return None


def maybe_snapshot(*, reason: str = "auto") -> Path | None:
    """Snapshot only if no recent one exists. Cheap to call on startup."""
    latest = _latest()
    if latest is None or _age_hours(latest) > _MAX_AGE_HOURS:
        return snapshot(reason=reason)
    return None


def force_snapshot_before_destructive(reason: str) -> Path | None:
    """Always snapshot, regardless of age. Use right before an action
    that modifies multiple rows (bulk delete, dedupe, sync orphans)."""
    return snapshot(reason=reason)


def _prune() -> int:
    """Keep only the `_KEEP` newest snapshots; delete the rest."""
    snaps = list_backups()
    deleted = 0
    for old in snaps[_KEEP:]:
        try:
            old.unlink()
            deleted += 1
        except OSError:
            pass
    return deleted


def restore_from(snapshot_path: str | Path) -> bool:
    """Replace the live DB with a snapshot. Caller is responsible for
    closing all open connections first (relaunch the app after).
    Returns True on success."""
    src = Path(snapshot_path)
    if not src.exists():
        log_warning(f"backup.restore: source missing: {src}")
        return False
    # Take ONE more snapshot of the current state before clobbering it
    snapshot(reason="pre-restore")
    try:
        shutil.copy2(src, DB_FILE)
        # Also remove the WAL/SHM siblings so the next open re-creates them
        for sibling in (DB_FILE.with_suffix(DB_FILE.suffix + "-wal"),
                         DB_FILE.with_suffix(DB_FILE.suffix + "-shm")):
            try:
                sibling.unlink()
            except OSError:
                pass
        log_info(f"backup.restore from {src.name} OK")
        return True
    except Exception as e:
        log_warning(f"backup.restore failed: {e}")
        return False


def stats() -> dict:
    """Summary for the Settings UI."""
    snaps = list_backups()
    if not snaps:
        return {"count": 0, "newest": None, "size_mb": 0.0}
    total = sum(p.stat().st_size for p in snaps) / (1024 * 1024)
    newest = snaps[0]
    return {
        "count":   len(snaps),
        "newest":  newest.name,
        "newest_age_h": _age_hours(newest),
        "size_mb": round(total, 1),
    }
