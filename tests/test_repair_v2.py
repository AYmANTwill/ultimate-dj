"""
Repair v2 — structural WAV damage (garbage AFTER the data chunk).

The 2026-06 regression appended an ``id3 `` RIFF chunk after ``data``;
Rekordbox 7 / Engine DJ refuse those files while v1's prefix detector
says "ok" (the RIFF magic IS at offset 0). These tests drive the chunk
walker + trailing repair + undo path on synthetic fixtures only.

Run from project root::

    python -m pytest tests/test_repair_v2.py -q
"""
from __future__ import annotations

import hashlib
import struct
import sys
import wave
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ── fixtures ─────────────────────────────────────────────────────

def _wav_bytes(n_samples: int = 4000, sr: int = 8000) -> bytes:
    samples = b"\x00\x01" * n_samples
    return (b"RIFF" + struct.pack("<I", 36 + len(samples)) + b"WAVE"
            + b"fmt " + struct.pack("<I", 16)
            + struct.pack("<HHIIHH", 1, 1, sr, sr * 2, 2, 16)
            + b"data" + struct.pack("<I", len(samples))
            + samples)


def _write_healthy(path: Path) -> bytes:
    raw = _wav_bytes()
    path.write_bytes(raw)
    return raw


def _write_trailing_id3(path: Path, *, fix_riff_size: bool = False) -> bytes:
    """Reproduce the regression: well-formed WAV + an ``id3 `` chunk
    appended after ``data``. By default the RIFF size header is left
    stale (what the bug produced); fix_riff_size=True covers the
    variant where the writer DID update the header."""
    raw = _wav_bytes()
    id3_payload = b"ID3\x04\x00\x00\x00\x00\x00\x0atestdata"
    tail = b"id3 " + struct.pack("<I", len(id3_payload)) + id3_payload
    if len(id3_payload) % 2:
        tail += b"\x00"
    corrupted = raw + tail
    if fix_riff_size:
        corrupted = (corrupted[:4]
                     + struct.pack("<I", len(corrupted) - 8)
                     + corrupted[8:])
    path.write_bytes(corrupted)
    return corrupted


def _write_legit_trailing(path: Path) -> bytes:
    """WAV with a legitimate LIST INFO chunk after data — must be
    flagged 'review', never auto-cut."""
    raw = _wav_bytes()
    info = b"INFOIART" + struct.pack("<I", 6) + b"artist"
    tail = b"LIST" + struct.pack("<I", len(info)) + info
    full = (raw[:4] + struct.pack("<I", len(raw) + len(tail) - 8)
            + raw[8:] + tail)
    path.write_bytes(full)
    return full


@pytest.fixture()
def repair_sandbox(tmp_path, monkeypatch):
    """Redirect repair's on-disk side outputs (history + tail backups)
    into tmp_path so tests never touch the real data/ folder."""
    from app.engine import repair
    monkeypatch.setattr(repair, "_HISTORY_FILE",
                        tmp_path / "repair_history.json")
    monkeypatch.setattr(repair, "_TAILS_DIR", tmp_path / "repair_tails")
    return repair


# ── inspect_chunks ───────────────────────────────────────────────

def test_inspect_chunks_healthy_wav_is_ok(repair_sandbox, tmp_path):
    p = tmp_path / "ok.wav"
    _write_healthy(p)
    info = repair_sandbox.inspect_chunks(p)
    assert info["status"] == "ok"
    assert info["trailing"] == []
    assert info["data_end"] == p.stat().st_size


def test_inspect_chunks_flags_trailing_id3(repair_sandbox, tmp_path):
    p = tmp_path / "corrupt.wav"
    _write_trailing_id3(p)
    info = repair_sandbox.inspect_chunks(p)
    assert info["status"] == "trailing_garbage"
    assert [t["id"] for t in info["trailing"]] == ["id3 "]
    assert info["data_end"] < p.stat().st_size


def test_inspect_chunks_flags_trailing_even_with_correct_riff_size(
        repair_sandbox, tmp_path):
    p = tmp_path / "corrupt2.wav"
    _write_trailing_id3(p, fix_riff_size=True)
    assert repair_sandbox.inspect_chunks(p)["status"] == "trailing_garbage"


def test_inspect_chunks_legit_trailing_is_review(repair_sandbox, tmp_path):
    p = tmp_path / "legit.wav"
    _write_legit_trailing(p)
    info = repair_sandbox.inspect_chunks(p)
    assert info["status"] == "review"


def test_inspect_chunks_refuses_without_data_chunk(repair_sandbox, tmp_path):
    p = tmp_path / "nodata.wav"
    p.write_bytes(b"RIFF" + struct.pack("<I", 28) + b"WAVE"
                  + b"fmt " + struct.pack("<I", 16)
                  + struct.pack("<HHIIHH", 1, 1, 8000, 16000, 2, 16))
    assert repair_sandbox.inspect_chunks(p)["status"] == "no_data_chunk"


def test_inspect_chunks_not_wav(repair_sandbox, tmp_path):
    p = tmp_path / "fake.wav"
    p.write_bytes(b"\x00" * 256)
    assert repair_sandbox.inspect_chunks(p)["status"] == "not_wav"


# ── repair_trailing ──────────────────────────────────────────────

def test_repair_trailing_cuts_and_fixes_header(repair_sandbox, tmp_path):
    p = tmp_path / "corrupt.wav"
    healthy = _wav_bytes()
    _write_trailing_id3(p)
    data_hash_before = hashlib.sha1(healthy).hexdigest()

    result = repair_sandbox.repair_trailing(p)
    assert result["repaired"] is True

    fixed = p.read_bytes()
    assert fixed == healthy
    assert hashlib.sha1(fixed).hexdigest() == data_hash_before
    riff_size = struct.unpack("<I", fixed[4:8])[0]
    assert riff_size == p.stat().st_size - 8
    with wave.open(str(p), "rb") as w:
        assert w.getnframes() > 0


def test_repair_trailing_is_idempotent(repair_sandbox, tmp_path):
    p = tmp_path / "corrupt.wav"
    _write_trailing_id3(p)
    assert repair_sandbox.repair_trailing(p)["repaired"] is True
    second = repair_sandbox.repair_trailing(p)
    assert second["repaired"] is False
    assert second["status"] == "ok"


def test_repair_trailing_refuses_review_files(repair_sandbox, tmp_path):
    p = tmp_path / "legit.wav"
    original = _write_legit_trailing(p)
    result = repair_sandbox.repair_trailing(p)
    assert result["repaired"] is False
    assert result["status"] == "review"
    assert p.read_bytes() == original


def test_repair_trailing_fixes_riff_size_only_mismatch(
        repair_sandbox, tmp_path):
    p = tmp_path / "badsize.wav"
    raw = _wav_bytes()
    p.write_bytes(raw[:4] + struct.pack("<I", 999999) + raw[8:])
    result = repair_sandbox.repair_trailing(p)
    assert result["repaired"] is True
    assert p.read_bytes() == raw


def test_repair_trailing_saves_tail_and_history(repair_sandbox, tmp_path):
    p = tmp_path / "corrupt.wav"
    _write_trailing_id3(p)
    result = repair_sandbox.repair_trailing(p)
    tail_file = result["tail_file"]
    assert tail_file and Path(tail_file).exists()
    entries = repair_sandbox.history(limit=5)
    assert entries and entries[0]["path"] == str(p)
    assert entries[0]["tail_file"] == tail_file
    assert entries[0]["cut_bytes"] == Path(tail_file).stat().st_size


# ── undo ─────────────────────────────────────────────────────────

def test_undo_trailing_restores_byte_identical_file(repair_sandbox,
                                                     tmp_path):
    p = tmp_path / "corrupt.wav"
    corrupted = _write_trailing_id3(p)
    corrupt_hash = hashlib.sha1(corrupted).hexdigest()

    repair_sandbox.repair_trailing(p)
    entry = repair_sandbox.history(limit=1)[0]
    out = repair_sandbox.undo_trailing(
        p, entry["tail_file"],
        riff_size_before=entry["riff_size_before"])
    assert out["restored"] is True
    assert hashlib.sha1(p.read_bytes()).hexdigest() == corrupt_hash


# ── scan_folder integration ──────────────────────────────────────

def test_scan_folder_counts_trailing_separately(repair_sandbox, tmp_path):
    _write_healthy(tmp_path / "a.wav")
    _write_trailing_id3(tmp_path / "b.wav")
    _write_legit_trailing(tmp_path / "c.wav")

    summary = repair_sandbox.scan_folder(tmp_path, dry_run=True)
    assert summary["scanned"] == 3
    assert summary["trailing_corrupt"] == 1
    assert summary["review"] == 1
    assert summary["corrupt"] == 1

    summary = repair_sandbox.scan_folder(tmp_path, dry_run=False)
    assert summary["repaired"] == 1
    assert (tmp_path / "b.wav").read_bytes() == _wav_bytes()
    assert repair_sandbox.inspect_chunks(tmp_path / "c.wav")["status"] \
        == "review"


# ── A2 guard rails: the tag-write path ───────────────────────────

def test_should_write_tags_for_defaults(monkeypatch):
    from app import config
    monkeypatch.setattr(config, "load_config", lambda: {})
    assert config.should_write_tags_for(".mp3") is True
    assert config.should_write_tags_for(".wav") is False
    assert config.should_write_tags_for(".flac") is False
    assert config.should_write_tags_for(".m4a") is False
    assert config.should_write_tags_for(".xyz") is False


def test_write_tags_wav_blocked_without_optin(repair_sandbox, tmp_path,
                                              monkeypatch):
    """force=True bypasses the master toggle but NOT the per-format
    gate — a WAV stays untouched unless write_tags_wav is opted in."""
    from app.engine import analyzer
    p = tmp_path / "t.wav"
    original = _write_healthy(p)
    monkeypatch.setattr(analyzer, "should_write_tags", lambda: True)
    monkeypatch.setattr(analyzer, "should_write_tags_for",
                        lambda ext: ext == ".mp3")
    analyzer.write_tags(str(p), 128.0, "A minor", force=True)
    assert p.read_bytes() == original


def test_write_tags_wav_never_leaves_corrupt_container(
        repair_sandbox, tmp_path, monkeypatch):
    """Opt-in WAV write must end with a clean chunk layout — either the
    tags landed safely or the file was reverted byte-identical."""
    from app.engine import analyzer
    p = tmp_path / "t.wav"
    original = _write_healthy(p)
    monkeypatch.setattr(analyzer, "should_write_tags", lambda: True)
    monkeypatch.setattr(analyzer, "should_write_tags_for",
                        lambda ext: True)
    analyzer.write_tags(str(p), 128.0, "A minor")
    assert repair_sandbox.inspect_chunks(p)["status"] == "ok"
    if p.read_bytes() != original:
        with wave.open(str(p), "rb") as w:
            assert w.getnframes() > 0
