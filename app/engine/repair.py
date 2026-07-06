"""
Repair audio files that were corrupted by the old ``write_tags()``.

Background:
    Earlier versions of the analyzer wrote MP3-style ID3v2 tags directly
    into WAV / FLAC / M4A files via ``ID3(path).save(path)``. Mutagen
    handles that on MP3s (the format expects a leading ID3 chunk) but
    on other containers it just *prepends* raw ID3 bytes BEFORE the
    file's actual magic header (RIFF, fLaC, ftyp). Rekordbox 7,
    Engine DJ, and any strict decoder bail out because the container
    no longer starts with the expected magic.

    Fix: scan a folder, look at the first ~64KB of each non-MP3 file,
    locate the real magic bytes, and rewrite the file starting there.
    Instead of physical .bak backups (disk-space heavy on large libs),
    every repair is appended to ``data/repair_history.json`` so the
    user has a verifiable audit trail.

Public API:
    inspect(path) -> dict        — diagnose without touching the file
    repair(path)  -> dict        — repair if needed, returns details
    inspect_chunks(path) -> dict — v2: walk WAV chunks, spot trailing damage
    repair_trailing(path) -> dict — v2: cut trailing garbage, fix RIFF size
    undo_trailing(path, tail_file, riff_size_before) -> dict — reverse a v2 fix
    scan_folder(root, on_progress=None) -> dict — bulk repair (v1 + v2 passes)
    history(limit=200) -> list[dict] — recent repair log entries
    purge_backups(root) -> int   — delete legacy .bak files

Designed to be safe to run on ALREADY-VALID files: a file whose magic
already starts at offset 0 is a no-op (the inspect() result tells the
caller "ok" and no write happens).
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from pathlib import Path
from typing import Callable

from app.config import DATA_DIR

# Magic bytes for the containers we know how to repair.
# Each tuple: (extension, bytes_to_search_for)
# We deliberately list common extensions for each magic so we still find
# RIFF/fLaC/ftyp even if the file was renamed.
_MAGICS: dict[str, bytes] = {
    ".wav":  b"RIFF",
    ".flac": b"fLaC",
    ".m4a":  b"ftyp",   # ISOBMFF — actual offset is typically 4, see below
    ".mp4":  b"ftyp",
    ".aac":  b"ftyp",
    ".ogg":  b"OggS",
    ".oga":  b"OggS",
    ".opus": b"OggS",
}

# Maximum number of leading bytes we'll accept as "garbage to strip".
# An ID3v2 tag header is up to ~256 KB in pathological cases but in
# practice the prepended chunk is < 64 KB. Anything bigger than this
# probably isn't ID3 garbage and we refuse to touch the file.
_MAX_PREFIX = 256 * 1024


def _expected_magic(path: Path) -> bytes | None:
    return _MAGICS.get(path.suffix.lower())


def _find_magic_offset(path: Path, magic: bytes) -> int:
    """Return the byte offset of `magic` within the first _MAX_PREFIX
    bytes of `path`, or -1 if not found.

    For ISOBMFF (m4a/mp4), the magic ``ftyp`` actually appears at offset
    4 of a valid file (the first 4 bytes are a 32-bit box size). So we
    treat any ftyp at offset 4 (mod 0) as the start of the real file
    and rewrite from offset 0 of the box (offset = found - 4).
    """
    with open(path, "rb") as f:
        chunk = f.read(_MAX_PREFIX)
    pos = chunk.find(magic)
    if pos < 0:
        return -1
    if magic == b"ftyp":
        # ISOBMFF: box header is [4-byte size][4-byte type 'ftyp']
        # Real file start is 4 bytes BEFORE the magic.
        pos = max(0, pos - 4)
    return pos


def inspect(path: str | Path) -> dict:
    """Diagnose a file without modifying it.

    Returns dict with keys:
      status:  'ok' | 'corrupt' | 'unknown_format' | 'no_magic_found' | 'error'
      offset:  int — where the real container starts (0 = file is valid)
      magic:   bytes — what we were looking for (None if unknown format)
      size:    int — file size on disk
      message: str — human-readable summary
    """
    p = Path(path)
    out: dict = {"path": str(p), "size": 0, "offset": 0,
                 "magic": None, "status": "ok", "message": ""}
    try:
        out["size"] = p.stat().st_size
    except OSError as e:
        out["status"] = "error"
        out["message"] = f"stat failed: {e}"
        return out

    magic = _expected_magic(p)
    if magic is None:
        out["status"] = "unknown_format"
        out["message"] = f"extension {p.suffix.lower()!r} not handled"
        return out
    out["magic"] = magic

    offset = _find_magic_offset(p, magic)
    if offset < 0:
        out["status"] = "no_magic_found"
        out["message"] = (f"no {magic!r} found in first {_MAX_PREFIX//1024} KB"
                          f" — file may not be {p.suffix.lower()}")
        return out
    out["offset"] = offset
    if offset == 0:
        out["status"] = "ok"
        out["message"] = f"{magic.decode(errors='replace')} at offset 0 — file is valid"
    else:
        out["status"] = "corrupt"
        out["message"] = (f"{magic.decode(errors='replace')} at offset "
                          f"{offset} — {offset} bytes of garbage to strip")
    return out


# ── Repair history (replaces .bak files) ─────────────────────────

_HISTORY_FILE = DATA_DIR / "repair_history.json"
_HISTORY_MAX = 5000     # cap so the JSON file can't grow unbounded


def _append_history(entry: dict) -> None:
    """Append one repair record to the on-disk history.

    The file is a JSON list; we read-modify-write rather than JSONL
    to keep the format trivial to display in the UI. With a 5000-entry
    cap the file stays well under 2 MB even for a heavy library.
    """
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        history: list[dict] = []
        if _HISTORY_FILE.exists():
            try:
                history = list(json.loads(_HISTORY_FILE.read_text("utf-8")))
            except Exception:
                history = []
        history.append(entry)
        if len(history) > _HISTORY_MAX:
            history = history[-_HISTORY_MAX:]
        _HISTORY_FILE.write_text(
            json.dumps(history, ensure_ascii=False, indent=2),
            encoding="utf-8")
    except Exception:
        # Never let history write failures break a repair
        pass


def history(limit: int = 200) -> list[dict]:
    """Return the most recent repair entries (newest first)."""
    if not _HISTORY_FILE.exists():
        return []
    try:
        data = json.loads(_HISTORY_FILE.read_text("utf-8"))
        return list(reversed(data))[:limit]
    except Exception:
        return []


def repair(path: str | Path) -> dict:
    """Fix `path` if it has prepended garbage. Idempotent.

    Returns the same dict shape as inspect(), with extra keys:
      repaired:   bool   — True only if we actually rewrote the file
      stripped:   int    — bytes removed from the front
      timestamp:  float  — when the repair happened

    Strategy: read the prefix to find the real magic, then rewrite the
    file starting at that offset. Uses a temp file + atomic rename so
    a crash mid-repair can't truncate the source. No physical backup —
    each repair is appended to ``data/repair_history.json`` instead, so
    the user can audit what changed without paying disk space.
    """
    info = inspect(path)
    info["repaired"] = False
    info["stripped"] = 0

    if info["status"] != "corrupt":
        return info

    p = Path(path)
    offset = int(info["offset"])
    original_size = info.get("size", 0)

    # Rewrite via temp + atomic replace.
    # No .bak — we trust the magic-bytes detector (it refuses to touch
    # files where the magic isn't found at all) and log to history instead.
    tmp = p.with_suffix(p.suffix + ".tmp_repair")
    try:
        with open(p, "rb") as src, open(tmp, "wb") as dst:
            src.seek(offset)
            shutil.copyfileobj(src, dst, length=1024 * 1024)
        os.replace(tmp, p)
    except OSError as e:
        try:
            tmp.unlink()
        except OSError:
            pass
        info["status"] = "error"
        info["message"] = f"rewrite failed: {e}"
        return info

    info["repaired"] = True
    info["stripped"] = offset
    info["status"] = "ok"
    info["timestamp"] = time.time()
    info["message"] = f"repaired — stripped {offset} bytes"

    _append_history({
        "ts":         info["timestamp"],
        "path":       str(p),
        "stripped":   offset,
        "size_before": original_size,
        "size_after": original_size - offset,
        "magic":      (info.get("magic") or b"").decode(errors="replace"),
    })
    return info


# ── v2: structural WAV damage — garbage AFTER the data chunk ─────
# The 2026-06 regression: mutagen's WAVE wrapper appends an "id3 "
# chunk after "data"; Rekordbox 7 / Engine DJ refuse the file. The v1
# inspect() above can't see it (the RIFF magic IS at offset 0), so
# this second pass walks the chunk list itself.

_TAILS_DIR = DATA_DIR / "repair_tails"

# Chunks that legitimately live after `data` in the wild. If the tail
# contains ONLY those we flag `review` and refuse to cut — only
# known-garbage tails are auto-repairable.
_LEGIT_TRAILING_IDS = {b"LIST", b"cue ", b"smpl", b"fact", b"bext",
                       b"PEAK", b"acid", b"inst"}

_MAX_CHUNKS = 4096


def inspect_chunks(path: str | Path) -> dict:
    """WAV-only structural check — the counterpart of inspect() for
    damage AFTER the data chunk.

    Returns dict with keys:
      status:   'ok' | 'trailing_garbage' | 'riff_size_mismatch' |
                'review' | 'no_data_chunk' | 'not_wav' | 'error'
      data_end: int — file offset just past the data chunk (0 if none)
      trailing: list[{id, offset, size}] — chunks found after data
      riff_size / file_size: header claim vs reality
    """
    p = Path(path)
    out: dict = {"path": str(p), "kind": "trailing", "status": "ok",
                 "file_size": 0, "riff_size": 0, "data_end": 0,
                 "trailing": [], "message": ""}
    try:
        out["file_size"] = p.stat().st_size
        with open(p, "rb") as f:
            header = f.read(12)
            if (len(header) < 12 or header[:4] != b"RIFF"
                    or header[8:12] != b"WAVE"):
                out["status"] = "not_wav"
                out["message"] = "no RIFF/WAVE header at offset 0"
                return out
            out["riff_size"] = int.from_bytes(header[4:8], "little")
            pos = 12
            data_end = 0
            for _ in range(_MAX_CHUNKS):
                f.seek(pos)
                head = f.read(8)
                if len(head) < 8:
                    break
                cid = head[:4]
                csize = int.from_bytes(head[4:8], "little")
                payload_end = pos + 8 + csize + (csize % 2)
                if data_end:
                    out["trailing"].append(
                        {"id": cid.decode("latin-1"), "offset": pos,
                         "size": csize})
                if cid == b"data" and not data_end:
                    data_end = min(payload_end, out["file_size"])
                pos = payload_end
                if pos >= out["file_size"]:
                    break
    except OSError as e:
        out["status"] = "error"
        out["message"] = f"read failed: {e}"
        return out

    if not data_end:
        out["status"] = "no_data_chunk"
        out["message"] = "data chunk not found — refusing to touch"
        return out
    out["data_end"] = data_end

    if out["trailing"]:
        ids = {t["id"].encode("latin-1") for t in out["trailing"]}
        names = ", ".join(sorted(t["id"] for t in out["trailing"]))
        if ids <= _LEGIT_TRAILING_IDS:
            out["status"] = "review"
            out["message"] = (f"legitimate-looking chunk(s) after data "
                              f"({names}) — left untouched")
        else:
            out["status"] = "trailing_garbage"
            out["message"] = (f"{len(out['trailing'])} chunk(s) after data "
                              f"({names}), "
                              f"{out['file_size'] - data_end} bytes to cut")
    elif out["riff_size"] != out["file_size"] - 8:
        out["status"] = "riff_size_mismatch"
        out["message"] = (f"RIFF header claims {out['riff_size']} bytes, "
                          f"file has {out['file_size'] - 8}")
    else:
        out["message"] = "chunk layout is clean"
    return out


def repair_trailing(path: str | Path) -> dict:
    """Cut everything past the data chunk and fix the RIFF size.

    The removed tail is saved under ``data/repair_tails/`` and referenced
    from ``repair_history.json`` — the repair is fully reversible via
    undo_trailing() at a few KB of disk instead of a full file copy.
    Refuses `review` files (legitimate trailing chunks). Idempotent.
    """
    info = inspect_chunks(path)
    info["repaired"] = False
    info["tail_file"] = None
    if info["status"] not in ("trailing_garbage", "riff_size_mismatch"):
        return info

    p = Path(path)
    data_end = int(info["data_end"])
    tail_file: Path | None = None
    tmp = p.with_suffix(p.suffix + ".tmp_repair")
    try:
        if info["file_size"] > data_end:
            _TAILS_DIR.mkdir(parents=True, exist_ok=True)
            with open(p, "rb") as f:
                f.seek(data_end)
                tail = f.read()
            digest = hashlib.sha1(
                str(p).encode("utf-8", "replace")).hexdigest()[:16]
            tail_file = _TAILS_DIR / f"{digest}-{int(time.time())}.bin"
            tail_file.write_bytes(tail)

        with open(p, "rb") as src, open(tmp, "wb") as dst:
            remaining = data_end
            while remaining > 0:
                chunk = src.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                dst.write(chunk)
                remaining -= len(chunk)
            dst.seek(4)
            dst.write((data_end - 8).to_bytes(4, "little"))
        os.replace(tmp, p)
    except OSError as e:
        try:
            tmp.unlink()
        except OSError:
            pass
        info["status"] = "error"
        info["message"] = f"rewrite failed: {e}"
        return info

    damage_kind = info["status"]
    info["repaired"] = True
    info["tail_file"] = str(tail_file) if tail_file else None
    info["timestamp"] = time.time()
    info["status"] = "ok"
    cut = info["file_size"] - data_end
    info["message"] = (f"repaired — cut {cut} trailing bytes, "
                       f"RIFF size set to {data_end - 8}")

    _append_history({
        "ts":               info["timestamp"],
        "path":             str(p),
        "kind":             damage_kind,
        "cut_bytes":        cut,
        "size_before":      info["file_size"],
        "size_after":       data_end,
        "riff_size_before": info["riff_size"],
        "tail_file":        info["tail_file"],
    })
    return info


def undo_trailing(path: str | Path, tail_file: str | Path,
                  riff_size_before: int | None = None) -> dict:
    """Reverse a repair_trailing(): re-append the saved tail and restore
    the original RIFF size field. Byte-identical restoration."""
    p, t = Path(path), Path(tail_file)
    out = {"path": str(p), "restored": False, "message": ""}
    if not t.exists():
        out["message"] = f"tail file missing: {t}"
        return out
    try:
        with open(p, "ab") as f:
            f.write(t.read_bytes())
        if riff_size_before is not None:
            with open(p, "r+b") as f:
                f.seek(4)
                f.write(int(riff_size_before).to_bytes(4, "little"))
        out["restored"] = True
        out["message"] = "tail re-appended"
    except OSError as e:
        out["message"] = f"undo failed: {e}"
    return out


def purge_backups(root: str | Path) -> int:
    """Delete every ``*.bak`` left over from previous app versions.

    Returns the count of files removed. Designed to free disk space
    after the repair pipeline switched from physical backups to an
    on-disk history log.
    """
    root_p = Path(root)
    if not root_p.is_dir():
        return 0
    removed = 0
    for bak in root_p.rglob("*.bak"):
        # Only remove .bak that match an audio-file sibling
        sibling = bak.with_suffix("")
        if sibling.suffix.lower() in (".wav", ".flac", ".m4a", ".mp4",
                                       ".ogg", ".oga", ".opus", ".mp3",
                                       ".aac"):
            try:
                bak.unlink()
                removed += 1
            except OSError:
                pass
    return removed


def scan_folder(
    root: str | Path,
    *,
    on_progress: Callable[[str, int, int], None] | None = None,
    extensions: tuple[str, ...] = (".wav", ".flac", ".m4a", ".ogg", ".oga"),
    dry_run: bool = False,
) -> dict:
    """Walk `root`, repair every corrupt file. Returns summary dict.

    on_progress(filename, current, total) is called once per file so a
    UI thread can show progress.

    dry_run=True: only inspect, don't rewrite. Useful to preview damage
    before committing to bulk-repair.
    """
    root_p = Path(root)
    if not root_p.is_dir():
        return {"scanned": 0, "ok": 0, "corrupt": 0, "repaired": 0,
                "errors": 0, "details": [],
                "message": f"not a directory: {root}"}

    files = [p for p in root_p.rglob("*")
             if p.suffix.lower() in extensions and p.is_file()]
    total = len(files)

    summary = {"scanned": total, "ok": 0, "corrupt": 0, "repaired": 0,
               "trailing_corrupt": 0, "review": 0,
               "errors": 0, "details": [], "dry_run": dry_run}

    for i, p in enumerate(files, 1):
        if on_progress:
            try:
                on_progress(p.name, i, total)
            except Exception:
                pass
        if dry_run:
            info = inspect(p)
        else:
            info = repair(p)
        st = info["status"]
        v1_counted_ok = st == "ok" and not info.get("repaired")
        if v1_counted_ok:
            summary["ok"] += 1
        elif st == "corrupt":
            summary["corrupt"] += 1
            summary["details"].append(info)
        elif st == "ok" and info.get("repaired"):
            summary["repaired"] += 1
            summary["details"].append(info)
        else:
            summary["errors"] += 1
            summary["details"].append(info)

        # v2 structural pass — only for WAVs whose prefix is sane
        # (a prefix-corrupt file can't be chunk-walked reliably).
        if p.suffix.lower() != ".wav" or st != "ok":
            continue
        chunk_info = inspect_chunks(p) if dry_run else repair_trailing(p)
        cst = chunk_info["status"]
        if chunk_info.get("repaired"):
            summary["repaired"] += 1
            summary["details"].append(chunk_info)
            if v1_counted_ok:
                summary["ok"] -= 1
        elif cst in ("trailing_garbage", "riff_size_mismatch"):
            summary["corrupt"] += 1
            summary["trailing_corrupt"] += 1
            summary["details"].append(chunk_info)
            if v1_counted_ok:
                summary["ok"] -= 1
        elif cst == "review":
            summary["review"] += 1
            summary["details"].append(chunk_info)
            if v1_counted_ok:
                summary["ok"] -= 1
        elif cst == "error":
            summary["errors"] += 1
            summary["details"].append(chunk_info)

    return summary
