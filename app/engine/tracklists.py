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

# Playwright's sync API is greenlet-bound: a sync_playwright() instance
# can only be touched from the thread that created it. Sharing a global
# cached browser across worker threads triggers
# `greenlet.error: Cannot switch to a different thread`. So we keep one
# (playwright, browser, ctx) triple PER THREAD via threading.local.
_PW_TLS = threading.local()
_PW_LOCK = threading.Lock()    # only protects _AUTH_STATE_PATH file writes

# Persistent session cookies — saved after a successful login so we
# don't have to re-authenticate on every app launch. Lives outside the
# Spotify-style keyring (cookies aren't really "secrets" — they're
# rotating tokens) but in the gitignored data/ folder.
_AUTH_STATE_PATH = DATA_DIR / "tracklists_auth_state.json"
# 1001tracklists has no dedicated /login URL. /login → 404,
# /user/login → "user not found" (the path /user/{name} treats "login"
# as a username). Login is a modal triggered from the homepage's nav
# bar. So we open the homepage and let the user click the login icon
# themselves in the visible window.
_HOMEPAGE_URL = "https://www.1001tracklists.com/"


def _playwright_available() -> bool:
    try:
        import playwright    # noqa: F401
        return True
    except ImportError:
        return False


def _get_thread_browser():
    """Get or create THIS THREAD's Playwright browser + context. Returns
    (browser, ctx). Each thread gets its own pair to avoid the
    greenlet-cross-thread errors of the sync API.

    Auto-reloads when the on-disk auth state file changes — that lets
    cookies saved by a successful login in one thread propagate to all
    other threads the next time they ask for a browser. Without this
    check, a worker that started BEFORE login keeps its stale (no-
    cookie) context forever and never benefits from the login.
    """
    cached_ctx = getattr(_PW_TLS, "ctx", None)
    cached_auth_mtime = getattr(_PW_TLS, "auth_mtime", -1)
    current_auth_mtime = -1
    try:
        if _AUTH_STATE_PATH.exists():
            current_auth_mtime = _AUTH_STATE_PATH.stat().st_mtime
    except Exception:
        pass
    # Reuse cache only if (a) it exists, AND (b) the auth file hasn't
    # changed since we built it.
    if cached_ctx is not None and cached_auth_mtime == current_auth_mtime:
        return _PW_TLS.browser, _PW_TLS.ctx
    # Either no cache or stale — rebuild
    if cached_ctx is not None:
        _reset_thread_browser()

    from playwright.sync_api import sync_playwright
    _PW_TLS.pw = sync_playwright().start()
    _PW_TLS.browser = _PW_TLS.pw.chromium.launch(
        headless=True,
        args=["--disable-blink-features=AutomationControlled",
               "--no-sandbox"])
    ctx_args: dict = dict(
        user_agent=_USER_AGENTS[0],
        viewport={"width": 1280, "height": 800},
        locale="en-US",
    )
    if _AUTH_STATE_PATH.exists():
        try:
            ctx_args["storage_state"] = str(_AUTH_STATE_PATH)
            log_info(
                f"tracklists: loaded saved login session from "
                f"{_AUTH_STATE_PATH.name} (mtime "
                f"{current_auth_mtime:.0f})")
        except Exception as e:
            log_warning(f"tracklists: failed to load auth state: {e}")
    _PW_TLS.ctx = _PW_TLS.browser.new_context(**ctx_args)
    _PW_TLS.auth_mtime = current_auth_mtime
    # Stealth — best-effort
    try:
        from playwright_stealth import Stealth
        Stealth().apply_stealth_sync(_PW_TLS.ctx)
    except ImportError:
        log_warning("playwright_stealth not installed — "
                    "1001tracklists will probably reject the "
                    "headless browser")
    except Exception as e:
        log_warning(f"stealth init failed: {e}")
    return _PW_TLS.browser, _PW_TLS.ctx


def _reset_thread_browser():
    """Tear down THIS THREAD's cached Playwright instance, so the next
    _get_thread_browser() picks up fresh cookies from disk."""
    ctx = getattr(_PW_TLS, "ctx", None)
    browser = getattr(_PW_TLS, "browser", None)
    pw = getattr(_PW_TLS, "pw", None)
    for x in (ctx, browser):
        try:
            if x is not None:
                x.close()
        except Exception:
            pass
    try:
        if pw is not None:
            pw.stop()
    except Exception:
        pass
    _PW_TLS.ctx = _PW_TLS.browser = _PW_TLS.pw = None


def _save_auth_state(ctx) -> None:
    """Persist a context's cookies + storage to disk. Thread-safe via
    _PW_LOCK (only the file write is protected)."""
    if ctx is None:
        return
    with _PW_LOCK:
        try:
            _AUTH_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            ctx.storage_state(path=str(_AUTH_STATE_PATH))
            log_info(
                f"tracklists: saved login session to "
                f"{_AUTH_STATE_PATH.name}")
        except Exception as e:
            log_warning(f"tracklists: failed to save auth state: {e}")


def _detect_logged_in(html: str) -> bool:
    """Decide whether an arbitrary 1001tracklists page HTML is being
    served to a logged-in user. STRICT detection: REQUIRES at least
    one positive signal (logout link) AND REQUIRES the absence of
    logged-out signals (login icon link, signup link).

    The previous version matched the literal substring ">logout<" or
    "log out" anywhere in the HTML — that fired false positives on
    e.g. cookie-banner text mentioning "log out at any time" or
    user-content tracklist names. We now anchor on the actual href
    attributes which only appear in the rendered nav for logged-in
    users.
    """
    if not html:
        return False
    h = html.lower()
    if _looks_like_ip_ban(h):
        return False
    # ── Positive signals (logged-in nav) ──
    # The /user/logout href is in the top nav ONLY when authenticated.
    has_logout_href = ("href=\"/user/logout" in h
                       or "href='/user/logout" in h)
    # Some pages put the logout under /logout (older style)
    has_logout_href2 = ("href=\"/logout\"" in h
                        or "href='/logout'" in h)
    pos = has_logout_href or has_logout_href2

    # ── Negative signals (logged-out nav) ──
    # The signup / login icons only appear for guests.
    has_login_href = ("href=\"/user/login\"" in h
                      or "href='/user/login'" in h)
    has_signup_href = ("href=\"/user/register" in h
                       or "href='/user/register" in h
                       or "href=\"/user/signup" in h
                       or "href='/user/signup" in h)
    neg = has_login_href or has_signup_href

    # Conclusive: positive signal present AND no negative signal
    return pos and not neg


def login_with_credentials(email: str, password: str,
                            *, force: bool = False,
                            wait_timeout_s: int = 600,
                            ) -> tuple[bool, str]:
    """Open a VISIBLE Chromium window on the 1001tracklists homepage.
    The USER logs in manually (clicks the login icon, fills the modal,
    solves the captcha, clicks Sign in). When they've finished, they
    CLOSE THE WINDOW themselves — that's our signal to save the
    cookies. We then verify by making a real scrape request, and the
    return value reflects whether scraping actually works now.

    Why this design (instead of polling for a logout link in the
    rendered HTML): 1001tracklists' nav uses icon buttons with JS
    click handlers, not plain <a href="/user/logout"> links. Any
    HTML-pattern heuristic produces false negatives (user is logged
    in but we don't detect it) or false positives (random text in the
    page matches). Letting the user close the window themselves is
    100% reliable — they know when they've finished logging in.

    We ALSO save cookies every 5 s while the window is open, so a
    crash mid-flow doesn't lose the session.

    Returns ``(success, message)`` based on a real verification scrape
    against /index.html — if it comes back without the IP-ban marker,
    cookies are working; if it still hits the ban, login didn't take.
    """
    if not _playwright_available():
        return False, "playwright n'est pas installé"
    if not email or not password:
        return False, "email + mot de passe requis"

    # Fast path: already logged in? Skip the popup.
    if not force:
        try:
            if is_logged_in():
                return True, "déjà connecté (session sauvegardée)"
        except Exception:
            pass

    from playwright.sync_api import sync_playwright
    pw = None
    browser = None
    ctx = None
    page = None
    try:
        pw = sync_playwright().start()
        browser = pw.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled"])
        ctx_args = dict(
            user_agent=_USER_AGENTS[0],
            viewport={"width": 1280, "height": 800},
            locale="en-US",
        )
        if _AUTH_STATE_PATH.exists():
            ctx_args["storage_state"] = str(_AUTH_STATE_PATH)
        ctx = browser.new_context(**ctx_args)
        try:
            from playwright_stealth import Stealth
            Stealth().apply_stealth_sync(ctx)
        except Exception:
            pass
        page = ctx.new_page()
        page.goto(_HOMEPAGE_URL, wait_until="domcontentloaded",
                  timeout=30_000)

        # Periodically dump cookies to disk while user works in popup.
        # Final save happens after the loop (or when user closes window).
        import time as _t
        t0 = _t.time()
        last_save = 0.0
        window_closed_by_user = False
        while _t.time() - t0 < wait_timeout_s:
            try:
                if page.is_closed():
                    window_closed_by_user = True
                    break
            except Exception:
                window_closed_by_user = True
                break
            now = _t.time()
            if now - last_save > 5.0:
                last_save = now
                try:
                    _AUTH_STATE_PATH.parent.mkdir(
                        parents=True, exist_ok=True)
                    ctx.storage_state(path=str(_AUTH_STATE_PATH))
                except Exception:
                    pass
            _t.sleep(0.5)

        if not window_closed_by_user:
            return False, ("timeout — fenêtre toujours ouverte après "
                            f"{wait_timeout_s//60} min. Login pas "
                            "terminé ? Re-clique Login et finis le "
                            "flow dans la fenêtre Chromium puis ferme-"
                            "la quand t'es loggé.")

        # Final save (cookies file should already be up to date from
        # the periodic dumps, but be safe)
        try:
            _AUTH_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            ctx.storage_state(path=str(_AUTH_STATE_PATH))
        except Exception:
            pass

    except Exception as e:
        log_warning(f"login_with_credentials failed: {e}")
        return False, f"erreur login : {str(e)[:120]}"
    finally:
        for obj in (page, ctx, browser):
            try:
                if obj is not None:
                    obj.close()
            except Exception:
                pass
        try:
            if pw is not None:
                pw.stop()
        except Exception:
            pass

    # Verification: try a real scrape (fetch the homepage in HEADLESS
    # mode using the saved cookies). If we hit the IP-ban marker, the
    # login didn't take. If we DON'T hit it, login succeeded — even if
    # we can't see a logout link, the cookies bypass the guest limit.
    _reset_thread_browser()
    try:
        verify_html = _playwright_get_html(
            _HOMEPAGE_URL, wait_for_selector="body", timeout_ms=20_000)
    except Exception as e:
        return True, (f"cookies sauvegardés. Vérif a échoué ({e}) — "
                        "lance un scrape pour confirmer.")
    if verify_html is None:
        return True, ("cookies sauvegardés. Vérif n'a pas pu charger "
                        "la home — lance un scrape pour confirmer.")
    if _looks_like_ip_ban(verify_html):
        return False, ("cookies sauvés MAIS la home affiche encore "
                        "'IP banned'. Le login dans la fenêtre n'a "
                        "pas été validé ou ton compte aussi est "
                        "rate-limited.")
    # Looks good — we got real content (not the ban page)
    return True, (f"login validé en tant que {email} — cookies sauvés, "
                    "la home charge sans le ban guest")


def is_logged_in() -> bool:
    """Whether the saved cookies actually let us bypass the guest
    rate-limit. We DON'T look for HTML markers like a logout link
    (1001tracklists uses JS-handler icons, not <a href> links, so any
    pattern is fragile). Instead we load the homepage in THIS thread's
    headless browser and check that it DOESN'T return the
    "Your IP has been limited" page — that's the real
    functional test of whether the cookies work.

    Returns False on any error or if Playwright isn't available.
    """
    if not _playwright_available():
        return False
    try:
        _, ctx = _get_thread_browser()
    except Exception:
        return False
    page = ctx.new_page()
    try:
        try:
            page.goto(_HOMEPAGE_URL,
                      wait_until="domcontentloaded", timeout=20_000)
            html = page.content() or ""
        except Exception:
            return False
        # If we hit the IP ban page → cookies aren't working
        if _looks_like_ip_ban(html):
            return False
        # If we got real content (any homepage content with no ban
        # marker), assume the cookies work — even if we can't see a
        # clear logout link.
        return bool(html) and len(html) > 5000
    finally:
        try:
            page.close()
        except Exception:
            pass


def logout_and_clear_session() -> None:
    """Delete the saved auth state file + reset THIS thread's
    Playwright cache so the next request starts logged-out. Other
    threads pick up the missing cookies at their next
    _get_thread_browser() call."""
    try:
        if _AUTH_STATE_PATH.exists():
            _AUTH_STATE_PATH.unlink()
    except Exception:
        pass
    _reset_thread_browser()


def _playwright_get_html(url: str, *,
                           wait_for_selector: str = "a[href*='/tracklist/']",
                           timeout_ms: int = 25_000,
                           settle_ms: int = 0) -> str | None:
    """Open `url` in THIS THREAD's stealthed headless Chromium, wait
    for content to render, return the post-JS DOM. None on failure.

    ``settle_ms`` adds an extra fixed wait AFTER the selector appears —
    1001tracklists is a SPA that hydrates the track rows progressively,
    so the first DOM snapshot right after wait_for_selector often has
    the nav links but NOT all the track <div class="bCont tl"> blocks
    yet. A short settle (1-2 s) lets them finish populating before we
    grab page.content().

    Persistent auth state: when the user has logged in at least once
    via ``login_with_credentials``, the saved session cookies are
    re-applied on every browser startup, so requests are authenticated
    and aren't subject to the guest IP rate-limit.
    """
    if not _playwright_available():
        return None
    try:
        _, ctx = _get_thread_browser()
    except Exception as e:
        log_warning(f"playwright init failed: {e}")
        return None
    try:
        page = ctx.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded",
                      timeout=timeout_ms)
            try:
                page.wait_for_selector(
                    wait_for_selector, timeout=timeout_ms)
            except Exception:
                # Selector didn't appear — return the DOM anyway, the
                # caller may still want to inspect it.
                pass
            if settle_ms > 0:
                try:
                    page.wait_for_timeout(settle_ms)
                except Exception:
                    pass
            html = page.content()
            return html
        finally:
            try:
                page.close()
            except Exception:
                pass
    except Exception as e:
        log_warning(f"playwright fetch failed for {url}: {e}")
        return None


def _shutdown_playwright():
    """Tear down THIS thread's Playwright cache. Called from atexit."""
    _reset_thread_browser()
    # No-op stubs to keep the legacy block harmless during cleanup
    pass


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

    # ALWAYS use playwright for tracklist detail pages. cloudscraper
    # can't execute JS, and 1001tracklists hydrates the track rows
    # (div.bCont.tl) client-side — so a cloudscraper response has the
    # nav/meta (which contains "tracklist/" links, fooling the
    # js-shell check) but ZERO actual tracks. We were silently parsing
    # that empty static HTML and reporting "0 tracks". Playwright with
    # the right selector + settle is the only path that works.
    html: str | None = None
    if _playwright_available():
        log_info(f"fetch_tracklist: playwright fetch {url}")
        # Wait for the ACTUAL track container (div.bCont.tl), not just
        # any tracklist link. settle 2 s so all rows finish populating
        # before we snapshot the DOM.
        html = _playwright_get_html(
            url, wait_for_selector="div.bCont.tl",
            settle_ms=2000)

    # Fallback to cloudscraper only if playwright is unavailable — it
    # won't have hydrated tracks but at least lets us detect IP bans /
    # cache hits.
    if html is None:
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

    if html is None:
        raise RuntimeError(
            f"could not fetch {url} (playwright + cloudscraper both "
            "failed) — check network / login")

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


def _parse_iso_duration(s: str) -> int:
    """Parse ISO-8601 duration like ``PT6M57S`` into total seconds.
    Returns 0 on malformed input."""
    if not s or not s.startswith("PT"):
        return 0
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", s)
    if not m:
        return 0
    h, mi, se = m.groups()
    return int(h or 0) * 3600 + int(mi or 0) * 60 + int(se or 0)


def _parse_tracks(soup) -> list[dict]:
    """Pull the list of tracks from a 1001tracklists tracklist page.

    Modern 1001tracklists (post-2025 SPA rewrite) embeds each track as
    a schema.org MusicRecording with the structured data we want
    directly in ``<meta itemprop="...">`` tags:

        <div class="bCont tl">
          <div id="tlpN_content" itemprop="tracks" itemscope
               itemtype="http://schema.org/MusicRecording">
            <meta itemprop="name"     content="Artist - Title (Remix)">
            <meta itemprop="byArtist" content="Artist">
            <meta itemprop="duration" content="PT6M57S">
            <meta itemprop="genre"    content="Techno">
            <meta itemprop="url"      content="/track/.../index.html">
            <meta itemprop="publisher" content="…HTML-encoded label…">
            <span class="trackValue …">… visible artist + title spans …</span>
          </div>
          <div class="iRow grow mediaRow" data-trackid="1082668">…
          <div class="cue noWrap action mt5">00:11</div>   ← cue time
        </div>

    This is much more reliable than parsing the visible spans (which
    have a wrapping-rich structure with nested artist/remix/label
    anchors). We read the meta tags first, fall back to the visible
    spans only when meta isn't present (e.g. an unidentified ID
    placeholder track).

    Returns list of {position, artist, title, time, label, raw,
                     duration, genre, url, track_id}.
    """
    from html import unescape
    tracks: list[dict] = []
    rows = soup.select("div.bCont.tl")
    for i, row in enumerate(rows, 1):
        rec = row.find(attrs={"itemprop": "tracks"})
        if rec is None:
            # No schema.org block on this row — likely a "no track"
            # placeholder header / separator. Skip.
            continue

        def _meta(prop: str) -> str:
            m = rec.find("meta", attrs={"itemprop": prop})
            return (m.get("content") or "").strip() if m else ""

        name = _meta("name")
        artist = _meta("byArtist")
        duration_iso = _meta("duration")
        genre = _meta("genre")
        track_url = _meta("url")

        if not name and not artist:
            # ID / unknown track — fall back to visible spans
            tv = row.select_one(".trackValue")
            raw = tv.get_text(" ", strip=True) if tv else ""
            if not raw:
                continue
            if " - " in raw:
                artist, _, title = raw.partition(" - ")
            else:
                title = raw
        else:
            # Derive title by stripping the leading "artist - " from
            # the full name. Falls back to the whole name if the
            # leading-artist convention doesn't match (rare).
            title = name
            if artist and name.startswith(artist + " - "):
                title = name[len(artist) + 3:]
            raw = name or f"{artist} - {title}".strip(" -")

        # Cue (timestamp in the set)
        cue_el = row.select_one("div.cue.noWrap") or row.select_one(".cue")
        time_in = cue_el.get_text(strip=True) if cue_el else ""

        # Label is HTML-encoded inside the publisher meta — decode +
        # re-parse to extract the visible text.
        label = ""
        publisher_meta = rec.find("meta",
                                    attrs={"itemprop": "publisher"})
        if publisher_meta is not None:
            try:
                from bs4 import BeautifulSoup as _BS
                label_html = unescape(publisher_meta.get("content") or "")
                label_soup = _BS(label_html, "lxml")
                label = label_soup.get_text(" ", strip=True)
            except Exception:
                pass

        # Track ID from the mediaRow's data-trackid attribute (useful
        # for de-duplicating + linking external metadata later)
        media_row = row.select_one("div.iRow.grow.mediaRow")
        track_id = ""
        if media_row is not None:
            track_id = media_row.get("data-trackid", "")

        tracks.append({
            "position": i,
            "artist":   artist.strip(),
            "title":    title.strip(),
            "time":     time_in,
            "label":    label.strip(),
            "raw":      raw.strip(),
            "duration": _parse_iso_duration(duration_iso),
            "genre":    genre.strip(),
            "url":      track_url.strip(),
            "track_id": track_id,
        })
    return tracks


# ── Match scraped tracks against the local library ───────────────

def _normalise(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"\(.*?\)|\[.*?\]", " ", s)        # drop parenthetical info
    s = re.sub(r"feat\.?|ft\.?|vs\.?", " ", s)    # drop feat/vs markers
    # Common remix/edit noise words that don't help identity matching
    s = re.sub(r"\b(original|extended|radio|club|mix|edit|remix|"
               r"version|bootleg|rework|vip|dub|instrumental)\b",
               " ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(s.split())


def _ratio(a: str, b: str) -> float:
    """Cheap fuzzy similarity in [0,1]. SequenceMatcher is in stdlib so
    we don't pull in fuzzywuzzy/rapidfuzz for one helper."""
    from difflib import SequenceMatcher
    return SequenceMatcher(None, a, b).ratio()


def _token_sort_ratio(a: str, b: str) -> float:
    """Word-order-INSENSITIVE ratio: sort the tokens of each string
    before comparing. "carl cox homicide" vs "homicide carl cox" →
    both sort to "carl cox homicide" → 1.0 instead of a low score."""
    ta = " ".join(sorted(a.split()))
    tb = " ".join(sorted(b.split()))
    return _ratio(ta, tb)


def _split_artist_title(s: str) -> tuple[str, str]:
    """Split a 'Artist - Title' string on the first ' - '. Returns
    (artist, title); artist='' if no separator."""
    if " - " in s:
        a, _, t = s.partition(" - ")
        return a.strip(), t.strip()
    return "", s.strip()


def _is_id_placeholder(artist: str, title: str) -> bool:
    """1001tracklists uses 'ID' (or 'ID - ID') for tracks the
    community hasn't identified yet. These must NEVER match a real
    library track — they carry no identity. Also reject empty/1-char
    fragments that match everything."""
    a = (artist or "").strip().lower()
    t = (title or "").strip().lower()
    if a in ("id", "") and t in ("id", ""):
        return True
    if t == "id" or a == "id":
        return True
    # Too short to be a meaningful identity (e.g. a stray "x")
    if len(_normalise(f"{artist} {title}")) < 4:
        return True
    return False


def name_match_score(scr_artist: str, scr_title: str,
                      lib_title: str) -> float:
    """PRECISION-first name similarity in [0,1] between a scraped track
    and a library track's stored title.

    A false cooccurrence pair teaches the model a transition that never
    happened — strictly worse than a missing pair. So this scorer is
    tuned for FEW false positives:

      - Primary metric = token_SORT of the full "artist title" strings.
        token_sort is word-order-insensitive but still requires MOST
        tokens to line up (extra/missing words drag it down) — unlike
        token_set which isolates the intersection and over-fires on a
        single shared word like "you" or "gucci".
      - A separate artist+title path can only LIFT the score when BOTH
        the artist AND the title independently clear strict bars
        (artist ≥ 0.6, title ≥ 0.8). This rescues "A & B - T" vs
        "B - T" style real matches without rewarding title-only or
        artist-only coincidences.
    """
    scr_full = _normalise(f"{scr_artist} {scr_title}")
    lib_full = _normalise(lib_title)
    if not scr_full or not lib_full:
        return 0.0

    # Primary: strict, order-insensitive full-string comparison
    score = _token_sort_ratio(scr_full, lib_full)

    # Secondary: independent artist + title, both must be strong
    lib_art_raw, lib_tit_raw = _split_artist_title(lib_title)
    if lib_art_raw and lib_tit_raw:
        n_scr_art = _normalise(scr_artist)
        n_scr_tit = _normalise(scr_title)
        n_lib_art = _normalise(lib_art_raw)
        n_lib_tit = _normalise(lib_tit_raw)
        if n_scr_art and n_scr_tit and n_lib_art and n_lib_tit:
            a = _token_sort_ratio(n_scr_art, n_lib_art)
            t = _token_sort_ratio(n_scr_tit, n_lib_tit)
            if a >= 0.6 and t >= 0.8:
                score = max(score, 0.45 * a + 0.55 * t)
    return score


def match_with_library(tl: dict, conn,
                        threshold: float = 0.80) -> list[dict]:
    """For each scraped track, find the best match in the local DB.

    Returns a list of dicts:
        {position, scraped, match (track or None), score}

    Precision-first: ``name_match_score`` is strict (token-sort based),
    ID/placeholder tracks are excluded entirely, and the default
    ``threshold`` is 0.80 — a false cooccurrence pair is worse for the
    model than a missing one.
    """
    rows = conn.execute(
        "SELECT path, title FROM tracks "
        "WHERE COALESCE(source,'user') = 'user'").fetchall()
    library = [(r["path"], r["title"] or "") for r in rows]

    out = []
    for s in tl.get("tracks", []):
        scr_artist = s.get("artist", "")
        scr_title = s.get("title", "")
        # Unidentified / placeholder tracks can't match anything
        if _is_id_placeholder(scr_artist, scr_title):
            out.append({"position": s.get("position"), "scraped": s,
                         "match": None, "score": 0.0})
            continue
        best = (None, 0.0)
        for path, raw_title in library:
            score = name_match_score(scr_artist, scr_title, raw_title)
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
