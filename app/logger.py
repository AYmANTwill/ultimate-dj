"""
Central error/event logger for Ultimate DJ.
All errors across every module write to data/errors.log.

Rotation: hard cap at 5 × 2 MB = 10 MB total on disk. Earlier the log
grew unbounded (a 5-day session left a 2.7 MB single file with no way
to bound it) — a stuck warning loop could blow up the data folder
unattended. RotatingFileHandler caps each file and keeps the last N.
"""
from __future__ import annotations

import logging
import logging.handlers
import traceback
from pathlib import Path

from app.config import DATA_DIR

LOG_FILE = DATA_DIR / "errors.log"
_MAX_BYTES = 2 * 1024 * 1024     # 2 MB per file
_BACKUP_COUNT = 4                # → 5 files total (errors.log, .1, .2, .3, .4)


def _get_logger() -> logging.Logger:
    logger = logging.getLogger("ultimatedj")
    if not logger.handlers:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(
            str(LOG_FILE), encoding="utf-8", mode="a",
            maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT)
        fh.setFormatter(logging.Formatter(
            "%(asctime)s  [%(levelname)-7s]  %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        logger.addHandler(fh)
        logger.setLevel(logging.DEBUG)
    return logger


def log_info(msg: str):
    _get_logger().info(msg)


def log_warning(msg: str):
    _get_logger().warning(msg)


def log_error(context: str, exc: Exception | None = None):
    logger = _get_logger()
    if exc:
        tb = traceback.format_exc().strip()
        logger.error(f"{context}: {exc}\n{tb}")
    else:
        logger.error(context)


def get_log_path() -> str:
    return str(LOG_FILE)


def tail_log(lines: int = 300, *, level: str | None = None,
             path: str | Path | None = None) -> list[str]:
    """Last ``lines`` log entries (oldest → newest), optionally filtered
    by level ('ERROR' / 'WARNING' / 'INFO'). Includes the previous
    rollover file so a freshly-rotated log still shows history. Powers
    the Settings → Journal viewer."""
    p = Path(path) if path else LOG_FILE
    out: list[str] = []
    for f in (Path(str(p) + ".1"), p):
        try:
            out.extend(f.read_text("utf-8",
                                    errors="replace").splitlines())
        except OSError:
            continue
    if level:
        needle = f"[{level.upper():<7}]"
        out = [ln for ln in out if needle in ln]
    return out[-lines:]
