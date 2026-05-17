"""
Free Music Archive (FMA) Small dataset integration.

The FMA Small dataset (https://github.com/mdeff/fma) is 8 000 tracks
across 8 balanced genres (Electronic / Experimental / Folk / Hip-Hop /
Instrumental / International / Pop / Rock), 30-second clips per track,
~7 GB total. Public domain / Creative Commons.

Why we use it
-------------
The user's library covers ONE musical taste. The L4 Siamese model
learns to distinguish "transitions that work" from "those that don't"
based on audio features — if we only train on the user's 1k techno
tracks, the model never sees what techno *doesn't* look like.

FMA Small gives us 8k diverse audio fingerprints to anchor the
embedding space. We import each track:
1. Extract its 256-d CLAP/PANNs/lite embedding (same backend as user
   tracks → vectors are comparable).
2. Compute BPM/key/energy via librosa.
3. Insert into the DB with ``source = 'fma'`` so it's hidden from the
   Library / Mixer / Setlist pages.
4. In embeddings-only mode, delete the MP3 right after.

Result: 8 000 extra training rows for L4. Storage cost after purge:
~50 MB of vectors. Time cost: ~10-15 h of analysis on a CPU
(interruptible — progress + stop_event throughout).

The bootstrap pipeline (``engine.training_pipeline.enrich_corpus``)
plus FMA gives the L4 model a much richer feature space than the
user library alone would ever provide.

Source URL
----------
The dataset is mirrored at multiple places; we try the official
SWITCH mirror first, fall back to GitHub release. Both are stable.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import time
import zipfile
from pathlib import Path
from typing import Callable, Optional

from app.config import DATA_DIR
from app.logger import log_info, log_warning, log_error


_FMA_DIR = DATA_DIR / "fma"
_FMA_AUDIO_DIR = _FMA_DIR / "fma_small"
_FMA_METADATA_DIR = _FMA_DIR / "fma_metadata"
_FMA_ZIP_PATH = _FMA_DIR / "fma_small.zip"
_FMA_META_ZIP_PATH = _FMA_DIR / "fma_metadata.zip"

# Mirror URLs (try in order)
_FMA_SMALL_URLS = [
    "https://os.unil.cloud.switch.ch/fma/fma_small.zip",
    "https://github.com/mdeff/fma/releases/download/v1.0/fma_small.zip",
]
_FMA_METADATA_URLS = [
    "https://os.unil.cloud.switch.ch/fma/fma_metadata.zip",
    "https://github.com/mdeff/fma/releases/download/v1.0/fma_metadata.zip",
]
# Known sha1 sums from the FMA paper / repo
_FMA_SMALL_SHA1 = "ade154f733639d52e35e32f5593efe5be76c6d70"
_FMA_META_SHA1 = "f0df49ffe5f2a6008d7dc83c6915b31835dfe733"


# ── Download (resumable, progress-aware) ─────────────────────────

def _download_with_resume(urls: list[str], dest: Path, *,
                            expected_sha1: Optional[str] = None,
                            on_progress: Callable | None = None,
                            stop_event=None) -> bool:
    """Try each URL in order, resuming from any partial download.
    Returns True on success.

    on_progress(bytes_done, bytes_total, msg)
    """
    import requests   # cloudscraper is overkill for static file hosts

    dest.parent.mkdir(parents=True, exist_ok=True)
    for url_i, url in enumerate(urls, 1):
        if stop_event is not None and stop_event.is_set():
            return False
        already = dest.stat().st_size if dest.exists() else 0
        headers = {"Range": f"bytes={already}-"} if already > 0 else {}
        log_info(f"fma._download: {url} (resume from {already})")
        try:
            r = requests.get(url, headers=headers, stream=True, timeout=60)
            if r.status_code not in (200, 206):
                log_warning(f"fma._download: HTTP {r.status_code} on {url}")
                continue
            # Total size = already-have + remaining
            content_length = int(r.headers.get("Content-Length", 0))
            total = already + content_length if content_length else 0
            done = already
            mode = "ab" if r.status_code == 206 else "wb"
            with open(dest, mode) as f:
                last_report = 0.0
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    if stop_event is not None and stop_event.is_set():
                        return False
                    if not chunk:
                        continue
                    f.write(chunk)
                    done += len(chunk)
                    now = time.time()
                    if on_progress and now - last_report > 0.5:
                        try:
                            on_progress(done, total,
                                         f"téléchargement "
                                         f"{done/1024/1024:.0f}/"
                                         f"{total/1024/1024:.0f} MB")
                        except Exception:
                            pass
                        last_report = now
        except Exception as e:
            log_warning(f"fma._download failed on {url}: {e}")
            continue
        # Verify
        if expected_sha1:
            actual = _file_sha1(dest)
            if actual != expected_sha1:
                log_warning(
                    f"fma._download: sha1 mismatch (expected "
                    f"{expected_sha1}, got {actual}) — retrying")
                try:
                    dest.unlink()
                except OSError:
                    pass
                continue
        return True
    return False


def _file_sha1(path: Path, chunk_size: int = 1 << 20) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()


# ── Extract ─────────────────────────────────────────────────────

def _extract_zip(zip_path: Path, target_dir: Path, *,
                  on_progress: Callable | None = None,
                  stop_event=None) -> bool:
    """Extract zip_path into target_dir. Skips entries already extracted
    (size match). Returns True on success."""
    if not zip_path.exists():
        return False
    target_dir.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(zip_path) as z:
            members = z.infolist()
            total = len(members)
            for i, m in enumerate(members, 1):
                if stop_event is not None and stop_event.is_set():
                    return False
                out_path = target_dir / m.filename
                if out_path.exists() and out_path.stat().st_size == m.file_size:
                    if on_progress and (i % 50 == 0):
                        on_progress(i, total, f"déjà extrait {i}/{total}")
                    continue
                z.extract(m, target_dir)
                if on_progress and (i % 20 == 0):
                    on_progress(i, total, f"extraction {i}/{total}")
    except Exception as e:
        log_error("fma._extract_zip failed", e)
        return False
    return True


# ── Public API ──────────────────────────────────────────────────

def is_metadata_downloaded() -> bool:
    return _FMA_META_ZIP_PATH.exists() or _FMA_METADATA_DIR.exists()


def is_audio_downloaded() -> bool:
    return _FMA_ZIP_PATH.exists() or _FMA_AUDIO_DIR.exists()


def list_fma_audio_paths(limit: Optional[int] = None) -> list[Path]:
    """Walk the extracted FMA Small audio folder and return MP3 paths."""
    if not _FMA_AUDIO_DIR.exists():
        return []
    out: list[Path] = []
    for p in _FMA_AUDIO_DIR.rglob("*.mp3"):
        out.append(p)
        if limit is not None and len(out) >= limit:
            break
    return out


def download_fma_small(*, on_progress: Callable | None = None,
                        stop_event=None) -> bool:
    """Download + extract fma_small.zip (~7.2 GB of 30-sec audio
    clips, 8000 tracks). Resumable. Returns True on success."""
    if _FMA_AUDIO_DIR.exists() and any(_FMA_AUDIO_DIR.iterdir()):
        log_info("fma.download_fma_small: already extracted")
        return True
    if not _FMA_ZIP_PATH.exists():
        ok = _download_with_resume(
            _FMA_SMALL_URLS, _FMA_ZIP_PATH,
            expected_sha1=_FMA_SMALL_SHA1,
            on_progress=on_progress, stop_event=stop_event)
        if not ok:
            return False
    if on_progress:
        on_progress(0, 0, "extraction du zip…")
    return _extract_zip(_FMA_ZIP_PATH, _FMA_DIR,
                         on_progress=on_progress, stop_event=stop_event)


def download_fma_metadata(*, on_progress: Callable | None = None,
                            stop_event=None) -> bool:
    """Optional — download fma_metadata.zip (~350 MB) for genre / artist
    info. Not required for training (we only need the audio for
    embeddings) but useful for stats / filtering."""
    if _FMA_METADATA_DIR.exists() and any(_FMA_METADATA_DIR.iterdir()):
        return True
    if not _FMA_META_ZIP_PATH.exists():
        ok = _download_with_resume(
            _FMA_METADATA_URLS, _FMA_META_ZIP_PATH,
            expected_sha1=_FMA_META_SHA1,
            on_progress=on_progress, stop_event=stop_event)
        if not ok:
            return False
    return _extract_zip(_FMA_META_ZIP_PATH, _FMA_DIR,
                         on_progress=on_progress, stop_event=stop_event)


def import_into_db(*, mode: str = "embeddings_only",
                    max_tracks: Optional[int] = None,
                    on_progress: Callable | None = None,
                    stop_event=None) -> dict:
    """For every audio file under data/fma/fma_small/, run the standard
    analysis + embedding pipeline and insert into the DB with
    ``source='fma'``. In ``embeddings_only`` mode the MP3 is deleted
    right after — DB row stays trainable.

    ``max_tracks`` caps the import (useful for first-run sanity check —
    pass e.g. 100 to validate the pipeline before letting it chew on
    all 8000).

    Returns summary dict {analyzed, failed, skipped, total}.
    """
    from app.engine.training_pipeline import analyze_into_db

    paths = list_fma_audio_paths(limit=max_tracks)
    if not paths:
        log_warning("fma.import_into_db: no audio found — call "
                    "download_fma_small() first")
        return {"analyzed": 0, "failed": 0, "skipped": 0, "total": 0}

    # Skip tracks already in DB (resume support)
    from app.engine.library import get_connection
    conn = get_connection()
    existing = {
        r[0] for r in conn.execute(
            "SELECT path FROM tracks WHERE source = 'fma'").fetchall()
    }
    todo = [p for p in paths if str(p) not in existing]
    skipped = len(paths) - len(todo)
    log_info(
        f"fma.import_into_db: {len(todo)} to analyse "
        f"({skipped} already in DB), mode={mode}")

    analyzed = analyze_into_db(
        [str(p) for p in todo],
        source="fma",
        mode=mode,
        on_progress=on_progress,
        stop_event=stop_event,
    )
    return {
        "analyzed": analyzed,
        "failed": len(todo) - analyzed,
        "skipped": skipped,
        "total": len(paths),
    }


def cleanup_zip() -> bool:
    """Delete the downloaded zip(s) once extraction succeeded. Saves
    ~7 GB. Safe to call after import_into_db has run successfully."""
    deleted = 0
    for p in (_FMA_ZIP_PATH, _FMA_META_ZIP_PATH):
        if p.exists():
            try:
                p.unlink()
                deleted += 1
            except Exception as e:
                log_warning(f"fma.cleanup_zip: couldn't delete {p}: {e}")
    return deleted > 0
