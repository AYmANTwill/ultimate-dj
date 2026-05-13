"""
Spotify API client via spotipy.
Handles playlist, album, and single-track URLs.

Credentials are read from the Windows Credential Manager (via the
`secrets_store` module), NOT from config.json. The plaintext fallback
in config.json is kept for backward-compatibility but blanked on first
launch by the migration in `secrets_store.ensure_migrated()`.
"""
from __future__ import annotations

from app.logger import log_error
from app.secrets_store import get_spotify_credentials


def _get_client():
    cid, secret = get_spotify_credentials()
    if not cid or not secret:
        return None
    try:
        import spotipy
        from spotipy.oauth2 import SpotifyClientCredentials
        return spotipy.Spotify(auth_manager=SpotifyClientCredentials(
            client_id=cid, client_secret=secret,
        ))
    except Exception:
        return None


def is_configured() -> bool:
    cid, secret = get_spotify_credentials()
    return bool(cid and secret)


def is_editorial(url: str) -> bool:
    """Editorial playlists (37i9dQZF1…) are blocked by the Spotify API."""
    if "/playlist/" not in url:
        return False
    pid = url.split("/playlist/")[-1].split("?")[0]
    return pid.startswith("37i9dQZF1")


def url_id(url: str) -> str:
    """Stable Spotify resource ID extracted from any Spotify URL.
    Used as a cache key by `engine.playlist_sync`. Returns "" on
    unrecognised URL formats."""
    for kind in ("playlist", "album", "track"):
        if f"/{kind}/" in url:
            return _extract_id(url, kind)
    return ""


def _url_type(url: str) -> str:
    if "/playlist/" in url:  return "playlist"
    if "/album/"    in url:  return "album"
    if "/track/"    in url:  return "track"
    return "unknown"


def _extract_id(url: str, kind: str) -> str:
    return url.split(f"/{kind}/")[-1].split("?")[0].split("/")[0]


def _track_item(t: dict) -> dict | None:
    if not t or not t.get("id"):
        return None
    return {
        "spotify_id": t["id"],          # used by playlist_sync to match
                                        # downloaded files vs the source
                                        # playlist on re-sync
        "title":    t["name"],
        "artist":   ", ".join(a["name"] for a in t.get("artists", [])),
        "duration": int(t.get("duration_ms", 0) / 1000),
    }


# ── Public API ──────────────────────────────────────────────────

def fetch_playlist(url: str) -> tuple[str, list[dict], str]:
    """
    Fetch track list from any Spotify URL: playlist, album, or single track.
    Returns (name, tracks, error_msg).  error_msg is "" on success.
    """
    sp = _get_client()
    if not sp:
        cid, secret = get_spotify_credentials()
        if not cid or not secret:
            return "", [], "Spotify credentials not configured — go to Settings"
        return "", [], "Could not create Spotify client (check credentials)"

    kind = _url_type(url)
    if kind == "unknown":
        return "", [], "Unsupported Spotify URL — must be a playlist, album, or track link"

    try:
        if kind == "track":
            return _fetch_track(sp, url)
        if kind == "album":
            return _fetch_album(sp, url)
        return _fetch_playlist_data(sp, url)
    except Exception as e:
        err = str(e)
        log_error(f"Spotify fetch failed ({kind}): {url}", e)
        if "404" in err or "Resource not found" in err:
            return "", [], f"{kind.title()} not found (404) — check the URL"
        return "", [], f"Spotify API error: {err[:150]}"


def _fetch_track(sp, url: str) -> tuple[str, list[dict], str]:
    tid   = _extract_id(url, "track")
    data  = sp.track(tid)
    item  = _track_item(data)
    if not item:
        return "", [], "Track not found or unavailable"
    name = f"{item['artist']} — {item['title']}"
    return name, [item], ""


def _fetch_album(sp, url: str) -> tuple[str, list[dict], str]:
    aid    = _extract_id(url, "album")
    album  = sp.album(aid)
    name   = album["name"]
    artist = ", ".join(a["name"] for a in album.get("artists", []))
    label  = f"{artist} — {name}" if artist else name

    page   = album["tracks"]
    items  = list(page["items"])
    while page.get("next"):
        page = sp.next(page)
        items.extend(page["items"])

    tracks = [t for t in (_track_item(i) for i in items) if t]
    return label, tracks, "" if tracks else f"Album '{name}' has no accessible tracks"


def _fetch_playlist_data(sp, url: str) -> tuple[str, list[dict], str]:
    pid   = _extract_id(url, "playlist")
    data  = sp.playlist(pid)
    name  = data["name"]
    page  = data["tracks"]
    items = list(page["items"])
    while page.get("next"):
        page = sp.next(page)
        items.extend(page["items"])

    tracks = [t for t in (_track_item(i.get("track")) for i in items) if t]
    return name, tracks, "" if tracks else "Playlist is empty or all tracks are unavailable"


def format_duration(seconds: int) -> str:
    return f"{seconds // 60}:{seconds % 60:02d}"
