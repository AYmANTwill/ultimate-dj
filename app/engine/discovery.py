"""
AI Music Discovery engine.

- Scrapes 1001tracklists for tracks played at big events
- Learns user taste profile from liked songs (BPM/key/energy/genre patterns)
- Uses Spotify recommendations API with seed tracks
- Iterative refinement: keep/discard -> regenerate ("Playlist Akinator")
"""
from __future__ import annotations

import json
import random
import re
import sqlite3
from pathlib import Path
from typing import Optional

from app.config import DATA_DIR, load_config

TASTE_DB = DATA_DIR / "taste_profile.json"
SETLIST_CACHE = DATA_DIR / "setlist_cache.json"


# ══════════════════════════════════════════════════════════════════
# Taste Profile — learns from user's liked/disliked tracks
# ══════════════════════════════════════════════════════════════════

def _load_taste() -> dict:
    if TASTE_DB.exists():
        try:
            return json.loads(TASTE_DB.read_text())
        except Exception as e:
            from app.logger import log_warning
            log_warning(f"discovery: taste profile corrompu, reset "
                        f"({TASTE_DB.name}): {e}")
    return {
        "liked_artists": {},       # artist -> count
        "liked_bpm_range": [120, 150],
        "liked_energy_range": [4, 9],
        "liked_keys": {},          # camelot code -> count
        "liked_titles": [],        # titles the user explicitly kept
        "disliked_titles": [],     # titles the user discarded
        "genres": {},              # genre keyword -> count
        "description": "",         # user's free-text style description
        "iterations": 0,
    }


def _save_taste(taste: dict):
    TASTE_DB.write_text(json.dumps(taste, indent=2))


def record_like(track: dict, taste: dict | None = None) -> dict:
    """Record that the user liked a track."""
    if taste is None:
        taste = _load_taste()

    artist = track.get("artist", "")
    for a in artist.split(","):
        a = a.strip()
        if a:
            taste["liked_artists"][a] = taste["liked_artists"].get(a, 0) + 1

    bpm = track.get("bpm")
    if bpm:
        lo, hi = taste["liked_bpm_range"]
        taste["liked_bpm_range"] = [min(lo, bpm - 5), max(hi, bpm + 5)]

    energy = track.get("energy")
    if energy:
        lo, hi = taste["liked_energy_range"]
        taste["liked_energy_range"] = [min(lo, energy - 0.5), max(hi, energy + 0.5)]

    cam = track.get("camelot", "")
    if cam:
        taste["liked_keys"][cam] = taste["liked_keys"].get(cam, 0) + 1

    title = track.get("title", "")
    if title and title not in taste["liked_titles"]:
        taste["liked_titles"].append(title)

    _save_taste(taste)
    return taste


def record_dislike(track: dict, taste: dict | None = None) -> dict:
    """Record that the user discarded a track."""
    if taste is None:
        taste = _load_taste()
    title = track.get("title", "")
    if title and title not in taste["disliked_titles"]:
        taste["disliked_titles"].append(title)
    _save_taste(taste)
    return taste


def set_description(text: str):
    """Save user's free-text description of their style."""
    taste = _load_taste()
    taste["description"] = text
    _save_taste(taste)


def get_taste() -> dict:
    return _load_taste()


# ══════════════════════════════════════════════════════════════════
# Setlist scraping — 1001tracklists
# ══════════════════════════════════════════════════════════════════

def _load_setlist_cache() -> list[dict]:
    if SETLIST_CACHE.exists():
        try:
            return json.loads(SETLIST_CACHE.read_text())
        except Exception as e:
            from app.logger import log_warning
            log_warning(f"discovery: cache setlists corrompu, ignoré "
                        f"({SETLIST_CACHE.name}): {e}")
    return []


def _save_setlist_cache(data: list[dict]):
    SETLIST_CACHE.write_text(json.dumps(data, indent=2))


def scrape_1001tracklists(query: str = "", max_results: int = 50) -> list[dict]:
    """
    Scrape 1001tracklists.com for tracks from big events/DJs.
    Returns list of {title, artist, event, plays}.
    Falls back to cached data if scraping fails.
    """
    import urllib.request
    import urllib.parse

    results = []
    search_url = "https://www.1001tracklists.com/search/result.php"

    try:
        if query:
            params = urllib.parse.urlencode({"main_search": query, "search_selection": "2"})
            url = f"{search_url}?{params}"
        else:
            # Default: most-played tracks
            url = "https://www.1001tracklists.com/charts/top-tracks.html"

        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="ignore")

        # Parse track entries from HTML
        # Pattern: <span class="trackValue">Artist - Title</span>
        for match in re.finditer(
            r'class="trackValue"[^>]*>([^<]+)</span>', html
        ):
            raw = match.group(1).strip()
            if " - " in raw:
                artist, title = raw.split(" - ", 1)
            else:
                artist, title = "", raw
            results.append({
                "title": title.strip(),
                "artist": artist.strip(),
                "source": "1001tracklists",
            })
            if len(results) >= max_results:
                break

        # Also try to find track entries in different format
        if not results:
            for match in re.finditer(
                r'<meta[^>]*content="([^"]+)"[^>]*property="og:title"', html
            ):
                raw = match.group(1)
                results.append({"title": raw, "artist": "", "source": "1001tracklists"})

        if results:
            _save_setlist_cache(results)

    except Exception as e:
        from app.logger import log_warning
        log_warning(f"discovery: live scrape failed, serving cached "
                    f"setlists: {e}")
        results = _load_setlist_cache()

    return results[:max_results]


# ══════════════════════════════════════════════════════════════════
# Spotify Recommendations
# ══════════════════════════════════════════════════════════════════

def spotify_recommend(seed_tracks: list[str], limit: int = 20) -> list[dict]:
    """
    Use Spotify recommendations API with seed track names.
    Searches for each seed, then gets recommendations.
    Returns list of {title, artist, duration, spotify_id}.
    """
    cfg = load_config()
    cid = cfg.get("spotify_client_id", "")
    secret = cfg.get("spotify_client_secret", "")
    if not cid or not secret:
        return []

    try:
        import spotipy
        from spotipy.oauth2 import SpotifyClientCredentials
        sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(
            client_id=cid, client_secret=secret))
    except Exception as e:
        from app.logger import log_warning
        log_warning(f"discovery: Spotify client init failed: {e}")
        return []

    # Find Spotify track IDs for seeds
    seed_ids = []
    for name in seed_tracks[:5]:  # API limit: max 5 seeds
        try:
            r = sp.search(q=name, type="track", limit=1)
            items = r.get("tracks", {}).get("items", [])
            if items:
                seed_ids.append(items[0]["id"])
        except Exception:
            continue

    if not seed_ids:
        return []

    # Get recommendations
    try:
        taste = _load_taste()
        bpm_lo, bpm_hi = taste["liked_bpm_range"]
        recs = sp.recommendations(
            seed_tracks=seed_ids[:5],
            limit=limit,
            min_tempo=max(80, bpm_lo - 10),
            max_tempo=min(200, bpm_hi + 10),
        )
        tracks = []
        for t in recs.get("tracks", []):
            tracks.append({
                "title": t["name"],
                "artist": ", ".join(a["name"] for a in t["artists"]),
                "duration": int(t["duration_ms"] / 1000),
                "spotify_id": t["id"],
                "preview_url": t.get("preview_url") or "",
                "source": "spotify_recommend",
            })
        return tracks
    except Exception as e:
        from app.logger import log_warning
        log_warning(f"discovery: Spotify recommendations failed: {e}")
        return []


def search_spotify(query: str, limit: int = 20) -> list[dict]:
    """Search Spotify for tracks matching a query."""
    cfg = load_config()
    cid = cfg.get("spotify_client_id", "")
    secret = cfg.get("spotify_client_secret", "")
    if not cid or not secret:
        return []

    try:
        import spotipy
        from spotipy.oauth2 import SpotifyClientCredentials
        sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(
            client_id=cid, client_secret=secret))
        r = sp.search(q=query, type="track", limit=limit)
        tracks = []
        for t in r.get("tracks", {}).get("items", []):
            tracks.append({
                "title": t["name"],
                "artist": ", ".join(a["name"] for a in t["artists"]),
                "duration": int(t["duration_ms"] / 1000),
                "spotify_id": t["id"],
                "preview_url": t.get("preview_url") or "",
                "source": "spotify_search",
            })
        return tracks
    except Exception:
        return []


# ══════════════════════════════════════════════════════════════════
# Playlist Akinator — iterative smart generation
# ══════════════════════════════════════════════════════════════════

def generate_playlist(
    seed_songs: list[str] | None = None,
    style_desc: str = "",
    count: int = 20,
    kept_tracks: list[dict] | None = None,
) -> list[dict]:
    """
    Generate a discovery playlist based on:
    1. User's taste profile (liked artists, BPM, keys, energy)
    2. Seed songs provided
    3. Style description
    4. Tracks the user already kept (for iteration)

    Returns list of {title, artist, source, ...}.
    """
    taste = _load_taste()
    if style_desc:
        taste["description"] = style_desc
        _save_taste(taste)

    all_suggestions: list[dict] = []
    disliked = set(taste.get("disliked_titles", []))
    kept_titles = set(t.get("title", "") for t in (kept_tracks or []))

    # Source 1: Spotify recommendations from seeds
    seeds = seed_songs or []
    if not seeds and kept_tracks:
        seeds = [f"{t.get('artist','')} {t.get('title','')}" for t in kept_tracks[:5]]
    if not seeds:
        # Use liked artists as seeds
        top_artists = sorted(taste["liked_artists"].items(),
                             key=lambda x: -x[1])[:5]
        seeds = [a for a, _ in top_artists]

    if seeds:
        recs = spotify_recommend(seeds, limit=count)
        all_suggestions.extend(recs)

    # Source 2: Search based on style description + top artists
    search_terms = []
    if style_desc:
        search_terms.append(style_desc)
    for artist, _ in sorted(taste["liked_artists"].items(),
                             key=lambda x: -x[1])[:3]:
        search_terms.append(artist)

    for term in search_terms[:3]:
        results = search_spotify(term, limit=10)
        all_suggestions.extend(results)

    # Source 3: 1001tracklists scraping
    for term in (search_terms[:2] or ["top tracks"]):
        scraped = scrape_1001tracklists(term, max_results=15)
        all_suggestions.extend(scraped)

    # Deduplicate by title (case-insensitive)
    seen = set()
    unique = []
    for t in all_suggestions:
        key = t.get("title", "").lower().strip()
        if key and key not in seen and t.get("title") not in disliked:
            # Don't include tracks the user already kept
            if t.get("title") not in kept_titles:
                seen.add(key)
                unique.append(t)

    # Score and rank by taste match
    def score(track):
        s = 0
        artist = track.get("artist", "")
        for a in artist.split(","):
            a = a.strip()
            if a in taste["liked_artists"]:
                s += taste["liked_artists"][a] * 10
        if track.get("source") == "spotify_recommend":
            s += 5  # boost recommendations
        return s

    unique.sort(key=score, reverse=True)

    # Add variety — shuffle the bottom half slightly
    mid = len(unique) // 2
    if mid > 3:
        bottom = unique[mid:]
        random.shuffle(bottom)
        unique = unique[:mid] + bottom

    taste["iterations"] = taste.get("iterations", 0) + 1
    _save_taste(taste)

    return unique[:count]
