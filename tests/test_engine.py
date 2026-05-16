"""
Engine-level unit tests — pure-Python, no Tk.

Covers the high-leverage modules the smoke tests don't drill into:
embeddings, segmentation, cooccurrence, task registry, repair, score
breakdown. Designed to run in < 5 s on CI.

Run from project root::

    python -m pytest tests/test_engine.py -q
"""
from __future__ import annotations

import json
import os
import sqlite3
import struct
import sys
import tempfile
from pathlib import Path

import numpy as np

# Allow `from app.…` from the test file when run via pytest
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ── helpers ──────────────────────────────────────────────────────

def _wav(path: Path, *, freq: int = 440, duration_s: float = 3.0,
         sr: int = 22050):
    """Write a minimal RIFF/WAV with a steady sine. Used to build
    deterministic fixtures for embeddings / segmentation tests."""
    n = int(duration_s * sr)
    t = np.arange(n) / sr
    samples = (0.4 * np.sin(2 * np.pi * freq * t) * 32767).astype(np.int16)
    with open(path, "wb") as f:
        f.write(b"RIFF" + struct.pack("<I", 36 + n * 2) + b"WAVE")
        f.write(b"fmt " + struct.pack("<I", 16))
        f.write(struct.pack("<HHIIHH", 1, 1, sr, sr * 2, 2, 16))
        f.write(b"data" + struct.pack("<I", n * 2))
        f.write(samples.tobytes())


def _wav_structured(path: Path, *, sr: int = 8000):
    """60s file with 10s quiet + 40s loud + 10s quiet — used as the
    canonical structure-detection fixture."""
    quiet = (0.05 * np.random.randn(10 * sr)).astype(np.float32)
    loud  = (0.45 * np.random.randn(40 * sr)).astype(np.float32)
    audio = np.concatenate([quiet, loud, quiet])
    samples = (audio * 32767).astype(np.int16)
    with open(path, "wb") as f:
        f.write(b"RIFF" + struct.pack("<I", 36 + len(samples) * 2)
                + b"WAVE")
        f.write(b"fmt " + struct.pack("<I", 16))
        f.write(struct.pack("<HHIIHH", 1, 1, sr, sr * 2, 2, 16))
        f.write(b"data" + struct.pack("<I", len(samples) * 2))
        f.write(samples.tobytes())


def _in_mem_db():
    from app.engine import library
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    library.init_schema(conn)
    return conn


# ── embeddings ───────────────────────────────────────────────────

def test_embedding_lite_shape_and_norm(tmp_path):
    from app.engine import embeddings
    p = tmp_path / "tone.wav"
    _wav(p)
    vec = embeddings.embed(str(p), backend="lite")
    assert vec.shape == (embeddings.EMBED_DIM,)
    assert abs(np.linalg.norm(vec) - 1.0) < 0.01


def test_embedding_similar_pitches_score_higher(tmp_path):
    from app.engine import embeddings
    a = tmp_path / "a440.wav"
    b = tmp_path / "a445.wav"
    c = tmp_path / "a880.wav"
    _wav(a, freq=440)
    _wav(b, freq=445)
    _wav(c, freq=880)
    va = embeddings.embed(str(a))
    vb = embeddings.embed(str(b))
    vc = embeddings.embed(str(c))
    sim_close = embeddings.cosine(va, vb)
    sim_far   = embeddings.cosine(va, vc)
    assert sim_close > sim_far


def test_embedding_blob_roundtrip():
    from app.engine import embeddings
    v = np.random.randn(embeddings.EMBED_DIM).astype(np.float32)
    v /= np.linalg.norm(v)
    blob = embeddings.to_blob(v)
    assert isinstance(blob, bytes)
    assert len(blob) == embeddings.EMBED_DIM * 4
    back = embeddings.from_blob(blob)
    assert np.allclose(v, back)
    # None / empty edge cases
    assert embeddings.to_blob(None) is None
    assert embeddings.from_blob(None) is None
    assert embeddings.from_blob(b"") is None


# ── segmentation ─────────────────────────────────────────────────

def test_segmentation_finds_intro_and_outro(tmp_path):
    from app.engine.segmentation import detect_structure
    p = tmp_path / "structured.wav"
    _wav_structured(p)
    r = detect_structure(str(p))
    # 10s quiet → 40s loud → 10s quiet, with ~3s smoothing margin
    assert 7.0 < r["intro_end"] < 13.0
    assert 47.0 < r["outro_start"] < 53.0
    assert 59.0 < r["duration"] < 61.0


def test_segmentation_short_track_is_safe(tmp_path):
    """Tracks under 30s skip segmentation and return safe sentinels."""
    from app.engine.segmentation import detect_structure
    p = tmp_path / "short.wav"
    _wav(p, duration_s=5.0)
    r = detect_structure(str(p))
    assert r["intro_end"] == 0.0
    assert r["outro_start"] > 0  # = duration


# ── transition score breakdown ───────────────────────────────────

def test_breakdown_explains_a_perfect_match():
    from app.engine import library
    a = {"path": "/a", "title": "A", "camelot": "8A",
          "bpm": 124, "energy": 6.0}
    b = {"path": "/b", "title": "B", "camelot": "8A",
          "bpm": 124, "energy": 6.0}
    bd = library.transition_score_breakdown(a, b)
    assert "key" in bd and "bpm" in bd and "energy" in bd
    assert bd["key"]["score"] == 100.0
    assert bd["bpm"]["score"] == 100.0
    # weights sum to 1 (audio weight = 0 when no embeddings)
    s = sum(bd[k]["weight"]
            for k in ("key", "bpm", "energy", "audio"))
    assert abs(s - 1.0) < 0.001
    assert bd["total"] >= 95


def test_breakdown_same_artist_penalty_visible():
    from app.engine import library
    a = {"path": "/a", "title": "Carl Cox - Track 1",
          "camelot": "8A", "bpm": 124, "energy": 6.0}
    b = {"path": "/b", "title": "Carl Cox - Track 2",
          "camelot": "8A", "bpm": 124, "energy": 6.0}
    bd = library.transition_score_breakdown(a, b)
    assert bd["same_artist"] == -8.0


# ── repair ───────────────────────────────────────────────────────

def test_repair_strips_id3_prefix(tmp_path):
    """Simulate the legacy bug (raw ID3v2 bytes before RIFF) and
    verify the repair tool restores the file to valid WAV."""
    from app.engine import repair
    real_wav = tmp_path / "ok.wav"
    _wav(real_wav)
    garbled = tmp_path / "garbled.wav"
    fake_id3 = b"ID3\x04\x00\x00\x00\x00\x07\x76"
    garbled.write_bytes(fake_id3 + real_wav.read_bytes())

    insp = repair.inspect(garbled)
    assert insp["status"] == "corrupt"
    assert insp["offset"] == len(fake_id3)

    fixed = repair.repair(garbled)
    assert fixed["repaired"] is True
    assert garbled.read_bytes()[:4] == b"RIFF"


def test_repair_refuses_files_with_no_magic(tmp_path):
    """The tool MUST refuse to touch a file where the expected
    container magic isn't found at all — never blindly trim."""
    from app.engine import repair
    fake = tmp_path / "fake.wav"
    fake.write_bytes(b"\x00" * 4096)
    info = repair.repair(fake)
    assert info["status"] == "no_magic_found"
    assert info["repaired"] is False


# ── cooccurrence ─────────────────────────────────────────────────

def test_cooccurrence_pairs_adjacent_tracks_higher(tmp_path, monkeypatch):
    """Two tracks adjacent in 3 sets should score higher than two
    tracks that only appear together once at distance 4."""
    from app.engine import cooccurrence
    # Redirect the cache dir to our tmp_path so we don't touch the
    # user's real data/tracklists folder
    monkeypatch.setattr(cooccurrence, "_CACHE_DIR", tmp_path)

    # Three sets — Carl Cox and Adam Beyer adjacent in all three
    for i, dj in enumerate(("dj1", "dj2", "dj3"), 1):
        sets = {
            "url": f"https://x/tracklist/{i}/test.html",
            "title": f"Test {i}", "dj": dj, "tracks": [
                {"position": 1, "artist": "Carl Cox",
                 "title": "Phuture",
                 "raw": "Carl Cox - Phuture"},
                {"position": 2, "artist": "Adam Beyer",
                 "title": "Your Mind",
                 "raw": "Adam Beyer - Your Mind"},
                {"position": 3, "artist": "Random",
                 "title": "Other",
                 "raw": "Random - Other"},
                {"position": 4, "artist": "Reinier",
                 "title": "Move",
                 "raw": "Reinier - Move"},
            ],
        }
        (tmp_path / f"set_{i}.json").write_text(
            json.dumps(sets), encoding="utf-8")

    conn = _in_mem_db()
    from app.engine import library
    local = [
        ("/m/cox.mp3",     "Carl Cox - Phuture"),
        ("/m/beyer.mp3",   "Adam Beyer - Your Mind"),
        ("/m/random.mp3",  "Random - Other"),
        ("/m/reinier.mp3", "Reinier - Move"),
    ]
    for path, title in local:
        library.upsert_track(conn, {
            "path": path, "title": title, "bpm": 130, "key": "C major",
            "camelot": "8B", "energy": 5, "duration": 240})

    summary = cooccurrence.rebuild(conn)
    cooccurrence.invalidate_cache()
    assert summary["sets"] == 3
    assert summary["matched_tracks"] >= 4

    strong = cooccurrence.cooccurrence_score(
        conn, "/m/cox.mp3", "/m/beyer.mp3")
    weak = cooccurrence.cooccurrence_score(
        conn, "/m/cox.mp3", "/m/reinier.mp3")
    assert strong > weak
    assert strong >= 50.0


# ── task registry ────────────────────────────────────────────────

def test_task_registry_basic_flow():
    from app.engine import tasks
    events = []
    tasks.subscribe(lambda: events.append("change"))
    t = tasks.register("Test", message="start")
    tasks.update(t.id, progress=0.5, message="half")
    tasks.complete(t.id, success=True, message="done")
    assert len(events) >= 3
    snap = tasks.list_active()
    # The done task lingers briefly so we can still inspect it
    assert any(x.id == t.id for x in snap)
    finished = [x for x in snap if x.id == t.id][0]
    assert finished.status == "done"


def test_task_registry_cancel_sets_event():
    from app.engine import tasks
    t = tasks.register("Cancellable")
    assert not t.cancel_requested()
    tasks.cancel(t.id)
    assert t.cancel_event.is_set()
    assert t.cancel_requested()
    assert t.status == "cancelled"
