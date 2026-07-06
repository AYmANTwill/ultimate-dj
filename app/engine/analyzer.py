"""
Audio analysis engine — BPM, musical key, energy detection via librosa.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import librosa
import numpy as np
import mutagen
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, TBPM, TKEY, ID3NoHeaderError

from app.config import (CAMELOT_MAP, load_config, should_write_tags,
                        should_write_tags_for)
from app.logger import log_warning

# Krumhansl-Kessler key profiles
_MAJOR_PROFILE = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09,
                            2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
_MINOR_PROFILE = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53,
                            2.54, 4.75, 3.98, 2.69, 3.34, 3.17])

_PITCH_NAMES = ["C", "C#", "D", "D#", "E", "F",
                "F#", "G", "G#", "A", "A#", "B"]


def detect_key(y: np.ndarray, sr: int) -> tuple[str, float]:
    """Detect musical key using chroma features + Krumhansl-Kessler.

    Returns (key, confidence) where confidence is in 0.0–1.0:
    - 1.0 = perfect lock to one profile
    - 0.0 = correlations are flat (key is genuinely ambiguous)

    The confidence is computed as the gap between the best and the
    second-best correlation, normalised — when the chroma profile fits
    *one* key clearly better than all others the gap is wide; when the
    track is atonal/percussive the gap is narrow.
    """
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
    mean_chroma = chroma.mean(axis=1)

    correlations: list[tuple[float, str]] = []
    for shift in range(12):
        rolled = np.roll(mean_chroma, -shift)
        major_corr = float(np.corrcoef(rolled, _MAJOR_PROFILE)[0, 1])
        minor_corr = float(np.corrcoef(rolled, _MINOR_PROFILE)[0, 1])
        correlations.append((major_corr, f"{_PITCH_NAMES[shift]} major"))
        correlations.append((minor_corr, f"{_PITCH_NAMES[shift]} minor"))

    correlations.sort(key=lambda x: -x[0])
    best_corr, best_key = correlations[0]
    second_corr, _ = correlations[1] if len(correlations) > 1 else (0.0, "")

    # Confidence: gap between top-2 normalised by the top correlation.
    # Clamped so the score is always in [0, 1].
    if best_corr > 0:
        gap = max(0.0, best_corr - second_corr)
        confidence = min(1.0, gap / max(best_corr, 0.01) * 2.0)
    else:
        confidence = 0.0

    return best_key, round(confidence, 3)


def detect_bpm(y: np.ndarray, sr: int) -> float:
    """Detect BPM using librosa beat tracker."""
    tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
    if isinstance(tempo, np.ndarray):
        tempo = float(tempo[0])
    return round(float(tempo), 1)


def detect_beat_grid(y: np.ndarray, sr: int,
                      *, max_beats: int = 4096) -> tuple[float, list[float]]:
    """Detect BPM AND the position (in seconds) of every beat onset.

    The DJ-relevant payload here is the beat list: with it the deck can
    paint a beat-grid overlay on the waveform, and the mixer can offer
    real BPM-sync (time-stretch one deck so the beats line up with the
    other deck's grid).

    Returns (bpm, beats_seconds). `beats_seconds` is capped at
    `max_beats` entries to keep DB rows compact — for a 6-minute track
    at 130 BPM that's ~780 beats, well within the cap.
    """
    tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
    if isinstance(tempo, np.ndarray):
        tempo = float(tempo[0])
    beat_times = librosa.frames_to_time(beat_frames, sr=sr)
    beats = [round(float(t), 4) for t in beat_times[:max_beats]]
    return round(float(tempo), 1), beats


def detect_energy(y: np.ndarray) -> float:
    """Compute RMS energy as a 0-10 scale."""
    rms = float(np.sqrt(np.mean(y ** 2)))
    return round(min(rms * 50, 10.0), 1)


def get_duration(path: str) -> float:
    """Get track duration in seconds. Uses mutagen.File auto-detect so
    WAV/FLAC/M4A/OGG don't all return 0.0 like they did when this was
    hard-wired to the MP3 class.
    """
    try:
        audio = mutagen.File(path)
        if audio is not None and audio.info is not None:
            return round(float(audio.info.length), 1)
    except Exception:
        pass
    # Last-resort fallback for MP3s that mutagen.File somehow refuses
    try:
        return round(MP3(path).info.length, 1)
    except Exception as e:
        log_warning(f"get_duration: unreadable {Path(path).name} "
                    f"— duration set to 0: {e}")
        return 0.0


def get_bitrate(path: str) -> int:
    """Container bitrate in kbps via mutagen, 0 when unknown. WAV/FLAC
    report their true lossless rate (~900-1500); lossy containers report
    the encoded rate — note that a transcode keeps the container rate,
    so this is a floor-quality hint, not proof of source quality."""
    try:
        audio = mutagen.File(path)
        br = getattr(getattr(audio, "info", None), "bitrate", 0) or 0
        return int(br // 1000) if br > 10000 else int(br)
    except Exception:
        return 0


def analyze_track(path: str) -> dict:
    """
    Full analysis of an audio file.
    Returns dict with: bpm, key, camelot, energy, duration, path.
    """
    cfg = load_config()
    dur = cfg.get("analysis_duration", 90)

    y, sr = librosa.load(path, sr=22050, mono=True, duration=dur)
    bpm, beat_grid = detect_beat_grid(y, sr)
    key, key_confidence = detect_key(y, sr)
    energy = detect_energy(y)
    camelot = CAMELOT_MAP.get(key, "?")
    duration = get_duration(path)

    # Structure boundaries (intro/outro/drops). Best-effort — if it
    # fails, we just leave the fields None and the Mixer falls back to
    # whole-track scoring. Reads the FULL file at low SR (8 kHz) since
    # outro detection needs the tail librosa.load truncated above.
    try:
        from app.engine.segmentation import detect_structure
        struct = detect_structure(path)
    except Exception:
        struct = {"intro_end": None, "outro_start": None, "drops": []}

    # Extract title from filename
    title = Path(path).stem
    # Clean numbered prefixes like "01 - "
    import re
    title = re.sub(r"^\d+\s*[-_.]\s*", "", title)

    return {
        "path": str(Path(path).resolve()),
        "title": title,
        "bpm": bpm,
        "key": key,
        "camelot": camelot,
        "energy": energy,
        "duration": duration,
        "key_confidence": key_confidence,
        "bitrate": get_bitrate(path),
        "beat_grid": beat_grid,
        "intro_end":   struct.get("intro_end"),
        "outro_start": struct.get("outro_start"),
        "drops":       struct.get("drops") or [],
    }


def write_tags(path: str, bpm: float, key: str, *, force: bool = False):
    """Write BPM + key into the file's tag system, picking the right
    container per extension.

    Only runs when the user has explicitly opted in via Settings →
    ``write_tags_to_files``. By default we keep ALL metadata in our DB
    so Rekordbox / Engine DJ / Serato do their own analysis untouched
    when they import the same files.

    Pass ``force=True`` to bypass the user setting (used by the « Export
    tags now » button).

    !!! CRITICAL HISTORY !!!
    The previous implementation called ``ID3(path).save(path)`` for ALL
    files regardless of extension. For WAV/FLAC/M4A this PREPENDS raw
    ID3v2 bytes before the file's header (RIFF / fLaC / ftyp), which
    corrupts the container — Rekordbox 7, Engine DJ and stricter
    decoders refuse to open the file. The repair tool in
    ``engine.repair`` undoes that damage by stripping the pre-magic
    bytes; this function now dispatches per-extension so it never
    corrupts again.
    """
    if not force and not should_write_tags():
        # Default — DJ doesn't want us mutating their files.
        # Metadata stays in the SQLite library only.
        return
    ext = Path(path).suffix.lower()
    if not should_write_tags_for(ext):
        # Per-format gate — deliberately survives force=True for the
        # risky containers (post-corruption-regression policy).
        log_warning(f"write_tags refused for {Path(path).name}: "
                    f"format {ext or '?'} not opted-in in Settings")
        return
    if ext != ".mp3":
        from app.engine import repair as _repair
        magic = _repair._expected_magic(Path(path))
        if (magic is None
                or _repair._find_magic_offset(Path(path), magic) != 0):
            log_warning(f"write_tags skipped {Path(path).name}: container "
                        f"magic not at offset 0 — run Réparation first")
            return
    bpm_int = int(round(float(bpm or 0)))
    key_str = (key or "").strip()

    try:
        if ext == ".mp3":
            try:
                tags = ID3(path)
            except ID3NoHeaderError:
                tags = ID3()
            tags["TBPM"] = TBPM(encoding=3, text=[str(bpm_int)])
            if key_str:
                tags["TKEY"] = TKEY(encoding=3, text=[key_str])
            tags.save(path)

        elif ext == ".wav":
            # WAV files store ID3 inside an "id3 " RIFF chunk. Mutagen
            # writes it AFTER the data chunk, which Rekordbox 7 /
            # Engine DJ reject — so the write is verified-or-reverted:
            # snapshot first, re-walk the chunks after save, restore the
            # snapshot byte-identical if the layout is no longer clean.
            from app.engine.repair import inspect_chunks
            pre = inspect_chunks(path)
            if pre["status"] != "ok":
                log_warning(f"write_tags skipped {Path(path).name}: "
                            f"WAV structure {pre['status']} — repair first")
                return
            snapshot = Path(path).read_bytes()
            from mutagen.wave import WAVE
            audio = WAVE(path)
            if audio.tags is None:
                audio.add_tags()
            audio.tags["TBPM"] = TBPM(encoding=3, text=[str(bpm_int)])
            if key_str:
                audio.tags["TKEY"] = TKEY(encoding=3, text=[key_str])
            audio.save()
            post = inspect_chunks(path)
            if post["status"] != "ok":
                Path(path).write_bytes(snapshot)
                log_warning(
                    f"write_tags reverted {Path(path).name}: save produced "
                    f"{post['status']} — file restored byte-identical")

        elif ext == ".flac":
            from mutagen.flac import FLAC
            audio = FLAC(path)
            audio["BPM"] = str(bpm_int)
            if key_str:
                audio["KEY"] = key_str
            audio.save()

        elif ext in (".m4a", ".mp4", ".aac"):
            from mutagen.mp4 import MP4
            audio = MP4(path)
            # iTunes "freeform" atoms — Rekordbox reads the BPM atom
            audio["tmpo"] = [bpm_int]
            if key_str:
                audio["----:com.apple.iTunes:initialkey"] = [
                    key_str.encode("utf-8")]
            audio.save()

        elif ext in (".ogg", ".oga", ".opus"):
            from mutagen.oggvorbis import OggVorbis
            audio = OggVorbis(path)
            audio["BPM"] = str(bpm_int)
            if key_str:
                audio["KEY"] = key_str
            audio.save()

        # else: unknown format — refuse to touch it (better than corrupting)
    except Exception as e:
        # Don't propagate — analysis should never crash the app — but
        # do log it so we can spot recurring container weirdness.
        log_warning(f"write_tags failed for {Path(path).name}: {e}")
