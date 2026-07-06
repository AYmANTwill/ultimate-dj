"""
Smart playlist re-sync — track which Spotify playlists were downloaded
where, so a re-download into the same folder ONLY fetches new entries
and proposes to clean up files that left the source playlist.

Cache shape (one JSON file per (playlist, folder) pair):
    {
        "playlist_url":  "https://open.spotify.com/playlist/<id>",
        "playlist_id":   "<id>",
        "playlist_name": "Friday Vibes",
        "folder":        "D:\\Music\\House",
        "last_synced":   1736370000,
        "tracks": [
            {
              "spotify_id": "abc",
              "artist":     "Carl Cox",
              "title":      "Phuture",
              "filepath":   "D:\\Music\\House\\Carl Cox - Phuture.mp3"
            },
            ...
        ]
    }

A diff against a fresh playlist fetch produces three lists:
    added   — in source, not in cache       → DOWNLOAD these
    kept    — in source, in cache, file OK  → SKIP
    removed — in cache, not in source       → ASK USER, optionally DELETE

If the cache references a file that's been deleted manually, that entry
falls through to "added" so the user re-downloads it (correct behaviour
for "I deleted this by mistake").
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import TypedDict

from app.config import DATA_DIR
from app.logger import log_warning


_CACHE_DIR = DATA_DIR / "playlist_cache"
_CACHE_DIR.mkdir(parents=True, exist_ok=True)


class CachedTrack(TypedDict):
    spotify_id: str
    artist: str
    title: str
    filepath: str


# ── Cache key + I/O ──────────────────────────────────────────────

def _key(playlist_id: str, folder: str | Path) -> str:
    """Stable filename for the (playlist, folder) pair. Folder is
    hashed because it can contain characters bad for filenames."""
    folder_norm = str(Path(folder).resolve()).lower()
    folder_hash = hashlib.sha1(folder_norm.encode("utf-8")).hexdigest()[:10]
    return f"{playlist_id}__{folder_hash}.json"


def _cache_path(playlist_id: str, folder: str | Path) -> Path:
    return _CACHE_DIR / _key(playlist_id, folder)


def load_cache(playlist_id: str, folder: str | Path) -> dict | None:
    """Return the cached snapshot or None if there's no prior sync."""
    if not playlist_id:
        return None
    p = _cache_path(playlist_id, folder)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        log_warning(f"playlist_sync.load_cache parse error: {e}")
        return None


def save_cache(playlist_url: str, playlist_id: str, playlist_name: str,
                folder: str | Path,
                tracks: list[CachedTrack]) -> Path:
    """Persist the post-sync state of a playlist→folder pair."""
    p = _cache_path(playlist_id, folder)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "playlist_url":  playlist_url,
        "playlist_id":   playlist_id,
        "playlist_name": playlist_name,
        "folder":        str(folder),
        "last_synced":   int(time.time()),
        "tracks":        list(tracks),
    }
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                 encoding="utf-8")
    return p


# ── Diff (what to download, what to keep, what to drop) ──────────

class Diff(TypedDict):
    added:   list[dict]          # source tracks NOT in cache → download
    kept:    list[CachedTrack]   # cached tracks still in source AND on disk
    removed: list[CachedTrack]   # cached but no longer in source → maybe delete
    missing: list[CachedTrack]   # cached but file disappeared from disk
                                 # (treated like "added" so the user
                                 # re-downloads them; surfaced for clarity)


def compute_diff(source_tracks: list[dict],
                 cache: dict | None) -> Diff:
    """source_tracks: from spotify.fetch_playlist (each has spotify_id).

    Returns the four-way classification described above. The output is
    safe to pass to ``download_tracks_by_search`` (just `added`) and to
    show to the user as a confirmation before mutating the disk.
    """
    src_ids = {t.get("spotify_id") for t in source_tracks
               if t.get("spotify_id")}
    cached_tracks = list((cache or {}).get("tracks", []))
    cached_by_id = {t.get("spotify_id"): t for t in cached_tracks}

    added: list[dict] = []
    kept: list[CachedTrack] = []
    missing: list[CachedTrack] = []

    # Single ordered walk over the SOURCE so `added` (the download
    # queue) and `kept` come out in playlist order — the set difference
    # this replaces scrambled the downloaded order vs Spotify.
    seen: set[str] = set()
    for t in source_tracks:
        sid = t.get("spotify_id")
        if not sid or sid in seen:
            continue
        seen.add(sid)
        ct = cached_by_id.get(sid)
        if ct is None:
            added.append(t)
            continue
        fp = ct.get("filepath", "")
        if fp and os.path.isfile(fp):
            kept.append(ct)
        else:
            # File is gone → re-download with the source's current
            # artist/title spelling; surfaced in `missing` for clarity.
            added.append(t)
            missing.append(ct)

    removed = [ct for ct in cached_tracks
               if ct.get("spotify_id") not in src_ids]

    return {
        "added":   added,
        "kept":    kept,
        "removed": removed,
        "missing": missing,
    }


def _norm(s: str) -> str:
    """Lowercased alnum, whitespace collapsed — the fuzzy key used to
    match a Spotify (artist, title) against a filename stem. Collapsing
    matters: stripping '-' from "Artist - Title" leaves double spaces
    that would defeat the substring check."""
    cleaned = "".join(ch.lower() for ch in s if ch.isalnum() or ch.isspace())
    return " ".join(cleaned.split())


_AUDIO_EXTS = (".mp3", ".wav", ".flac", ".m4a", ".ogg", ".oga",
               ".opus", ".aac")


def bootstrap_cache_from_folder(source_tracks: list[dict],
                                 folder: str | Path) -> dict | None:
    """Synthesise a cache for a folder that was downloaded BEFORE the
    sync system existed (or on another machine): fuzzy-match each source
    track against the audio files already sitting in ``folder``.

    Matched tracks then behave like ``kept`` in compute_diff, so a
    re-download of the same playlist only fetches the genuinely new
    songs instead of everything. Returns None when nothing matches
    (caller falls back to a full download)."""
    folder_p = Path(folder)
    if not folder_p.is_dir():
        return None
    by_stem: dict[str, str] = {}
    for p in folder_p.iterdir():
        if p.is_file() and p.suffix.lower() in _AUDIO_EXTS:
            by_stem[_norm(p.stem)] = str(p)
    if not by_stem:
        return None
    tracks: list[CachedTrack] = []
    for t in source_tracks:
        sid = t.get("spotify_id")
        if not sid:
            continue
        needle = _norm(f"{t.get('artist', '')} {t.get('title', '')}").strip()
        if not needle:
            continue
        for stem, path in by_stem.items():
            if needle in stem or stem in needle:
                tracks.append({"spotify_id": sid,
                               "artist": t.get("artist", ""),
                               "title": t.get("title", ""),
                               "filepath": path})
                break
    if not tracks:
        return None
    return {"playlist_id": "", "playlist_name": "",
            "folder": str(folder_p), "bootstrapped": True,
            "tracks": tracks}


# ── Disk-side helpers ────────────────────────────────────────────

def write_m3u(folder: str | Path, playlist_name: str,
              tracks: list[CachedTrack]) -> Path | None:
    """Materialise the Spotify playlist ORDER on disk as a .m3u8.

    Filenames alone can't carry the order (yt-dlp names files
    "Artist - Title", so any folder view sorts alphabetically) —
    Rekordbox / Engine / VLC import this file instead. Overwritten on
    every sync; entries whose file is gone are skipped. Never touches
    the audio files themselves."""
    safe = re.sub(r'[<>:"/\\|?*]+', "_", playlist_name).strip() or "playlist"
    p = Path(folder) / f"{safe}.m3u8"
    folder_res = Path(folder).resolve()
    lines = ["#EXTM3U"]
    for t in tracks:
        fp = t.get("filepath") or ""
        if not fp or not os.path.isfile(fp):
            continue
        try:
            ref = str(Path(fp).resolve().relative_to(folder_res))
        except ValueError:
            ref = str(fp)
        lines.append(f"#EXTINF:-1,{t.get('artist', '')} - "
                     f"{t.get('title', '')}")
        lines.append(ref)
    try:
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return p
    except OSError as e:
        log_warning(f"playlist_sync.write_m3u {p}: {e}")
        return None


def delete_files(tracks: list[CachedTrack]) -> tuple[int, int]:
    """Delete `tracks` files from disk. Returns (deleted, failed).
    Doesn't touch the SQLite library — caller is responsible for
    keeping that consistent (or the next library Sync will clean it
    via orphan removal)."""
    ok = fail = 0
    for t in tracks:
        fp = t.get("filepath")
        if not fp:
            continue
        try:
            if os.path.isfile(fp):
                os.remove(fp)
                ok += 1
        except OSError as e:
            fail += 1
            log_warning(f"playlist_sync.delete_files {fp}: {e}")
    return ok, fail


def merge_after_download(cache: dict | None,
                          source_tracks: list[dict],
                          downloaded_paths: list[str]) -> list[CachedTrack]:
    """Build the new cache.tracks list after a successful sync.

    Strategy: for each track in `source_tracks`, find the matching
    file path. Three sources of truth, in order:
      1. The freshly-downloaded paths (artist/title fuzzy match)
      2. The previous cache (same spotify_id)
      3. None — track skipped or download failed

    Tracks with no resolvable filepath are dropped from the cache so
    the next sync re-tries them as `added`.
    """
    cached_by_id = {t["spotify_id"]: t
                    for t in (cache or {}).get("tracks", [])}

    # Index downloaded paths by the shared fuzzy key so we can match a
    # Spotify track to its yt-dlp-produced filename.
    by_stem: dict[str, str] = {}
    for p in downloaded_paths:
        stem = _norm(Path(p).stem)
        by_stem[stem] = p

    out: list[CachedTrack] = []
    for t in source_tracks:
        sid = t.get("spotify_id")
        if not sid:
            continue
        # Try fuzzy match against just-downloaded files first
        needle = _norm(f"{t.get('artist','')} {t.get('title','')}")
        fp = ""
        # Exact substring match works for yt-dlp's typical filenames
        for stem, path in by_stem.items():
            if needle and (needle in stem or stem in needle):
                fp = path
                break
        # Fallback: re-use previous cache entry if file still exists
        if not fp and sid in cached_by_id:
            old_fp = cached_by_id[sid].get("filepath", "")
            if old_fp and os.path.isfile(old_fp):
                fp = old_fp
        if fp:
            out.append({
                "spotify_id": sid,
                "artist":     t.get("artist", ""),
                "title":      t.get("title", ""),
                "filepath":   fp,
            })
    return out
