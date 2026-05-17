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
import threading
import time
from pathlib import Path
from typing import Callable, Optional
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


# ── Playwright fallback (real headless browser) ─────────────────
#
# 1001tracklists renders all real content (DJ index, tracklist details)
# via JavaScript after page load. cloudscraper grabs only the initial
# HTML shell — which contains a "Please enable JavaScript" loader and
# no actual tracklist data. We use Playwright + headless Chromium as
# the fallback path: navigates, waits for content to render, then
# returns the post-JS DOM.
#
# Playwright is optional — if it's not installed, we fall back to the
# cloudscraper path and report 0 results clearly. ``pip install
# playwright && playwright install chromium`` enables it.

_PW_BROWSER = None         # cached Playwright browser instance
_PW_CTX = None             # cached browser context (cookies)
_PW_PLAYWRIGHT = None      # the playwright instance itself
_PW_LOCK = threading.Lock()


def _playwright_available() -> bool:
    try:
        import playwright    # noqa: F401
        return True
    except ImportError:
        return False


def _playwright_get_html(url: str, *,
                           wait_for_selector: str = "a[href*='/tracklist/']",
                           timeout_ms: int = 25_000) -> str | None:
    """Open `url` in a stealthed headless Chromium, wait for content
    to render, return the post-JS DOM as a string. Returns None on any
    failure (caller falls back / reports 0 results).

    1001tracklists serves a "Please wait, you will be forwarded"
    interstitial that ONLY redirects to the real page once it's
    convinced you're a real browser. Vanilla headless Chromium fails
    this check (navigator.webdriver = true, missing plugins, etc.).
    We use playwright-stealth to patch all the standard tells, so the
    forwarding script runs and we land on the real DJ / tracklist page.
    """
    global _PW_BROWSER, _PW_CTX, _PW_PLAYWRIGHT
    if not _playwright_available():
        return None
    with _PW_LOCK:
        try:
            if _PW_BROWSER is None:
                from playwright.sync_api import sync_playwright
                _PW_PLAYWRIGHT = sync_playwright().start()
                _PW_BROWSER = _PW_PLAYWRIGHT.chromium.launch(
                    headless=True,
                    args=["--disable-blink-features=AutomationControlled",
                           "--no-sandbox"])
                _PW_CTX = _PW_BROWSER.new_context(
                    user_agent=_USER_AGENTS[0],
                    viewport={"width": 1280, "height": 800},
                    locale="en-US",
                )
                # playwright-stealth patches the context to hide the
                # standard automation tells. Best-effort: if it's not
                # installed, the bare context still works for non-
                # protected pages.
                try:
                    from playwright_stealth import Stealth
                    Stealth().apply_stealth_sync(_PW_CTX)
                except ImportError:
                    log_warning("playwright_stealth not installed — "
                                "1001tracklists will probably reject "
                                "the headless browser")
                except Exception as e:
                    log_warning(f"stealth init failed: {e}")
            page = _PW_CTX.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded",
                          timeout=timeout_ms)
                # Wait for at least one tracklist link to render — this
                # is the cheapest "JS done" signal for both DJ index
                # pages and individual tracklist pages.
                try:
                    page.wait_for_selector(
                        wait_for_selector, timeout=timeout_ms)
                except Exception:
                    # Selector didn't appear — page might still have
                    # content (set without tracks? track page that uses
                    # a different selector?), so don't bail yet.
                    pass
                html = page.content()
                return html
            finally:
                page.close()
        except Exception as e:
            log_warning(f"playwright fetch failed for {url}: {e}")
            return None


def _shutdown_playwright():
    """Tear down the cached browser. Called from atexit."""
    global _PW_BROWSER, _PW_CTX, _PW_PLAYWRIGHT
    with _PW_LOCK:
        try:
            if _PW_BROWSER is not None:
                _PW_BROWSER.close()
            if _PW_PLAYWRIGHT is not None:
                _PW_PLAYWRIGHT.stop()
        except Exception:
            pass
        _PW_BROWSER = _PW_CTX = _PW_PLAYWRIGHT = None


import atexit as _atexit
_atexit.register(_shutdown_playwright)


def _looks_like_js_shell(html: str) -> bool:
    """Detect 1001tracklists' JS-rendered shell page: tiny content,
    JS loader markers, no real tracklist links."""
    if not html:
        return True
    if "tracklist/" in html:
        return False
    markers = ("Please enable JavaScript",
                "class=\"loader",
                "forwarding does not work")
    return any(m in html for m in markers)


class IPLimitedError(RuntimeError):
    """Raised when 1001tracklists returns its 'Your IP has been limited
    due to overuse' page. Distinct from other errors so callers can
    surface a specific message: the only fix is to wait or use a
    different network (VPN / different ISP)."""
    pass


def _looks_like_ip_ban(html: str) -> bool:
    """Detect the 1001tracklists rate-limit / IP-ban page so we can
    raise a distinct error instead of silently returning empty."""
    if not html:
        return False
    markers = (
        "Your IP or guest/user account has been limited",
        "due to overuse",
        "Fill out the captcha to unblock your IP",
    )
    return any(m in html for m in markers)


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

    # Try the cheap path first (cloudscraper) — it occasionally works
    # for cached / static responses. We immediately detect the
    # JS-shell case and escalate to playwright when needed.
    html: str | None = None
    sc = _scraper()
    if sc is not None:
        _wait_polite()
        headers = {"User-Agent":
                    _USER_AGENTS[int(time.time()) % len(_USER_AGENTS)]}
        try:
            resp = sc.get(url, headers=headers, timeout=30)
            if resp.status_code in (200, 206):
                html = resp.text
        except Exception as e:
            log_warning(f"cloudscraper failed for {url}: {e}")

    # If cloudscraper got nothing useful, escalate to playwright.
    if html is None or _looks_like_js_shell(html):
        if not _playwright_available():
            raise RuntimeError(
                "playwright not installed and cloudscraper returned a "
                "JS shell — pip install playwright && playwright install "
                "chromium to fix 1001tracklists scraping")
        log_info(f"fetch_tracklist: escalating to playwright for {url}")
        html = _playwright_get_html(url)
        if html is None:
            raise RuntimeError(
                f"playwright fetch failed for {url}")

    if _looks_like_ip_ban(html):
        raise IPLimitedError(
            "1001tracklists rate-limited this IP. Wait a few hours, "
            "use a VPN, or run from a different network.")

    parsed = _parse_html(html, url=url)
    parsed["scraped_at"] = int(time.time())
    parsed["cached"] = False

    if not parsed.get("tracks"):
        # Still empty? Don't cache nothing — re-raise so the caller can
        # surface the issue instead of silently storing an empty set.
        raise RuntimeError(
            f"fetched but parsed 0 tracks from {url} — page layout may "
            f"have changed or login is required")

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


# ── Phase 2: batch + DJ-discovery ────────────────────────────────

def _slug_variants(slug: str) -> list[str]:
    """Generate plausible 1001tracklists slug variants.

    The site is inconsistent: some DJs are at /dj/carl_cox/ (underscore),
    others at /dj/charlottedewitte/ (no separator), some at
    /dj/peggy-gou/ (hyphen). We try the user's input as-is first, then
    the two other conventions, so the user doesn't have to know which
    one the site picked for any given artist.
    """
    s = slug.strip().lower()
    if not s:
        return []
    base = s.replace("_", "").replace("-", "").replace(" ", "")
    variants = [s]
    for v in (s.replace("-", "_"), s.replace("_", "-"), base,
               s.replace("-", "").replace("_", "")):
        if v and v not in variants:
            variants.append(v)
    return variants


def _fetch_dj_index_html(dj_slug: str) -> tuple[str | None, str]:
    """Try the cloudscraper → playwright chain for one DJ slug. Returns
    (html, actual_slug_used).

    Raises ``IPLimitedError`` IMMEDIATELY if the IP-ban marker is seen
    in either the cloudscraper or the playwright response — propagating
    up at the earliest detection point so the loop in
    ``discover_dj_sets`` can't accidentally swallow it when later
    variants fail (e.g. cloudscraper 429 + playwright timeout returning
    None, which used to make the function return 0 silently).
    """
    url = f"https://www.1001tracklists.com/dj/{dj_slug}/index.html"
    cloud_html: str | None = None
    sc = _scraper()
    if sc is not None:
        _wait_polite()
        headers = {"User-Agent":
                    _USER_AGENTS[int(time.time()) % len(_USER_AGENTS)]}
        try:
            resp = sc.get(url, headers=headers, timeout=30)
            if resp.status_code in (200, 206):
                cloud_html = resp.text
        except Exception as e:
            log_warning(f"discover_dj_sets({dj_slug}) cloudscraper: {e}")

    # Sticky IP-ban detection #1: cloudscraper response.
    if cloud_html and _looks_like_ip_ban(cloud_html):
        raise IPLimitedError(
            f"1001tracklists rate-limited this IP (detected on "
            f"/dj/{dj_slug}/ via cloudscraper)")

    # If cloudscraper returned something usable (real content, no
    # shell), return it.
    if cloud_html is not None and not _looks_like_js_shell(cloud_html):
        return cloud_html, dj_slug

    # Otherwise escalate to playwright
    if _playwright_available():
        pw_html = _playwright_get_html(url)
        # Sticky IP-ban detection #2: playwright response.
        if pw_html and _looks_like_ip_ban(pw_html):
            raise IPLimitedError(
                f"1001tracklists rate-limited this IP (detected on "
                f"/dj/{dj_slug}/ via playwright)")
        if pw_html:
            return pw_html, dj_slug

    # Playwright failed too. Return cloudscraper's response if we have
    # ANYTHING — useful for downstream debugging.
    if cloud_html:
        return cloud_html, dj_slug
    return None, dj_slug


def discover_dj_sets(dj_slug: str, *, limit: int = 20) -> list[str]:
    """List recent set URLs from a DJ's profile page.

    `dj_slug` is the part of the URL right after /dj/. The site has
    no single naming convention (carl_cox, charlottedewitte,
    peggy-gou…), so we try the user's input first, then automatic
    variants (no-separator, hyphen↔underscore swap).

    Returns a list of full URLs to individual sets, newest first.
    Returns an empty list on any failure so callers can keep going
    across the artist list.

    Uses the playwright fallback when cloudscraper gets blocked by the
    site's JS-rendered shell (the modern default for 1001tracklists).
    Raises IPLimitedError if 1001tracklists rate-limited this IP, so
    callers can abort cleanly instead of looping over more artists.
    """
    from bs4 import BeautifulSoup
    import re as _re

    variants = _slug_variants(dj_slug)
    if not variants:
        return []

    last_html = ""
    for variant in variants:
        html, _ = _fetch_dj_index_html(variant)
        if html is None:
            continue
        last_html = html
        if _looks_like_ip_ban(html):
            raise IPLimitedError(
                f"1001tracklists rate-limited this IP "
                f"(detected on /dj/{variant}/)")
        soup = BeautifulSoup(html, "lxml")
        # Set links live in anchors whose href matches /tracklist/<id>/<slug>.html
        seen: set[str] = set()
        out: list[str] = []
        for a in soup.find_all("a", href=_re.compile(r"^/tracklist/")):
            href = a.get("href", "").split("?")[0]
            if not href.endswith(".html"):
                continue
            full = "https://www.1001tracklists.com" + href
            if full in seen:
                continue
            seen.add(full)
            out.append(full)
            if len(out) >= limit:
                break
        if out:
            log_info(
                f"discover_dj_sets({dj_slug} → {variant}): {len(out)} URLs")
            return out

    # All variants exhausted — emit a richer warning so the user knows
    # WHY (slug not found vs page layout changed vs JS shell)
    log_warning(
        f"discover_dj_sets({dj_slug}): tried {variants}, no tracklist "
        f"links found. Either the slug doesn't exist on 1001tracklists "
        f"or the page layout changed.")
    return []


def batch_scrape(urls: list[str], *,
                  on_progress: Callable[[int, int, str, str], None]
                                | None = None,
                  stop_event=None,
                  max_consecutive_fails: int = 6,
                  initial_backoff_s: float = 30.0,
                  ) -> dict:
    """Fetch a list of tracklist URLs, respecting the 5s rate-limit and
    backing off when 1001tracklists starts kicking us out.

    Skips URLs that are already cached. Reports progress via
    ``on_progress(i, total, status, title_or_error)`` where status is
    one of: "cache", "ok", "fail", "backoff", "aborted".

    Circuit breaker: after ``max_consecutive_fails`` failures in a row
    we pause for ``initial_backoff_s`` seconds (doubling each retry,
    capped at 30 min) and try again. After 4 backoffs without recovery
    we abort the whole run — Cloudflare has us flagged and continuing
    will just burn the session.

    Pass a ``threading.Event`` as stop_event to allow cancellation.

    Returns ``{"fetched": N, "cached": N, "failed": N, "aborted": bool}``.
    """
    fetched = cached = failed = 0
    total = len(urls)
    consec_fails = 0
    backoff_attempts = 0
    aborted = False
    backoff_s = initial_backoff_s

    for i, url in enumerate(urls, 1):
        if stop_event is not None and stop_event.is_set():
            log_info(f"batch_scrape: stopped at {i}/{total}")
            break
        # Cache hit → no network at all
        if _cache_path(url).exists():
            cached += 1
            consec_fails = 0    # cache hits reset the breaker
            if on_progress:
                try:
                    on_progress(i, total, "cache", url)
                except Exception:
                    pass
            continue
        try:
            tl = fetch_tracklist(url, use_cache=False)
            fetched += 1
            consec_fails = 0
            backoff_attempts = 0
            backoff_s = initial_backoff_s
            if on_progress:
                try:
                    on_progress(i, total, "ok",
                                 tl.get("title", "")[:60])
                except Exception:
                    pass
        except Exception as e:
            failed += 1
            consec_fails += 1
            log_warning(f"batch_scrape failed for {url}: {e}")
            if on_progress:
                try:
                    on_progress(i, total, "fail", str(e)[:80])
                except Exception:
                    pass
            if consec_fails >= max_consecutive_fails:
                backoff_attempts += 1
                if backoff_attempts > 4:
                    log_warning(
                        "batch_scrape: 4 backoffs without recovery — "
                        "aborting (Cloudflare likely has us flagged)")
                    aborted = True
                    if on_progress:
                        try:
                            on_progress(i, total, "aborted",
                                         "Cloudflare backoff exhausted")
                        except Exception:
                            pass
                    break
                wait_s = min(1800.0, backoff_s)
                log_warning(
                    f"batch_scrape: {consec_fails} consecutive fails, "
                    f"backing off {wait_s:.0f}s (attempt "
                    f"{backoff_attempts}/4)")
                if on_progress:
                    try:
                        on_progress(i, total, "backoff",
                                     f"pause {wait_s:.0f}s")
                    except Exception:
                        pass
                # Interruptible sleep
                slept = 0.0
                step = 1.0
                while slept < wait_s:
                    if stop_event is not None and stop_event.is_set():
                        break
                    time.sleep(step)
                    slept += step
                consec_fails = 0
                backoff_s *= 2.0
    log_info(f"batch_scrape done — fetched={fetched}, "
             f"cached={cached}, failed={failed}, aborted={aborted}")
    return {"fetched": fetched, "cached": cached, "failed": failed,
            "aborted": aborted}
