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


def test_l4_verdict_classification():
    from app.engine.library import l4_verdict
    assert l4_verdict(70.0, None) == "absent"
    assert l4_verdict(70.0, 3.0) == "neutral"
    assert l4_verdict(70.0, -3.0) == "neutral"
    assert l4_verdict(70.0, 8.0) == "agree"
    assert l4_verdict(30.0, -8.0) == "agree"
    assert l4_verdict(70.0, -8.0) == "dispute"
    assert l4_verdict(30.0, 8.0) == "dispute"


def test_breakdown_exposes_l4_verdict_keys():
    """The Mixer popup + doubt panel rely on these three keys; they
    must exist whether or not a trained model is present."""
    from app.engine import library
    a = {"path": "/a", "title": "A", "camelot": "8A",
          "bpm": 124, "energy": 6.0}
    b = {"path": "/b", "title": "B", "camelot": "8A",
          "bpm": 124, "energy": 6.0}
    bd = library.transition_score_breakdown(a, b)
    assert "heuristic_total" in bd
    assert bd["heuristic_total"] >= 95.0
    assert bd["l4_verdict"] in ("absent", "neutral", "agree", "dispute")
    if bd["l4_delta"] is None:
        assert bd["l4_verdict"] == "absent"
    else:
        assert -10.0 <= bd["l4_delta"] <= 10.0
        assert bd["l4_verdict"] == library.l4_verdict(
            bd["heuristic_total"], bd["l4_delta"])


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


# ── training pipeline ────────────────────────────────────────────

def test_resolve_missing_consumes_matcher_entries(monkeypatch):
    """Regression: match_with_library returns a LIST of entries, not the
    old {'tracks': [...]} dict — resolve_missing must consume it, skip
    matched + placeholder tracks, and dedup case-insensitively."""
    from app.engine import tracklists
    from app.engine import training_pipeline as tp
    entries = [
        {"position": 1, "scraped": {"artist": "A", "title": "Known"},
         "match": ("/m/known.mp3", "A - Known"), "score": 0.95},
        {"position": 2, "scraped": {"artist": "B", "title": "Missing"},
         "match": None, "score": 0.0},
        {"position": 3, "scraped": {"artist": "ID", "title": "ID"},
         "match": None, "score": 0.0},
        {"position": 4, "scraped": {"artist": "b", "title": "missing"},
         "match": None, "score": 0.0},
    ]
    monkeypatch.setattr(tracklists, "match_with_library",
                        lambda tl, conn: entries)
    missing = tp.resolve_missing(None, [{"tracks": []}])
    assert missing == [{"artist": "B", "title": "Missing"}]


# ── tracklists : parser + matcher (B1) ───────────────────────────

_TL_FIXTURE = """<html><body>
<h1>Daft Punk @ Test Festival, France 2026-01-01</h1>
<a href="/dj/daftpunk/index.html">Daft Punk</a>
<div class="bCont tl">
  <div itemprop="tracks" itemscope itemtype="http://schema.org/MusicRecording">
    <meta itemprop="name" content="Daft Punk - Around The World">
    <meta itemprop="byArtist" content="Daft Punk">
    <meta itemprop="duration" content="PT7M10S">
    <meta itemprop="genre" content="House">
  </div>
  <div class="cue noWrap action mt5">00:11</div>
</div>
<div class="bCont tl">
  <div itemprop="tracks" itemscope itemtype="http://schema.org/MusicRecording">
    <meta itemprop="name" content="Modjo - Lady (Hear Me Tonight)">
    <meta itemprop="byArtist" content="Modjo">
  </div>
</div>
<div class="bCont tl">
  <div itemprop="tracks" itemscope itemtype="http://schema.org/MusicRecording">
    <span class="trackValue">ID - ID</span>
  </div>
</div>
</body></html>"""


def test_parse_html_extracts_schema_org_tracks():
    from app.engine.tracklists import _parse_html
    tl = _parse_html(_TL_FIXTURE, url="https://x/tracklist/t/test.html")
    assert tl["dj"] == "Daft Punk"
    assert tl["title"].startswith("Daft Punk @ Test Festival")
    tracks = tl["tracks"]
    assert len(tracks) == 3
    assert tracks[0]["artist"] == "Daft Punk"
    assert tracks[0]["title"] == "Around The World"
    assert tracks[0]["time"] == "00:11"
    assert tracks[1]["artist"] == "Modjo"
    assert tracks[1]["title"].startswith("Lady")
    assert tracks[2]["raw"] == "ID - ID"


def test_parse_iso_duration():
    from app.engine.tracklists import _parse_iso_duration
    assert _parse_iso_duration("PT7M10S") == 430
    assert _parse_iso_duration("PT1H2M3S") == 3723
    assert _parse_iso_duration("PT45S") == 45
    assert _parse_iso_duration("") == 0
    assert _parse_iso_duration("garbage") == 0


def test_name_match_score_precision_first():
    from app.engine.tracklists import name_match_score
    hi = name_match_score("Daft Punk", "Around The World",
                          "Daft Punk - Around The World")
    assert hi >= 0.9
    reorder = name_match_score("Daft Punk", "Around The World",
                               "Around The World - Daft Punk")
    assert reorder >= 0.8
    lo = name_match_score("Carl Cox", "Phuture",
                          "Daft Punk - Around The World")
    assert lo < 0.5
    padded = name_match_score(
        "Gorillaz", "Feel Good Inc",
        "Gorillaz - Feel Good Inc (Instrumental Extended Club Mix)")
    assert padded >= 0.8
    other_track = name_match_score("Gorillaz", "Feel Good Inc",
                                   "Gorillaz - On Melancholy Hill")
    assert other_track < 0.8


def test_is_id_placeholder():
    from app.engine.tracklists import _is_id_placeholder
    assert _is_id_placeholder("ID", "ID") is True
    assert _is_id_placeholder("", "") is True
    assert _is_id_placeholder("id", "Some Title") is True
    assert _is_id_placeholder("x", "y") is True
    assert _is_id_placeholder("Daft Punk", "Around The World") is False


# ── L4 : inférence sans modèle (B1) ──────────────────────────────

def test_l4_score_none_without_model(monkeypatch, tmp_path):
    from app.engine import transition_model as tm
    monkeypatch.setattr(tm, "_MODEL_PATH", tmp_path / "absent.pt")
    monkeypatch.setattr(tm, "_model_cache", None)
    a = {"path": "/a", "title": "A", "bpm": 124, "camelot": "8A",
         "energy": 5.0}
    b = {"path": "/b", "title": "B", "bpm": 126, "camelot": "9A",
         "energy": 6.0}
    assert tm.score(a, b) is None
    assert tm.is_ready() is False


# ── playlist sync ────────────────────────────────────────────────

def test_compute_diff_preserves_spotify_order(tmp_path):
    """Regression: `added` used to be built from a set difference, so
    the download queue lost the source-playlist order."""
    from app.engine import playlist_sync
    kept_file = tmp_path / "b.mp3"
    kept_file.write_bytes(b"x")
    source = [
        {"spotify_id": "a", "artist": "A", "title": "1"},
        {"spotify_id": "b", "artist": "B", "title": "2"},
        {"spotify_id": "c", "artist": "C", "title": "3"},
        {"spotify_id": "d", "artist": "D", "title": "4"},
    ]
    cache = {"tracks": [
        {"spotify_id": "b", "artist": "B", "title": "2",
         "filepath": str(kept_file)},
        {"spotify_id": "d", "artist": "D", "title": "4",
         "filepath": str(tmp_path / "gone.mp3")},
        {"spotify_id": "z", "artist": "Z", "title": "9",
         "filepath": str(kept_file)},
    ]}
    diff = playlist_sync.compute_diff(source, cache)
    assert [t["spotify_id"] for t in diff["added"]] == ["a", "c", "d"]
    assert [t["spotify_id"] for t in diff["kept"]] == ["b"]
    assert [t["spotify_id"] for t in diff["missing"]] == ["d"]
    assert [t["spotify_id"] for t in diff["removed"]] == ["z"]


def test_bootstrap_cache_matches_existing_folder(tmp_path):
    """A folder downloaded before the sync system existed must be
    recognised: matched files become `kept`, only new songs download."""
    from app.engine import playlist_sync
    (tmp_path / "01 - Daft Punk - Around The World.mp3").write_bytes(b"x")
    (tmp_path / "cover.jpg").write_bytes(b"x")
    source = [
        {"spotify_id": "a", "artist": "Daft Punk",
         "title": "Around The World"},
        {"spotify_id": "b", "artist": "Modjo", "title": "Lady"},
    ]
    cache = playlist_sync.bootstrap_cache_from_folder(source, tmp_path)
    assert cache is not None and cache.get("bootstrapped") is True
    assert [t["spotify_id"] for t in cache["tracks"]] == ["a"]

    diff = playlist_sync.compute_diff(source, cache)
    assert [t["spotify_id"] for t in diff["added"]] == ["b"]
    assert [t["spotify_id"] for t in diff["kept"]] == ["a"]


def test_bootstrap_cache_none_when_nothing_matches(tmp_path):
    from app.engine import playlist_sync
    source = [{"spotify_id": "a", "artist": "X", "title": "Y"}]
    assert playlist_sync.bootstrap_cache_from_folder(source, tmp_path) \
        is None
    assert playlist_sync.bootstrap_cache_from_folder(
        source, tmp_path / "absent") is None


def test_write_m3u_orders_entries(tmp_path):
    from app.engine import playlist_sync
    f1 = tmp_path / "First Track.mp3"
    f1.write_bytes(b"x")
    f2 = tmp_path / "Second Track.mp3"
    f2.write_bytes(b"x")
    tracks = [
        {"spotify_id": "1", "artist": "AA", "title": "First",
         "filepath": str(f1)},
        {"spotify_id": "2", "artist": "BB", "title": "Second",
         "filepath": str(f2)},
        {"spotify_id": "3", "artist": "CC", "title": "Gone",
         "filepath": str(tmp_path / "missing.mp3")},
    ]
    p = playlist_sync.write_m3u(tmp_path, 'My "Mix": 2026?', tracks)
    assert p is not None and p.exists() and p.suffix == ".m3u8"
    lines = p.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "#EXTM3U"
    refs = [ln for ln in lines[1:] if ln and not ln.startswith("#")]
    assert refs == ["First Track.mp3", "Second Track.mp3"]


# ── setlist.fm fallback (C2) ─────────────────────────────────────

_SLFM_SETLIST = {
    "artist": {"name": "Daft Punk"},
    "venue": {"name": "Test Arena"},
    "eventDate": "01-01-2026",
    "url": "https://www.setlist.fm/setlist/daft-punk/2026/test.html",
    "sets": {"set": [
        {"song": [
            {"name": "Around The World"},
            {"name": "Feel Good Inc", "cover": {"name": "Gorillaz"}},
            {"name": ""},
        ]},
        {"song": [{"name": "One More Time"}]},
    ]},
}


def test_setlistfm_to_tracklist_maps_cooccurrence_shape():
    from app.engine import setlist_fm
    tl = setlist_fm.to_tracklist(_SLFM_SETLIST)
    assert tl is not None
    assert tl["dj"] == "Daft Punk"
    assert tl["source"] == "setlist.fm"
    assert [t["position"] for t in tl["tracks"]] == [1, 2, 3]
    assert tl["tracks"][0]["raw"] == "Daft Punk - Around The World"
    assert tl["tracks"][1]["artist"] == "Gorillaz"
    assert tl["tracks"][2]["title"] == "One More Time"


def test_setlistfm_rejects_sets_without_pairs():
    from app.engine import setlist_fm
    solo = {"artist": {"name": "X"},
            "sets": {"set": [{"song": [{"name": "Only One"}]}]}}
    assert setlist_fm.to_tracklist(solo) is None


def test_setlistfm_fetch_and_cache_writes_files(tmp_path, monkeypatch):
    from app.engine import setlist_fm
    monkeypatch.setattr(setlist_fm, "_CACHE_DIR", tmp_path)
    monkeypatch.setattr(setlist_fm, "is_configured", lambda: True)
    monkeypatch.setattr(setlist_fm, "_http_get_json",
                        lambda url: {"setlist": [_SLFM_SETLIST]})
    paths = setlist_fm.fetch_and_cache("Daft Punk", limit=5)
    assert len(paths) == 1
    data = json.loads(paths[0].read_text(encoding="utf-8"))
    assert data["dj"] == "Daft Punk"
    assert len(data["tracks"]) == 3


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
