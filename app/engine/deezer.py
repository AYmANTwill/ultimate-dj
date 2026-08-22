"""
Deezer as a playlist SOURCE — same role Spotify plays here.

Deezer's public REST API (api.deezer.com) serves playlist / album /
track metadata with no login and no token for public content. We read
the track list from there, then the existing search-based downloader
pulls the matching audio from YouTube — byte-for-byte the same path as
the Spotify flow. We never touch Deezer's own protected streams.

Public surface mirrors app.engine.spotify so the download page can
treat both the same way::

    fetch_playlist(url) -> (name, tracks, error)   # error "" on success
    url_id(url)         -> str | None              # cache key
    is_editorial(url)   -> bool                    # always False here

Each track dict matches the shared contract used by playlist_sync and
downloader.download_tracks_by_search::

    {"spotify_id": "dz:<id>", "title": str, "artist": str,
     "duration": int}

The id lives in the ``spotify_id`` field (the sync key's incidental
name) namespaced with a ``dz:`` prefix so it can never collide with a
real Spotify id if a folder ever mixes sources.
"""
from __future__ import annotations

import json
import re
import urllib.request
from urllib.error import URLError

from app.logger import log_error, log_warning

_API = "https://api.deezer.com"
_TIMEOUT = 20
_UA = "Mozilla/5.0 (UltimateDJ)"
# .../playlist/123 , .../album/123 , .../track/123  (optional /en/ locale)
_KIND_RE = re.compile(
    r"deezer\.com/(?:[a-z]{2}/)?(playlist|album|track)/(\d+)", re.I)


def is_editorial(url: str) -> bool:
    """Spotify has locked editorial playlists; Deezer has no such
    gate. Kept for a symmetric resolver interface."""
    return False


def _kind_and_id(url: str) -> tuple[str, str] | None:
    m = _KIND_RE.search(url or "")
    return (m.group(1).lower(), m.group(2)) if m else None


def url_id(url: str) -> str | None:
    """Stable id for the playlist-sync cache filename."""
    ki = _kind_and_id(_resolve_short(url))
    return f"dz-{ki[0]}-{ki[1]}" if ki else None


def _resolve_short(url: str) -> str:
    """Deezer share links (link.deezer.com/s/..., deezer.page.link/...)
    redirect to the canonical /playlist|album|track/ URL. Follow one
    hop so paste-from-mobile works. Returns the original on any error."""
    if not url or ("link.deezer.com" not in url
                   and "deezer.page.link" not in url):
        return url
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            return resp.geturl() or url
    except (URLError, OSError, ValueError) as e:
        log_warning(f"deezer: short-link resolve failed {url}: {e}")
        return url


def _get_json(path: str) -> dict:
    req = urllib.request.Request(f"{_API}/{path}",
                                 headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _track_item(t: dict) -> dict | None:
    if not t or not t.get("id") or not t.get("title"):
        return None
    artist = (t.get("artist") or {}).get("name", "") if isinstance(
        t.get("artist"), dict) else (t.get("artist") or "")
    return {
        "spotify_id": f"dz:{t['id']}",
        "title":      t["title"],
        "artist":     artist,
        "duration":   int(t.get("duration", 0) or 0),
    }


def fetch_playlist(url: str) -> tuple[str, list[dict], str]:
    """Fetch a track list from any Deezer playlist / album / track URL.
    Returns (name, tracks, error_msg); error_msg is "" on success."""
    ki = _kind_and_id(_resolve_short(url))
    if not ki:
        return "", [], ("Lien Deezer non reconnu — colle un lien "
                        "playlist, album ou titre Deezer.")
    kind, rid = ki
    try:
        if kind == "track":
            data = _get_json(f"track/{rid}")
            if data.get("error"):
                return "", [], _api_error(data)
            item = _track_item(data)
            if not item:
                return "", [], "Titre Deezer indisponible."
            name = f"{item['artist']} — {item['title']}"
            return name, [item], ""

        data = _get_json(f"{kind}/{rid}")
        if data.get("error"):
            return "", [], _api_error(data)
        name = data.get("title", "Deezer") or "Deezer"
        raw = (data.get("tracks") or {}).get("data") or []
        tracks = [it for it in (_track_item(t) for t in raw) if it]
        # Playlists over 100 tracks paginate via tracks.next.
        nxt = (data.get("tracks") or {}).get("next")
        pages = 0
        while nxt and pages < 40:
            try:
                more = _get_json(nxt.split(f"{_API}/", 1)[-1])
            except (URLError, OSError, ValueError):
                break
            tracks += [it for it in
                       (_track_item(t) for t in more.get("data") or []) if it]
            nxt = more.get("next")
            pages += 1
        if not tracks:
            return "", [], "Aucun titre trouvé dans ce lien Deezer."
        return name, tracks, ""
    except (URLError, OSError) as e:
        log_error(f"deezer fetch failed ({kind}): {url}", e)
        return "", [], f"Réseau Deezer indisponible : {str(e)[:120]}"
    except (ValueError, KeyError) as e:
        log_error(f"deezer parse failed ({kind}): {url}", e)
        return "", [], f"Réponse Deezer illisible : {str(e)[:120]}"


def _api_error(data: dict) -> str:
    err = data.get("error") or {}
    msg = err.get("message") if isinstance(err, dict) else str(err)
    return f"Deezer : {msg or 'ressource introuvable'}"
