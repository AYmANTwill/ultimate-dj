"""
Co-occurrence learning from 1001tracklists.

What it does
------------
Reads every cached tracklist in ``data/tracklists/`` (populated by
``engine.tracklists.fetch_tracklist``), fuzzy-matches each scraped
track against the local library, and counts how often pairs of LOCAL
tracks appear close together in real DJ sets. The resulting weights
feed ``library.transition_score`` as the 5th axis: tracks that pro DJs
actually mix get a bonus over tracks that just happen to share a key
or BPM.

Storage model
-------------
``track_pairs(path_a TEXT, path_b TEXT, weight REAL, sets INTEGER,
PRIMARY KEY(path_a, path_b))``

Each row stores the directional weight from A → B. We always insert
both directions so the lookup is symmetric without joining twice.
``weight`` is the sum of position-decay scores across all sets where
A and B both appeared. ``sets`` is the raw count of sets — used for
confidence (a pair seen in 30 different sets is far stronger signal
than a pair seen 30 times in one weird set).

Position decay
--------------
If A is at position i and B at position j in a set, we add::

    1.0 / (1 + abs(j - i))

so adjacent tracks (|j-i|=1) get the full 1.0, tracks 2 apart get
0.5, 4 apart get 0.2, etc. Capped at ``MAX_DISTANCE`` so a 2-hour set
doesn't pull together its bookend tracks.

Public API
----------
    rebuild(conn, on_progress=None) -> dict
        Walk every cached tracklist, rebuild the table from scratch.
        Returns a summary {sets, pairs, matched_tracks, unmatched_tracks}.

    cooccurrence_score(conn, path_a, path_b) -> float
        Lookup the weight for one pair, normalised to 0-100 vs the
        library's max weight. 0 if the pair has never been seen.

    pair_count(conn) -> int
        Total rows in track_pairs. Useful for the Settings status line.
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
from difflib import SequenceMatcher
from pathlib import Path
from typing import Callable, Optional

from app.config import DATA_DIR
from app.logger import log_info, log_warning


# Distance window: pairs further apart than this aren't counted at all.
# In a 25-track set, MAX_DISTANCE=5 means we look at the 5 tracks ahead
# and the 5 tracks behind. Beyond that the signal is mostly noise.
MAX_DISTANCE = 5

# Cap a single track's max weight bonus to avoid one weird track that
# happens to be at position 1 in 200 sets dominating the score.
_NORMALISE_QUANTILE = 0.99

_CACHE_DIR = DATA_DIR / "tracklists"


# ── Normalisation + fuzzy match (shared with tracklists.py) ───────

def _normalise(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"\(.*?\)|\[.*?\]", " ", s)
    s = re.sub(r"feat\.?|ft\.?|vs\.?", " ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(s.split())


def _ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def _build_library_index(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    """List of (path, normalised_title) for every local track. Built
    once per rebuild and reused across every set's fuzzy match."""
    rows = conn.execute(
        "SELECT path, title FROM tracks "
        "WHERE path NOT IN (SELECT path FROM trash)").fetchall()
    return [(r["path"], _normalise(r["title"] or ""))
            for r in rows]


def _best_match(needle: str, lib_index: list[tuple[str, str]],
                 threshold: float = 0.72) -> Optional[str]:
    """Return the library path that fuzzy-matches `needle`, or None
    if the best score is below threshold."""
    best_score = 0.0
    best_path = None
    for path, norm_title in lib_index:
        score = _ratio(needle, norm_title)
        if score > best_score:
            best_score = score
            best_path = path
    return best_path if best_score >= threshold else None


# ── Schema migration ─────────────────────────────────────────────

def ensure_schema(conn: sqlite3.Connection) -> None:
    """Idempotent — safe to call on every rebuild. The table is
    cheap to rebuild from scratch, so we don't bother with online
    migrations of weight columns."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS track_pairs (
            path_a TEXT NOT NULL,
            path_b TEXT NOT NULL,
            weight REAL NOT NULL DEFAULT 0,
            sets   INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (path_a, path_b)
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_track_pairs_a ON track_pairs(path_a)")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_track_pairs_weight "
        "ON track_pairs(weight DESC)")
    conn.commit()


# ── Rebuild ──────────────────────────────────────────────────────

def _iter_cached_sets():
    """Yield (slug, parsed_dict) for every cached tracklist on disk."""
    if not _CACHE_DIR.exists():
        return
    for f in sorted(_CACHE_DIR.glob("*.json")):
        try:
            yield f.stem, json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:
            log_warning(f"cooccurrence: failed to read {f.name}: {e}")
            continue


def rebuild(conn: sqlite3.Connection, *,
            on_progress: Callable[[int, int, str], None] | None = None,
            ) -> dict:
    """Wipe + rebuild the track_pairs table from every cached set.

    Returns a summary dict so the UI can show what happened::

        {
          "sets":              42,    # total sets processed
          "pairs":           1530,    # distinct directional pairs stored
          "matched_tracks":   312,    # local tracks that appear in any set
          "unmatched_tracks": 873,    # scraped tracks with no lib match
        }
    """
    ensure_schema(conn)
    conn.execute("DELETE FROM track_pairs")
    conn.commit()

    lib = _build_library_index(conn)
    if not lib:
        log_info("cooccurrence.rebuild: empty library, skipping")
        return {"sets": 0, "pairs": 0, "matched_tracks": 0,
                "unmatched_tracks": 0}

    sets_files = list(_CACHE_DIR.glob("*.json")) if _CACHE_DIR.exists() else []
    n_sets = len(sets_files)
    if n_sets == 0:
        log_info("cooccurrence.rebuild: no cached tracklists yet")
        return {"sets": 0, "pairs": 0, "matched_tracks": 0,
                "unmatched_tracks": 0}

    # Aggregate weights in-memory then flush to DB — single transaction
    # so a rebuild of 5k sets stays under 30s even on slow disks.
    weights: dict[tuple[str, str], float] = {}
    counts: dict[tuple[str, str], int] = {}
    matched_paths: set[str] = set()
    n_unmatched = 0
    n_matched = 0

    for idx, (slug, parsed) in enumerate(_iter_cached_sets(), 1):
        if on_progress:
            try:
                on_progress(idx, n_sets, slug)
            except Exception:
                pass

        tracks = parsed.get("tracks") or []
        # Resolve every scraped track to a local path (or None)
        resolved: list[str | None] = []
        for t in tracks:
            needle = _normalise(
                f"{t.get('artist', '')} {t.get('title', '')}")
            if not needle:
                resolved.append(None)
                n_unmatched += 1
                continue
            match = _best_match(needle, lib)
            if match is None:
                resolved.append(None)
                n_unmatched += 1
            else:
                resolved.append(match)
                matched_paths.add(match)
                n_matched += 1

        # Build pairs with position-decay weighting
        for i, a in enumerate(resolved):
            if a is None:
                continue
            for j in range(i + 1, min(i + 1 + MAX_DISTANCE, len(resolved))):
                b = resolved[j]
                if b is None or a == b:
                    continue
                decay = 1.0 / (1 + (j - i))
                # Symmetric: A↔B is the same edge
                k1 = (a, b)
                k2 = (b, a)
                weights[k1] = weights.get(k1, 0.0) + decay
                weights[k2] = weights.get(k2, 0.0) + decay
                counts[k1] = counts.get(k1, 0) + 1
                counts[k2] = counts.get(k2, 0) + 1

    # Flush
    rows = [(a, b, w, counts[(a, b)]) for (a, b), w in weights.items()]
    if rows:
        conn.executemany(
            "INSERT INTO track_pairs (path_a, path_b, weight, sets) "
            "VALUES (?, ?, ?, ?)", rows)
    conn.commit()

    summary = {
        "sets":             n_sets,
        "pairs":            len(rows),
        "matched_tracks":   len(matched_paths),
        "unmatched_tracks": n_unmatched,
    }
    log_info(f"cooccurrence.rebuild: {summary}")
    return summary


# ── Score lookup ─────────────────────────────────────────────────

# Cache the normalisation reference (max weight in the table) so
# cooccurrence_score is O(1) after first call. Bust the cache when
# rebuild() finishes.
_max_weight_cache: dict[int, float] = {}


def _max_weight(conn: sqlite3.Connection) -> float:
    """The quantile-trimmed max weight, used to normalise lookups to
    [0, 100]. Cached per-connection."""
    cid = id(conn)
    if cid in _max_weight_cache:
        return _max_weight_cache[cid]
    try:
        # Use ~99th percentile so outliers don't crush all other scores
        row = conn.execute(
            "SELECT weight FROM track_pairs "
            "ORDER BY weight DESC "
            "LIMIT 1 OFFSET CAST((SELECT COUNT(*) FROM track_pairs) * 0.01 AS INT)"
        ).fetchone()
        m = float(row[0]) if row else 0.0
    except Exception:
        m = 0.0
    if m <= 0:
        # Fallback to raw max
        try:
            row = conn.execute(
                "SELECT MAX(weight) FROM track_pairs").fetchone()
            m = float(row[0] or 0)
        except Exception:
            m = 0.0
    _max_weight_cache[cid] = m
    return m


def invalidate_cache() -> None:
    """Call after rebuild() — the cached max_weight may have shifted."""
    _max_weight_cache.clear()


def cooccurrence_score(conn: sqlite3.Connection,
                        path_a: str, path_b: str) -> float:
    """0-100 — how often these two tracks co-occur in real DJ sets.
    Returns 0.0 if the pair has never been seen or the table is empty."""
    if not path_a or not path_b or path_a == path_b:
        return 0.0
    try:
        row = conn.execute(
            "SELECT weight FROM track_pairs "
            "WHERE path_a = ? AND path_b = ?",
            (path_a, path_b)).fetchone()
    except sqlite3.OperationalError:
        return 0.0          # table doesn't exist yet
    if not row:
        return 0.0
    raw = float(row[0])
    mw = _max_weight(conn)
    if mw <= 0:
        return 0.0
    return float(min(100.0, (raw / mw) * 100.0))


def pair_count(conn: sqlite3.Connection) -> int:
    try:
        return int(conn.execute(
            "SELECT COUNT(*) FROM track_pairs").fetchone()[0])
    except sqlite3.OperationalError:
        return 0


def neighbours(conn: sqlite3.Connection, path: str,
                limit: int = 20) -> list[tuple[str, float]]:
    """The N strongest co-play partners for a given track.
    Returns [(path, score), …] sorted by descending score."""
    try:
        rows = conn.execute(
            "SELECT path_b, weight FROM track_pairs "
            "WHERE path_a = ? ORDER BY weight DESC LIMIT ?",
            (path, limit)).fetchall()
    except sqlite3.OperationalError:
        return []
    mw = _max_weight(conn) or 1.0
    return [(r[0], min(100.0, r[1] / mw * 100.0)) for r in rows]
