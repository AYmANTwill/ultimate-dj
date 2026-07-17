"""
L4 training pipeline — production-grade orchestrator that grows the
training corpus over time.

What this does
--------------
Currently the L4 Siamese model trains on:
- Co-occurrence pairs (from 1001tracklists, via engine.cooccurrence)
- User feedback (from engine.feedback)

Both depend on tracks already being in the user's library — pairs where
EITHER side is unknown to us produce no training signal. To break past
that ceiling, this orchestrator chains together:

  1. Discovery   — pick DJs the user actually listens to (artists with
                   ≥ N tracks in lib) and pull their top scraped sets.
  2. Scrape      — batch_scrape the tracklists (text only, lightweight).
  3. Resolve     — for every scraped track, decide:
                   - already in library              → reuse as-is
                   - matches by fuzzy artist+title   → reuse
                   - missing                         → download via yt-dlp
  4. Download    — fetch missing tracks into data/training_corpus/.
                   Audio quality 192 kbps MP3 (smaller than user lib's
                   320 kbps — embeddings don't need pristine audio).
  5. Analyze     — same pipeline as user tracks: librosa BPM/key/energy,
                   segmentation intro/outro/drops, embeddings.embed().
  6. Purge audio — if embeddings-only mode is on, delete the MP3 file
                   after step 5 (the DB still has the embedding vector
                   + all scalars, so the row remains trainable).
  7. Cooc rebuild— now that more tracks are encoded, track_pairs grows.
  8. Retrain     — engine.transition_model.train() on the bigger pool.

Storage modes
-------------
- 'embeddings_only' (default): MP3 deleted after analyze. ~10 KB per
  track in DB (embedding + scalars). 5 000 training tracks = ~50 MB.
- 'keep_audio': MP3 stays under data/training_corpus/. ~10 MB per
  track. 5 000 = ~50 GB — only enable if you plan to re-encode later
  with a different embedding backend (and you have the disk).

Source separation
-----------------
Every track downloaded by this pipeline is written to the DB with
``source = 'training'`` (or ``'fma'`` when imported from Free Music
Archive). The Library / Mixer / Setlist pages call
``library.all_tracks(include_training=False)`` so the user only ever
sees their real library — corpus tracks are invisible to the UI, but
fully usable by the trainer + scorer.

Public API
----------
    enrich_corpus(target_pairs=2000, *, on_progress=None,
                  stop_event=None, mode='embeddings_only',
                  retrain=True) -> dict
        End-to-end run. Returns a summary dict.

    discover_user_artists(conn, *, min_tracks=2, top_n=20) -> list[str]
        Bucket lib by artist, keep artists with ≥ min_tracks tracks,
        return top_n by count. These become the DJ slugs to scrape.

    resolve_missing(conn, scraped_sets) -> list[dict]
        Diff every scraped track against the DB. Returns the list of
        missing tracks as [{artist, title}] for the downloader.

    download_missing(missing, output_folder, *, on_progress=None) -> list[str]
        Thin wrapper over engine.downloader.download_tracks_by_search.

    analyze_into_db(paths, *, source='training', mode='embeddings_only',
                     on_progress=None) -> int
        Analyze each path, insert into DB with the given source flag,
        optionally delete the audio file after.
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
from collections import Counter
from pathlib import Path
from typing import Callable, Optional

from app.config import DATA_DIR
from app.logger import log_info, log_warning, log_error


_TRAINING_CORPUS_DIR = DATA_DIR / "training_corpus"
_DOWNLOAD_QUALITY = "192"        # lower than user-lib default; embeddings
                                  # don't need 320 kbps quality
_DOWNLOAD_CODEC = "mp3"


# ── Step 1: Discovery — which DJs to scrape ──────────────────────

def discover_user_artists(conn: sqlite3.Connection,
                           *, min_tracks: int = 2,
                           top_n: int = 20) -> list[str]:
    """Pick the artists worth scraping: the ones the user has multiple
    tracks of. These are most likely the DJs/producers whose sets will
    contain *other* tracks the user already owns, giving us a high
    match rate.

    Strategy: the title field in our DB is typically formatted like
    "Artist - Track Name" (yt-dlp output convention). We split on " - "
    to pull the leading artist, count, and return the top N.
    """
    counts: Counter[str] = Counter()
    rows = conn.execute(
        "SELECT title FROM tracks "
        "WHERE COALESCE(source, 'user') = 'user' "
        "AND title IS NOT NULL").fetchall()
    for r in rows:
        title = r[0] or ""
        if " - " not in title:
            continue
        artist = title.split(" - ", 1)[0].strip()
        if not artist or len(artist) > 64:
            continue
        counts[artist] += 1
    # Filter and convert to lowercase slug-style identifiers
    artists = [
        a for a, c in counts.most_common(top_n * 4)
        if c >= min_tracks
    ][:top_n]
    log_info(
        f"discover_user_artists: top {len(artists)} from "
        f"{len(counts)} distinct artists in lib")
    return artists


def _artist_to_slug(name: str) -> str:
    """Convert "Charlotte de Witte" → "charlotte-de-witte". The
    1001tracklists URL convention is hyphens + lowercase + accents
    stripped."""
    import re
    import unicodedata
    n = unicodedata.normalize("NFKD", name)
    n = n.encode("ascii", "ignore").decode("ascii").lower()
    n = re.sub(r"[^a-z0-9]+", "-", n).strip("-")
    return n


# ── Step 3: Resolve — which scraped tracks are missing ─────────────

def resolve_missing(conn: sqlite3.Connection,
                     scraped_sets: list[dict]) -> list[dict]:
    """Walk every track of every scraped set, deduplicate, and return
    the ones whose (artist, title) doesn't fuzzy-match an existing DB
    row.

    Re-uses tracklists.match_with_library so the matching rules are
    identical to the cooccurrence layer — a track we'd count as "in
    library" for cooc must also count as "in DB" here, otherwise we'd
    download duplicates.
    """
    from app.engine.tracklists import _is_id_placeholder, match_with_library

    seen: set[tuple[str, str]] = set()
    missing: list[dict] = []
    for s in scraped_sets:
        # match_with_library returns [{position, scraped, match, score}]
        # — `match` is truthy when the track resolved to a local file.
        for entry in match_with_library(s, conn):
            if entry.get("match"):
                continue
            scraped = entry.get("scraped") or {}
            artist = (scraped.get("artist") or "").strip()
            title = (scraped.get("title") or "").strip()
            if not artist or not title:
                continue
            if _is_id_placeholder(artist, title):
                continue
            key = (artist.lower(), title.lower())
            if key in seen:
                continue
            seen.add(key)
            missing.append({"artist": artist, "title": title})
    log_info(f"resolve_missing: {len(missing)} unique tracks missing "
             f"across {len(scraped_sets)} sets")
    return missing


# ── Step 4: Download — yt-dlp search for missing tracks ────────────

def download_missing(missing: list[dict],
                      *, on_progress: Callable | None = None,
                      stop_event: threading.Event | None = None,
                      ) -> list[str]:
    """Thin wrapper over engine.downloader.download_tracks_by_search.
    Writes to data/training_corpus/. Returns the list of paths actually
    downloaded."""
    if not missing:
        return []
    _TRAINING_CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    from app.engine.downloader import download_tracks_by_search

    def _hook(i, total, display, status, err):
        if on_progress:
            try:
                on_progress(
                    i, total, status,
                    f"{display}{' — ' + err if err else ''}"[:120])
            except Exception:
                pass

    ok, fail, paths, failed_tracks = download_tracks_by_search(
        missing,
        output_folder=str(_TRAINING_CORPUS_DIR),
        quality=_DOWNLOAD_QUALITY,
        codec=_DOWNLOAD_CODEC,
        fallback_codec=None,
        on_track=_hook,
        stop_event=stop_event,
    )
    log_info(f"download_missing: {ok} downloaded, {fail} failed")
    return paths


# ── Step 5+6: Analyze + (optional) purge audio ───────────────────

def _confirms_duplicate(candidate_path: str, lib_title: str) -> bool:
    """Name-level confirmation required before an audio-cosine hit may
    delete a corpus file as 'duplicate'. The candidate stem follows the
    downloader convention 'NN - Artist - Title'."""
    import re
    from app.engine.tracklists import name_match_score
    stem = re.sub(r"^\d+\s*[-_.]\s*", "", Path(candidate_path).stem)
    if " - " in stem:
        artist, _, title = stem.partition(" - ")
    else:
        artist, title = "", stem
    return name_match_score(artist.strip(), title.strip(),
                            lib_title or "") >= 0.75


def analyze_into_db(paths: list[str], *, source: str = "training",
                     mode: str = "embeddings_only",
                     on_progress: Callable | None = None,
                     stop_event: threading.Event | None = None,
                     ) -> int:
    """For every downloaded file: run the full audio analysis (BPM, key,
    energy, segmentation, embedding) and upsert into the tracks table
    with ``source`` set. If ``mode == 'embeddings_only'`` the MP3 is
    deleted right after — the DB row stays trainable because the 256-d
    embedding + scalars are persisted.

    Returns the number of tracks successfully analysed + stored.
    """
    if not paths:
        return 0
    from app.engine.analyzer import analyze_track
    from app.engine import embeddings, library

    conn = library.get_connection()
    done = 0
    audio_dups = 0
    total = len(paths)
    for i, path in enumerate(paths, 1):
        if stop_event is not None and stop_event.is_set():
            break
        try:
            # Embed FIRST so we can audio-dedup before committing a row.
            try:
                vec = embeddings.embed(path)
            except Exception as e:
                log_warning(f"embed failed for {path}: {e}")
                vec = None

            # ── Audio-coupled matching ───────────────────────────
            # If this downloaded track's fingerprint matches an
            # existing USER track ≥ 0.92 cosine, the name matcher just
            # missed it — it's a track the user already owns. Don't
            # store a redundant corpus copy; drop the file and move on.
            # CRITICAL: audio cosine alone over-fires badly on the lite
            # backend (measured mean 0.97 between RANDOM tracks — a
            # 0.9999 hit paired Janet Jackson with Skrillex and wiped a
            # whole 1106-file batch on 2026-07-06). A dup verdict must
            # ALSO be confirmed by the names.
            if vec is not None:
                dup_path, dup_score = library.find_audio_duplicate(
                    conn, vec, threshold=0.92, source="user")
                if dup_path is not None:
                    row = conn.execute(
                        "SELECT title FROM tracks WHERE path = ?",
                        (dup_path,)).fetchone()
                    if not _confirms_duplicate(
                            path, row["title"] if row else ""):
                        dup_path = None
                if dup_path is not None:
                    audio_dups += 1
                    log_info(
                        f"audio-dedup: {Path(path).name} ≈ "
                        f"{Path(dup_path).name} (cos {dup_score}) — "
                        f"skipping redundant corpus copy")
                    try:
                        os.remove(path)
                    except Exception:
                        pass
                    if on_progress:
                        try:
                            on_progress(i, total, "dup",
                                         f"{Path(path).name[:40]} "
                                         f"≈ lib")
                        except Exception:
                            pass
                    continue

            info = analyze_track(path)
            info["source"] = source
            library.upsert_track(conn, info)
            if vec is not None:
                library.set_embedding(
                    conn, path, vec, backend=embeddings.best_backend())
            if mode == "embeddings_only":
                try:
                    os.remove(path)
                    conn.execute(
                        "UPDATE tracks SET audio_purged = 1 "
                        "WHERE path = ?", (path,))
                    conn.commit()
                except Exception as e:
                    log_warning(f"purge audio failed for {path}: {e}")
            done += 1
            if on_progress:
                try:
                    on_progress(i, total, "ok",
                                 Path(path).name[:60])
                except Exception:
                    pass
        except Exception as e:
            log_warning(f"analyze_into_db failed for {path}: {e}")
            if on_progress:
                try:
                    on_progress(i, total, "fail",
                                 f"{Path(path).name[:40]} — {str(e)[:40]}")
                except Exception:
                    pass
    log_info(f"analyze_into_db: {done}/{total} stored as source={source} "
             f"({audio_dups} audio-dups skipped)")
    return done


# ── End-to-end ───────────────────────────────────────────────────

_AUTO_ENRICH_LOCK = threading.Lock()
_auto_enrich_in_progress = False


def maybe_auto_enrich() -> bool:
    """Continuous learning: fire enrich_corpus() in a daemon thread when
    the user library grew by ai_auto_enrich_min_new tracks (default 25)
    since the last run. Opt-in via ai_auto_enrich, off by default —
    background scraping + downloads are not for everyone.

    The first call after enabling only sets the baseline: switching the
    toggle on must not instantly launch a scrape of a library that was
    already there. Mirrors transition_model.maybe_auto_retrain.

    Returns True if a run was actually scheduled.
    """
    global _auto_enrich_in_progress
    if _auto_enrich_in_progress:
        return False
    try:
        from app.config import load_config, save_config
        cfg = load_config()
        if not cfg.get("ai_auto_enrich", False):
            return False
        from app.engine import library
        conn = library.get_connection()
        n_user = conn.execute(
            "SELECT COUNT(*) FROM tracks "
            "WHERE COALESCE(source, 'user') = 'user'").fetchone()[0]
        last = int(cfg.get("ai_auto_enrich_last_count", 0) or 0)
        min_new = int(cfg.get("ai_auto_enrich_min_new", 25) or 25)
        if last == 0 or n_user - last < min_new:
            if last == 0:
                cfg["ai_auto_enrich_last_count"] = n_user
                save_config(cfg)
            return False
        cfg["ai_auto_enrich_last_count"] = n_user
        save_config(cfg)
    except Exception as e:
        log_warning(f"maybe_auto_enrich: pre-checks failed: {e}")
        return False

    with _AUTO_ENRICH_LOCK:
        if _auto_enrich_in_progress:
            return False
        _auto_enrich_in_progress = True

    def _work():
        global _auto_enrich_in_progress
        try:
            from app.engine import tasks as _tasks
            task = _tasks.register(
                "Corpus auto-enrich",
                message="bibliothèque enrichie — scrape des setlists…")

            def _ep(phase, i, total, msg):
                _tasks.update(task.id,
                              progress=(i / total) if total else 0.0,
                              message=f"{phase}: {msg}")

            summary = enrich_corpus(on_progress=_ep)
            _tasks.complete(
                task.id, success=not summary.get("aborted"),
                message=f"{summary.get('total_pairs_after', 0)} paires "
                        f"après enrichissement")
        except Exception as e:
            log_warning(f"maybe_auto_enrich: run failed: {e}")
        finally:
            _auto_enrich_in_progress = False

    threading.Thread(target=_work, daemon=True,
                     name="auto-enrich").start()
    return True


def enrich_corpus(target_pairs: int = 2000,
                   *, on_progress: Callable | None = None,
                   stop_event: threading.Event | None = None,
                   mode: str = "embeddings_only",
                   max_dj_artists: int = 10,
                   sets_per_dj: int = 15,
                   retrain: bool = True,
                   ) -> dict:
    """Run the full pipeline. ``on_progress(phase, sub_i, sub_total, msg)``.

    Returns a summary dict:
        {phases: {...counts...}, total_pairs_after: int,
         model_retrained: bool, aborted: bool}
    """
    from app.engine import library, tracklists, cooccurrence
    summary: dict = {"phases": {}, "aborted": False}

    def _ph(phase: str, i: int, total: int, msg: str):
        if on_progress:
            try:
                on_progress(phase, i, total, msg)
            except Exception:
                pass

    # Phase 1: discovery
    _ph("discover", 0, 1, "scan user library for top artists")
    conn = library.get_connection()
    artists = discover_user_artists(
        conn, min_tracks=2, top_n=max_dj_artists)
    summary["phases"]["artists"] = artists
    if not artists:
        log_warning("enrich_corpus: no candidate artists in lib")
        summary["aborted"] = True
        return summary
    _ph("discover", 1, 1, f"{len(artists)} artist candidates")

    # Phase 2: scrape (per artist → discover_dj_sets → batch_scrape)
    all_set_urls: list[str] = []
    for i, artist in enumerate(artists, 1):
        if stop_event is not None and stop_event.is_set():
            summary["aborted"] = True
            break
        slug = _artist_to_slug(artist)
        _ph("discover_sets", i, len(artists),
            f"{artist} ({slug})")
        try:
            urls = tracklists.discover_dj_sets(slug, limit=sets_per_dj)
        except tracklists.IPLimitedError as e:
            # 1001tracklists has rate-limited us. Continuing would
            # just hit the same ban on every artist — abort the whole
            # pipeline with a clear, user-facing message.
            log_warning(f"IP rate-limited, aborting pipeline: {e}")
            summary["aborted"] = True
            summary["abort_reason"] = (
                "1001tracklists a bloqué cette IP (rate-limit). "
                "Réessaie dans quelques heures ou via un VPN.")
            urls = []
            break
        except Exception as e:
            log_warning(f"discover_dj_sets({slug}): {e}")
            urls = []
        all_set_urls.extend(urls)

    # Dedup URLs
    all_set_urls = list(dict.fromkeys(all_set_urls))
    summary["phases"]["set_urls_total"] = len(all_set_urls)

    if all_set_urls:
        _ph("scrape", 0, len(all_set_urls), "starting batch scrape")
        scrape_result = tracklists.batch_scrape(
            all_set_urls,
            on_progress=lambda i, t, s, m: _ph("scrape", i, t,
                                                 f"[{s}] {m}"),
            stop_event=stop_event)
        summary["phases"]["scrape"] = scrape_result
        if scrape_result.get("aborted"):
            summary["aborted"] = True

    # Phase 3: resolve missing
    cached = tracklists.list_cached_tracklists()
    scraped_sets = []
    for c in cached:
        try:
            tl = tracklists.fetch_tracklist(c["url"], use_cache=True) \
                if c.get("url") else None
        except Exception:
            tl = None
        # Some cached entries are slug-keyed dicts — load json directly
        if tl is None:
            try:
                import json as _json
                p = Path(c.get("path", "")) if isinstance(c, dict) \
                    else Path("")
                if p.exists():
                    tl = _json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
        if tl:
            scraped_sets.append(tl)
    _ph("resolve", 0, 1,
        f"resolving missing across {len(scraped_sets)} sets")
    missing = resolve_missing(conn, scraped_sets)
    summary["phases"]["missing_count"] = len(missing)

    # Phase 4: download
    paths: list[str] = []
    if missing and not (stop_event and stop_event.is_set()):
        _ph("download", 0, len(missing),
            f"downloading {len(missing)} missing tracks")
        paths = download_missing(
            missing,
            on_progress=lambda i, t, s, m: _ph("download", i, t,
                                                 f"[{s}] {m}"),
            stop_event=stop_event)
        summary["phases"]["downloaded"] = len(paths)

    # Phase 5+6: analyze + purge
    analyzed = 0
    if paths and not (stop_event and stop_event.is_set()):
        _ph("analyze", 0, len(paths),
            f"analysing {len(paths)} new training tracks")
        analyzed = analyze_into_db(
            paths, source="training", mode=mode,
            on_progress=lambda i, t, s, m: _ph("analyze", i, t,
                                                 f"[{s}] {m}"),
            stop_event=stop_event)
        summary["phases"]["analyzed"] = analyzed

    # Phase 7: cooccurrence rebuild
    if not (stop_event and stop_event.is_set()):
        _ph("cooc", 0, 1, "rebuilding cooccurrence matrix")
        try:
            cooc_summary = cooccurrence.rebuild(conn)
            cooccurrence.invalidate_cache()
            summary["phases"]["cooc"] = cooc_summary
        except Exception as e:
            log_warning(f"cooccurrence rebuild failed: {e}")

    summary["total_pairs_after"] = cooccurrence.pair_count(conn)

    # Phase 8: retrain
    summary["model_retrained"] = False
    if retrain and not (stop_event and stop_event.is_set()):
        from app.engine import transition_model
        _ph("train", 0, 1, "retraining L4 model")
        try:
            pairs = transition_model.extract_pairs(conn)
            if not pairs:
                pairs = transition_model.bootstrap_pairs(conn)
            ok = transition_model.train(
                pairs,
                on_progress=lambda f, m: _ph(
                    "train", int(f * 100), 100, m))
            summary["model_retrained"] = bool(ok)
        except Exception as e:
            log_warning(f"retrain failed: {e}")

    log_info(f"enrich_corpus: done — {summary}")
    return summary
