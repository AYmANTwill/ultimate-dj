"""
1001tracklists.com — read tracklists from URLs and match them against
the local library.

Phase 1 (this file):
    fetch_tracklist(url) -> dict   — parse one tracklist page → JSON
    match_with_library(tl, conn)   — fuzzy-match parsed tracks vs DB
    cache_tracklist(tl)            — persist locally so we don't re-scrape

Strategy:
    cloudscraper handles the basic Cloudflare anti-bot challenge that
    plain `requests` trips on. For sites with the harder JS challenge
    we'd need playwright / undetected-chromedriver — to upgrade later
    if cloudscraper hits a dead end.

Cache:
    `data/tracklists/<slug>.json` keeps the raw parsed tracklist so a
    library re-scan doesn't refetch. The cache key is the page slug
    (everything between /tracklist/<id>/ and .html), which is stable.

Politeness:
    - 5 second min delay between fetches enforced inside fetch_tracklist
    - User-Agent rotates across fetches
    - Respect robots.txt: /tracklist/ paths are allowed for read

The scraper is intentionally conservative — single URL, no batch yet.
The Phase 2 batch + Phase 3 enrichment build on this.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from app.config import DATA_DIR
from app.logger import log_warning, log_info


_CACHE_DIR = DATA_DIR / "tracklists"
_CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Politeness: at least this many seconds between live fetches
_MIN_FETCH_INTERVAL = 5.0
_last_fetch_at = 0.0


# ── URL parsing / cache key ──────────────────────────────────────

def slug_from_url(url: str) -> str:
    """`https://www.1001tracklists.com/tracklist/12345xyz/foo-bar.html`
    → `12345xyz_foo-bar`. Used as the on-disk cache key."""
    p = urlparse(url)
    parts = [seg for seg in p.path.split("/") if seg]
    if len(parts) >= 3 and parts[0] == "tracklist":
        slug_id = parts[1]
        title = parts[2].replace(".html", "")
        return f"{slug_id}_{title}"
    # Last-resort key — sanitise the full path
    return re.sub(r"[^a-z0-9_-]+", "_", p.path.strip("/").lower())


def _cache_path(url: str) -> Path:
    return _CACHE_DIR / f"{slug_from_url(url)}.json"


# ── Live fetch + parse ───────────────────────────────────────────

_USER_AGENTS = [
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
     "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"),
    ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
     "(KHTML, like Gecko) Version/17.0 Safari/605.1.15"),
    ("Mozilla/5.0 (X11; Linux x86_64; rv:122.0) Gecko/20100101 Firefox/122.0"),
]


def _scraper():
    """Lazy-import cloudscraper so a missing dep doesn't break the
    whole engine. Returns None on failure."""
    try:
        import cloudscraper
        return cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "windows", "mobile": False},
            delay=10,
        )
    except Exception as e:
        log_warning(f"cloudscraper unavailable: {e}")
        return None


def _wait_polite():
    """Block until enough time has passed since the previous fetch."""
    global _last_fetch_at
    elapsed = time.time() - _last_fetch_at
    if elapsed < _MIN_FETCH_INTERVAL:
        time.sleep(_MIN_FETCH_INTERVAL - elapsed)
    _last_fetch_at = time.time()


def fetch_tracklist(url: str, *, use_cache: bool = True) -> dict:
    """Read a 1001tracklists URL and return:
        {
            url:       str,
            title:     str,             — DJ + set name from the page
            dj:        str,             — first artist tag
            tracks:    list[dict],      — see _parse_tracks()
            scraped_at: int,            — unix timestamp
            cached:    bool,
        }

    Raises RuntimeError on network / parsing failures.
    """
    cached_path = _cache_path(url)
    if use_cache and cached_path.exists():
        try:
            data = json.loads(cached_path.read_text(encoding="utf-8"))
            data["cached"] = True
            return data
        except Exception:
            pass    # fall through and refetch

    sc = _scraper()
    if sc is None:
        raise RuntimeError("cloudscraper not installed — cannot fetch")

    _wait_polite()
    headers = {"User-Agent": _USER_AGENTS[int(time.time()) % len(_USER_AGENTS)]}
    try:
        resp = sc.get(url, headers=headers, timeout=30)
    except Exception as e:
        raise RuntimeError(f"fetch failed: {e}") from e
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code} from 1001tracklists")

    parsed = _parse_html(resp.text, url=url)
    parsed["scraped_at"] = int(time.time())
    parsed["cached"] = False

    try:
        cached_path.write_text(
            json.dumps(parsed, ensure_ascii=False, indent=2),
            encoding="utf-8")
    except Exception:
        pass    # cache write failure is non-fatal

    log_info(f"tracklists.fetch_tracklist: {parsed.get('title')} — "
             f"{len(parsed.get('tracks', []))} tracks")
    return parsed


def _parse_html(html: str, *, url: str) -> dict:
    """Extract tracklist metadata from a 1001tracklists page.

    The site's HTML evolves; this parser is conservative — if a field
    isn't found we return empty rather than crashing. Tracks are matched
    by structural CSS selectors that have been stable for years."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "lxml")

    # Title — page <h1>
    title_el = soup.find("h1")
    title = title_el.get_text(strip=True) if title_el else ""
    # DJ — first link in the breadcrumb / artist section
    dj = ""
    artist_el = soup.find("a", href=re.compile(r"^/dj/"))
    if artist_el:
        dj = artist_el.get_text(strip=True)

    tracks = _parse_tracks(soup)
    return {
        "url":    url,
        "title":  title,
        "dj":     dj,
        "tracks": tracks,
    }


def _parse_tracks(soup) -> list[dict]:
    """Pull the list of tracks from the page.

    1001tracklists uses container divs like ``<div class="tlpItem ...">``
    that hold the artist + title via spans like ``.trackFormat`` /
    nested anchors. We look for the most reliable signals — track name
    block + the artist link — and accept missing pieces gracefully.

    Returns list of {position, artist, title, time, label, raw}.
    """
    tracks: list[dict] = []
    rows = soup.select("div.tlpItem")
    for i, row in enumerate(rows, 1):
        # Skip non-track rows (segue markers, talk breaks, etc.)
        if "tlpItemNonTrack" in (row.get("class") or []):
            continue

        text_el = row.select_one(".trackValue") or row.select_one(".tlToogleData")
        if text_el is None:
            text_el = row
        raw = text_el.get_text(" ", strip=True)
        if not raw:
            continue

        # Heuristic split: "Artist - Title" or "Artist & Other - Title (Label)"
        # 1001tracklists usually formats with a clean " - " separator.
        artist = ""
        title = raw
        if " - " in raw:
            artist, _, rest = raw.partition(" - ")
            title = rest.strip()

        # Time-in-set marker (mm:ss) if present
        t_el = row.select_one(".cueValueField") or row.select_one(".cueValue")
        time_in = t_el.get_text(strip=True) if t_el else ""

        # Label / release info, when 1001tracklists includes a tag
        label_el = row.select_one(".labelValue")
        label = label_el.get_text(strip=True) if label_el else ""

        tracks.append({
            "position": i,
            "artist":   artist.strip(),
            "title":    title.strip(),
            "time":     time_in,
            "label":    label.strip(),
            "raw":      raw,
        })
    return tracks


# ── Match scraped tracks against the local library ───────────────

def _normalise(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"\(.*?\)|\[.*?\]", " ", s)        # drop parenthetical info
    s = re.sub(r"feat\.?|ft\.?|vs\.?", " ", s)    # drop feat/vs markers
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(s.split())


def _ratio(a: str, b: str) -> float:
    """Cheap fuzzy similarity in [0,1]. SequenceMatcher is in stdlib so
    we don't pull in fuzzywuzzy/rapidfuzz for one helper."""
    from difflib import SequenceMatcher
    return SequenceMatcher(None, a, b).ratio()


def match_with_library(tl: dict, conn,
                        threshold: float = 0.72) -> list[dict]:
    """For each scraped track, find the best match in the local DB.

    Returns a list of dicts:
        {position, scraped, match (track or None), score}

    The match.score is a fuzzy ratio in [0,1]; entries below `threshold`
    have match=None. Use this for Phase 4 recommendations (boost local
    tracks that show up in many scraped sets).
    """
    rows = conn.execute(
        "SELECT path, title FROM tracks").fetchall()
    library = [(r["path"], _normalise(r["title"]),
                  r["title"] or "")
               for r in rows]

    out = []
    for s in tl.get("tracks", []):
        needle = _normalise(f"{s.get('artist','')} {s.get('title','')}")
        best = (None, 0.0)
        for path, norm_title, raw_title in library:
            score = _ratio(needle, norm_title)
            if score > best[1]:
                best = ((path, raw_title), score)
        match = None
        if best[0] is not None and best[1] >= threshold:
            path, raw_title = best[0]
            match = {"path": path, "title": raw_title}
        out.append({
            "position": s.get("position"),
            "scraped":  s,
            "match":    match,
            "score":    round(best[1], 3),
        })
    return out


def cache_tracklist(tl: dict) -> Path:
    """Force-write the parsed tracklist to the cache folder."""
    p = _cache_path(tl["url"])
    p.write_text(json.dumps(tl, ensure_ascii=False, indent=2),
                 encoding="utf-8")
    return p


def list_cached_tracklists() -> list[dict]:
    """Enumerate the cached tracklists with summary metadata."""
    out = []
    for f in sorted(_CACHE_DIR.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            out.append({
                "slug": f.stem,
                "url":  data.get("url", ""),
                "title": data.get("title", ""),
                "dj":    data.get("dj", ""),
                "n_tracks": len(data.get("tracks", [])),
                "scraped_at": data.get("scraped_at", 0),
            })
        except Exception:
            continue
    return out
