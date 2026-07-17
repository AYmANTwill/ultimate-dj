"""
Bridge to the user's LOCAL Rekordbox installation (via pyrekordbox).

Rekordbox 6/7 keeps everything in an SQLCipher SQLite (master.db):
the library, and crucially the HISTORY — one session per set actually
played, tracks in play order. That history is the strongest possible
training signal for the transition AI: first-party co-plays, not
scraped strangers.

Services
--------
    is_available()        -> pyrekordbox importable AND a local
                             Rekordbox 6/7 database found
    import_history_sets() -> write every history session as a cached
                             tracklist (data/tracklists/*.json, same
                             shape as 1001tracklists scrapes) so the
                             existing cooccurrence rebuild ingests
                             them with zero new code paths
    live poller (Live-1) and ANLZ cue import (L3 ground truth) will
    land here too — same dependency, same access pattern.

Everything is READ-ONLY against Rekordbox. We never write to
master.db or any Pioneer file.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from app.config import DATA_DIR
from app.logger import log_info, log_warning

_CACHE_DIR = DATA_DIR / "tracklists"
_AUDIO_EXT_RE = re.compile(
    r"\.(mp3|wav|flac|m4a|aac|ogg|aiff?)\s*$", re.IGNORECASE)
_MIN_TRACKS_PER_SET = 4


def is_available() -> bool:
    try:
        import pyrekordbox  # noqa: F401
    except ImportError:
        return False
    return True


def _open_db():
    from pyrekordbox import Rekordbox6Database
    return Rekordbox6Database()


def _clean_title(title: str) -> str:
    """Rekordbox titles imported from bare files ARE filenames
    ('Noir _ Haze_Solomun.mp3') — strip the extension so the name
    matcher gets words, not suffixes."""
    return _AUDIO_EXT_RE.sub("", (title or "")).strip()


def _norm_path(p: str) -> str:
    try:
        return os.path.normcase(os.path.normpath(str(p)))
    except Exception:
        return str(p)


def _library_path_index() -> dict[str, tuple[str, str]]:
    """{normalised path -> (artist, title)} of OUR library. When a
    Rekordbox history entry points at the exact same file we emit OUR
    artist/title, guaranteeing the cooccurrence matcher a bullseye."""
    from app.engine.library import get_connection
    conn = get_connection()
    idx: dict[str, tuple[str, str]] = {}
    for r in conn.execute(
            "SELECT path, title FROM tracks "
            "WHERE COALESCE(source, 'user') = 'user'").fetchall():
        title = r["title"] or Path(r["path"]).stem
        artist = ""
        if " - " in title:
            artist, title = title.split(" - ", 1)
        idx[_norm_path(r["path"])] = (artist.strip(), title.strip())
    return idx


def import_history_sets(min_tracks: int = _MIN_TRACKS_PER_SET) -> dict:
    """Materialise every Rekordbox history session as a cached
    tracklist file. Idempotent: deterministic filenames, re-running
    overwrites the same files (no duplicates in the cache).

    Returns {sessions, written, skipped_short, tracks_total,
    matched_by_path}.
    """
    if not is_available():
        return {"error": "pyrekordbox non installé ou Rekordbox absent"}
    try:
        db = _open_db()
    except Exception as e:
        log_warning(f"rekordbox_bridge: ouverture master.db impossible: {e}")
        return {"error": f"ouverture master.db impossible : {e}"}

    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    lib_idx = _library_path_index()
    n_written = n_short = n_tracks = n_path_hits = 0
    hists = list(db.get_history())
    for h in hists:
        try:
            songs = sorted(db.get_history_songs(HistoryID=h.ID),
                           key=lambda s: int(s.TrackNo or 0))
        except Exception as e:
            log_warning(f"rekordbox_bridge: history {h.ID} illisible: {e}")
            continue
        tracks = []
        for s in songs:
            c = s.Content
            if c is None:
                continue
            artist = (getattr(c, "ArtistName", None) or "").strip()
            title = _clean_title(getattr(c, "Title", None) or "")
            folder = getattr(c, "FolderPath", None) or ""
            hit = lib_idx.get(_norm_path(folder)) if folder else None
            if hit:
                artist, title = hit[0] or artist, hit[1] or title
                n_path_hits += 1
            if not title:
                continue
            tracks.append({"artist": artist, "title": title})
        if len(tracks) < min_tracks:
            n_short += 1
            continue
        payload = {
            "url": f"rekordbox://history/{h.ID}",
            "dj": "Mes sets (Rekordbox)",
            "date": str(getattr(h, "DateCreated", "") or ""),
            "tracks": tracks,
        }
        out = _CACHE_DIR / f"rekordbox-history-{h.ID}.json"
        out.write_text(
            json.dumps(payload, ensure_ascii=False, indent=1),
            encoding="utf-8")
        n_written += 1
        n_tracks += len(tracks)

    log_info(f"rekordbox_bridge: {n_written}/{len(hists)} sessions "
             f"importées ({n_tracks} lignes, {n_path_hits} matchées "
             f"par chemin exact), {n_short} trop courtes ignorées")
    return {"sessions": len(hists), "written": n_written,
            "skipped_short": n_short, "tracks_total": n_tracks,
            "matched_by_path": n_path_hits}
