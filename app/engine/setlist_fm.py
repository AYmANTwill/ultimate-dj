"""
setlist.fm fallback — Plan B corpus source when 1001tracklists
rate-limits us (risk #2 in docs/AUDIT.md; it fired on 2026-07-05).

REST API, no scraping, no Playwright: https://api.setlist.fm/rest/1.0
Requires a free API key (https://www.setlist.fm/settings/api) stored in
config.json under ``setlistfm_api_key``.

Output is mapped to the exact tracklist-dict shape the cooccurrence
layer already consumes from data/tracklists/*.json, so rebuild() mines
setlist.fm sets with zero changes:

    {url, title, dj, tracks: [{position, artist, title, raw}],
     scraped_at, source}

Public API:
    is_configured() -> bool
    search_artist_sets(artist, *, limit=10) -> list[dict]  (raw setlists)
    to_tracklist(setlist) -> dict | None                   (mapped shape)
    fetch_and_cache(artist, *, limit=10) -> list[Path]     (cache writer)
"""
from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

from app.config import DATA_DIR, load_config
from app.logger import log_info, log_warning

_API_ROOT = "https://api.setlist.fm/rest/1.0"
_CACHE_DIR = DATA_DIR / "tracklists"


def is_configured() -> bool:
    return bool(load_config().get("setlistfm_api_key", "").strip())


def _http_get_json(url: str) -> dict:
    """GET with the API-key header. Separated so tests can monkeypatch."""
    key = load_config().get("setlistfm_api_key", "").strip()
    req = urllib.request.Request(url, headers={
        "x-api-key": key,
        "Accept": "application/json",
        "User-Agent": "UltimateDJ/1.4",
    })
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def search_artist_sets(artist: str, *, limit: int = 10) -> list[dict]:
    """Most recent raw setlists for an artist name. Empty list when
    unconfigured or on API failure (logged, never raises)."""
    if not is_configured():
        log_warning("setlist_fm: no API key configured — skipping")
        return []
    q = urllib.parse.quote(artist)
    url = f"{_API_ROOT}/search/setlists?artistName={q}&p=1"
    try:
        data = _http_get_json(url)
    except Exception as e:
        log_warning(f"setlist_fm: search failed for '{artist}': {e}")
        return []
    return list(data.get("setlist", []))[:limit]


def to_tracklist(setlist: dict) -> dict | None:
    """Map one setlist.fm setlist to the cooccurrence cache shape.

    setlist.fm marks covers with the ORIGINAL artist under song.cover —
    for pair-mining that original artist IS the track identity, so it
    wins over the performing artist. Returns None when fewer than 2
    usable songs remain (no pairs to mine)."""
    artist = ((setlist.get("artist") or {}).get("name") or "").strip()
    venue = ((setlist.get("venue") or {}).get("name") or "").strip()
    date = (setlist.get("eventDate") or "").strip()
    url = (setlist.get("url") or "").strip()
    tracks: list[dict] = []
    pos = 0
    for s in (setlist.get("sets") or {}).get("set", []):
        for song in s.get("song", []):
            name = (song.get("name") or "").strip()
            if not name:
                continue
            cover = ((song.get("cover") or {}).get("name") or "").strip()
            t_artist = cover or artist
            pos += 1
            tracks.append({"position": pos, "artist": t_artist,
                           "title": name,
                           "raw": f"{t_artist} - {name}"})
    if len(tracks) < 2:
        return None
    title = " @ ".join(x for x in (artist, venue) if x)
    if date:
        title = f"{title} {date}".strip()
    return {"url": url or f"setlistfm:{artist}:{date}",
            "title": title, "dj": artist,
            "tracks": tracks, "scraped_at": int(time.time()),
            "source": "setlist.fm"}


def _slug(s: str) -> str:
    out = re.sub(r"[^a-zA-Z0-9]+", "-", s.lower()).strip("-")
    return out[:80] or "set"


def fetch_and_cache(artist: str, *, limit: int = 10) -> list[Path]:
    """Search + map + write into data/tracklists/ so the next
    cooccurrence rebuild picks the sets up. Returns the written paths."""
    written: list[Path] = []
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    for sl in search_artist_sets(artist, limit=limit):
        tl = to_tracklist(sl)
        if tl is None:
            continue
        name = f"setlistfm-{_slug(tl['dj'])}-{_slug(tl.get('url', ''))}.json"
        p = _CACHE_DIR / name
        p.write_text(json.dumps(tl, ensure_ascii=False, indent=2),
                     encoding="utf-8")
        written.append(p)
    log_info(f"setlist_fm: cached {len(written)} sets for '{artist}'")
    return written
