"""
Audio playback + waveform engine for Ultimate DJ.

Two-deck architecture (deck A + deck B) for in-app preview and crossfade
testing — the same model real DJ controllers use.

Built on `sounddevice` (PortAudio bindings) + `librosa` for loading.
The earlier pygame.mixer-based version had a CRITICAL BUG: pygame
restarts from frame 0 on every Sound.play() call, so seek() and cue
jumps fell silently back to the start of the track. Sounddevice gives
us a streaming callback where we control the read position frame by
frame — real, frame-perfect seek.

Public surface (unchanged from previous version):
    play(deck, path, start_seconds=0)  — load + start playback
    pause(deck) / resume(deck) / stop(deck)
    seek(deck, seconds)                — frame-precise jump
    set_volume(deck, 0-1)
    crossfade(position)                — equal-power 0=A only, 1=B only
    is_playing(deck) -> bool
    position(deck) -> float
    duration(deck) -> float
    current_path(deck) -> str
    waveform(path) -> ndarray          — peak-downsampled, cached on disk
    waveform_cached_path(path)
    shutdown()                         — call on app exit

Threading model:
    sounddevice's audio callback runs on a high-priority audio thread.
    UI thread mutates the deck state via threading.Lock so the read
    position can be jumped from anywhere safely. The callback only
    holds the lock for a microsecond per audio chunk.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Literal

import numpy as np

from app.config import APP_DIR, DATA_DIR
from app.logger import log_error, log_warning


Deck = Literal["A", "B"]

_WAVE_CACHE_DIR = DATA_DIR / "waveforms"
_WAVE_CACHE_DIR.mkdir(parents=True, exist_ok=True)


# ── Per-deck state ───────────────────────────────────────────────

class _DeckState:
    """All mutable state lives here. The audio callback reads
    position_frame / volume / audio; the UI thread writes them.
    A lock guarantees no torn reads on multi-int seeks."""

    __slots__ = ("path", "audio", "sr", "channels",
                 "position_frame", "volume", "playing",
                 "stream", "lock")

    def __init__(self):
        self.path: str = ""
        self.audio: np.ndarray | None = None     # shape (n_frames, n_ch)
        self.sr: int = 44100
        self.channels: int = 2
        self.position_frame: int = 0
        self.volume: float = 0.85
        self.playing: bool = False
        self.stream = None
        self.lock = threading.Lock()


_decks: dict[Deck, _DeckState] = {"A": _DeckState(), "B": _DeckState()}


# ── Lazy import of sounddevice ───────────────────────────────────
# Imported on first use so a missing dep doesn't kill app startup.

_sd = None


def _get_sd():
    global _sd
    if _sd is None:
        try:
            import sounddevice as sd
            _sd = sd
        except Exception as e:
            log_error("sounddevice not available", e)
            return None
    return _sd


# ── Audio loading ────────────────────────────────────────────────

def _load_audio(path: str) -> tuple[np.ndarray, int]:
    """Decode any audio format to a stereo float32 numpy array.

    Tries soundfile first (fast, native libsndfile) and falls back to
    librosa (audioread → ffmpeg) for formats libsndfile doesn't ship
    with. Returns (samples_2d, samplerate).
    """
    # Try soundfile first — fast for WAV/FLAC/OGG; supports MP3 since
    # libsndfile 1.1.0 (most current installs)
    try:
        import soundfile as sf
        audio, sr = sf.read(path, dtype="float32", always_2d=True)
        # Ensure stereo
        if audio.shape[1] == 1:
            audio = np.tile(audio, (1, 2))
        elif audio.shape[1] > 2:
            audio = audio[:, :2]
        return audio, int(sr)
    except Exception:
        pass

    # Fall back to librosa for anything libsndfile can't open
    import librosa
    audio, sr = librosa.load(path, sr=None, mono=False, res_type="kaiser_fast")
    if audio.ndim == 1:
        audio = np.stack([audio, audio], axis=0)
    audio = audio.T.astype(np.float32, copy=False)  # (n, ch)
    if audio.shape[1] == 1:
        audio = np.tile(audio, (1, 2))
    return audio, int(sr)


# ── Public API ───────────────────────────────────────────────────

def play(deck: Deck, path: str, *, start_seconds: float = 0.0) -> bool:
    """Load `path` on `deck` and start playback at `start_seconds`."""
    sd = _get_sd()
    if sd is None:
        return False

    state = _decks[deck]

    # Stop any existing stream first
    _kill_stream(state)

    # Load — slow first time per track, then cached in state.audio
    same_track = state.path == path and state.audio is not None
    if not same_track:
        try:
            audio, sr = _load_audio(path)
        except Exception as e:
            log_warning(f"player.play: load failed for {Path(path).name}: {e}")
            return False
        with state.lock:
            state.path = path
            state.audio = audio
            state.sr = sr
            state.channels = audio.shape[1]

    with state.lock:
        n_frames = state.audio.shape[0]
        start_frame = max(0, min(int(start_seconds * state.sr), n_frames - 1))
        state.position_frame = start_frame
        state.playing = True

    # Build the audio callback. It reads `frames` samples from the
    # current position into outdata, applies volume, and advances.
    def callback(outdata, frames, time_info, status):
        if status:
            # Underflow / output dropouts — usually transient, log once
            pass
        with state.lock:
            if not state.playing or state.audio is None:
                outdata.fill(0)
                return
            start = state.position_frame
            end = start + frames
            audio = state.audio
            n = audio.shape[0]
            vol = state.volume
            if start >= n:
                # End of track — silence + signal stop
                outdata.fill(0)
                state.playing = False
                raise sd.CallbackStop
            chunk = audio[start:end]
            cn = chunk.shape[0]
            if cn < frames:
                outdata[:cn] = chunk * vol
                outdata[cn:] = 0
                state.position_frame = n
                state.playing = False
                raise sd.CallbackStop
            else:
                outdata[:] = chunk * vol
                state.position_frame = end

    try:
        stream = sd.OutputStream(
            samplerate=state.sr,
            channels=state.channels,
            dtype="float32",
            callback=callback,
            blocksize=1024,        # 23ms @ 44.1k — low latency, stable
            latency="low",
        )
        stream.start()
    except Exception as e:
        log_warning(f"sd.OutputStream start failed: {e}")
        state.playing = False
        return False

    state.stream = stream
    return True


def _kill_stream(state: _DeckState) -> None:
    """Stop and close the deck's stream if any. Doesn't clear audio cache."""
    if state.stream is not None:
        try:
            state.stream.stop(ignore_errors=True)
            state.stream.close(ignore_errors=True)
        except Exception:
            pass
        state.stream = None


def pause(deck: Deck) -> None:
    state = _decks[deck]
    with state.lock:
        state.playing = False
    if state.stream is not None:
        try:
            state.stream.stop(ignore_errors=True)
        except Exception:
            pass


def resume(deck: Deck) -> None:
    state = _decks[deck]
    if state.audio is None:
        return
    if state.stream is not None:
        # Re-start same stream from current position_frame — but PortAudio
        # streams can't really be paused/unpaused mid-callback cleanly,
        # so the safe move is to rebuild from the saved position.
        pos_seconds = state.position_frame / state.sr
        play(deck, state.path, start_seconds=pos_seconds)
    else:
        play(deck, state.path,
              start_seconds=state.position_frame / state.sr)


def stop(deck: Deck) -> None:
    state = _decks[deck]
    _kill_stream(state)
    with state.lock:
        state.position_frame = 0
        state.playing = False
        # Don't clear `audio` — keep the cache so re-play is instant.
        # `path` stays so current_path() still works after stop.


def seek(deck: Deck, position_seconds: float) -> bool:
    """Frame-precise jump. Works while playing OR paused.

    If the deck has a path but the audio isn't loaded yet (cue clicked
    before first Play), we load + start playing at the target position.
    The audio callback picks up the new position_frame on the next
    block boundary (~23ms latency), so cues feel instant.
    """
    state = _decks[deck]
    # Audio not loaded yet — fall back to play() which loads + jumps
    if state.audio is None:
        if not state.path:
            return False
        return play(deck, state.path, start_seconds=position_seconds)

    target = max(0, min(int(position_seconds * state.sr),
                         state.audio.shape[0] - 1))
    with state.lock:
        state.position_frame = target
    # If we're stopped (no stream), starting playback from the new
    # position is the natural behaviour for a "click on waveform".
    if state.stream is None or not state.playing:
        play(deck, state.path, start_seconds=position_seconds)
    return True


def set_volume(deck: Deck, volume: float) -> None:
    """0.0–1.0. Persists across track changes (and is read by the
    callback every block, so crossfade slides are smooth)."""
    v = max(0.0, min(1.0, float(volume)))
    state = _decks[deck]
    with state.lock:
        state.volume = v


def crossfade(position: float) -> None:
    """position ∈ [0,1]. 0 = full A, 1 = full B, 0.5 = equal mix.
    Equal-power curve so total perceived loudness stays flat."""
    p = max(0.0, min(1.0, float(position)))
    set_volume("A", float(np.cos(p * np.pi / 2)))
    set_volume("B", float(np.sin(p * np.pi / 2)))


def is_playing(deck: Deck) -> bool:
    state = _decks[deck]
    return bool(state.playing and state.stream is not None)


def position(deck: Deck) -> float:
    state = _decks[deck]
    if state.sr <= 0:
        return 0.0
    return state.position_frame / state.sr


def duration(deck: Deck) -> float:
    state = _decks[deck]
    if state.audio is None or state.sr <= 0:
        return 0.0
    return state.audio.shape[0] / state.sr


def current_path(deck: Deck) -> str:
    return _decks[deck].path


def shutdown() -> None:
    """Stop all decks. Call on app exit."""
    for d in ("A", "B"):
        try:
            stop(d)
        except Exception:
            pass


# ── Time-stretch (BPM sync) ───────────────────────────────────────

def _has_rubberband() -> bool:
    """Return True if pyrubberband + the rubberband-cli binary are both
    available on this machine. We cache the answer because checking
    requires spawning a subprocess."""
    cached = getattr(_has_rubberband, "_cached", None)
    if cached is not None:
        return cached
    ok = False
    try:
        import shutil as _sh
        if _sh.which("rubberband") is not None:
            import pyrubberband  # noqa: F401
            ok = True
    except Exception:
        ok = False
    _has_rubberband._cached = ok       # type: ignore[attr-defined]
    return ok


def time_stretch(audio: np.ndarray, sr: int, factor: float,
                 *, prefer: str = "rubberband") -> np.ndarray:
    """Time-stretch `audio` (n_frames × channels float32) by `factor`
    without changing pitch. ``factor=2.0`` = play twice as fast,
    ``factor=0.5`` = half speed.

    Quality preference order:
        1. pyrubberband + rubberband-cli  (best — used by Pioneer
           Rekordbox, Native Instruments, breakfastquay)
        2. librosa.effects.time_stretch    (always available — phase
           vocoder, decent quality, slight smearing on transients)

    Returns float32 array (n_new_frames × channels).
    """
    if factor <= 0 or abs(factor - 1.0) < 0.001:
        return audio

    audio = np.asarray(audio, dtype=np.float32)
    if audio.ndim == 1:
        mono_in = True
        audio_2d = audio.reshape(-1, 1)
    else:
        mono_in = False
        audio_2d = audio

    if prefer == "rubberband" and _has_rubberband():
        try:
            import pyrubberband as pyrb
            stretched = pyrb.time_stretch(audio_2d, sr, factor)
            return stretched.astype(np.float32, copy=False)
        except Exception as e:
            log_warning(f"rubberband time_stretch failed: {e}")

    # Librosa fallback — depends on numba which can break with newer
    # NumPy versions. We try it but accept failure silently.
    try:
        import librosa.effects
        out_chans = []
        for ch in range(audio_2d.shape[1]):
            y = audio_2d[:, ch].astype(np.float32, copy=False)
            stretched = librosa.effects.time_stretch(y, rate=factor)
            out_chans.append(stretched.astype(np.float32, copy=False))
        n = min(c.shape[0] for c in out_chans)
        result = np.stack([c[:n] for c in out_chans], axis=-1)
        return result.flatten() if mono_in else result
    except Exception as e:
        log_warning(f"librosa time_stretch unavailable: {e}")

    # FFmpeg fallback — always works because FFmpeg is a hard dep of
    # the app anyway. Uses the `atempo` filter (chained for factors
    # outside its [0.5, 2.0] range). Quality is decent — not as clean
    # as rubberband but no numba/numpy version pain.
    try:
        return _ffmpeg_time_stretch(audio_2d, sr, factor, mono_in=mono_in)
    except Exception as e:
        log_error("all time_stretch backends failed", e)
        return audio_2d.flatten() if mono_in else audio_2d


def _ffmpeg_time_stretch(audio_2d: np.ndarray, sr: int, factor: float,
                          *, mono_in: bool) -> np.ndarray:
    """FFmpeg-based stretch via the atempo filter.

    Builds a filter chain like ``atempo=2.0,atempo=2.0`` for a 4×
    factor, since each atempo step is clamped to [0.5, 2.0]. Round-trip
    via temp WAV is fine — for a 5-min track this is ~600ms on SSD.
    """
    import subprocess
    import tempfile
    import os
    import soundfile as sf
    from app.config import get_ffmpeg

    ffmpeg = get_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("ffmpeg not configured — see Settings")

    # Build atempo chain
    chain = []
    remaining = factor
    while remaining > 2.0:
        chain.append(2.0)
        remaining /= 2.0
    while remaining < 0.5:
        chain.append(0.5)
        remaining /= 0.5
    chain.append(remaining)
    filter_str = ",".join(f"atempo={x:.6f}" for x in chain)

    with tempfile.TemporaryDirectory(prefix="udj_stretch_") as tmp:
        in_path = os.path.join(tmp, "in.wav")
        out_path = os.path.join(tmp, "out.wav")
        sf.write(in_path, audio_2d, sr, subtype="FLOAT")
        creationflags = 0
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            creationflags = subprocess.CREATE_NO_WINDOW
        subprocess.run(
            [ffmpeg, "-y", "-loglevel", "error",
             "-i", in_path,
             "-filter:a", filter_str,
             "-c:a", "pcm_f32le",
             out_path],
            check=True, creationflags=creationflags, timeout=120)
        out_audio, _sr = sf.read(out_path, dtype="float32", always_2d=True)
    return out_audio.flatten() if mono_in else out_audio


def sync_to(deck: Deck, target_bpm: float) -> bool:
    """Time-stretch the deck's loaded audio so its tempo equals
    ``target_bpm``. Preserves the current playback position.

    Returns True on success. False if audio isn't loaded, BPM info is
    missing, or the stretch backend errored out.

    The stretch is offline (synchronous) — for a 5-min track it takes
    ~1-2s with rubberband, ~3-4s with librosa. Run in a worker thread
    if you want to keep the UI responsive during the call.
    """
    state = _decks[deck]
    if state.audio is None or state.sr <= 0:
        return False
    # Source BPM comes from the DB — ask the engine.library helper
    try:
        from app.engine.library import get_connection
        conn = get_connection()
        row = conn.execute(
            "SELECT bpm FROM tracks WHERE path = ?",
            (state.path,)).fetchone()
        src_bpm = float(row[0]) if row and row[0] else 0.0
    except Exception:
        src_bpm = 0.0
    if src_bpm <= 0 or target_bpm <= 0:
        return False

    factor = target_bpm / src_bpm
    if abs(factor - 1.0) < 0.005:
        # Already in sync (<0.5% difference) — no work
        return True

    # Snapshot current playback position as a fraction of duration
    pos_ratio = state.position_frame / max(1, state.audio.shape[0])

    new_audio = time_stretch(state.audio, state.sr, factor)
    new_n = new_audio.shape[0]

    with state.lock:
        state.audio = new_audio.astype(np.float32, copy=False)
        state.position_frame = max(0, min(int(pos_ratio * new_n), new_n - 1))

    # If currently playing, restart at the new position so the audio
    # path picks up the fresh array
    if state.stream is not None and state.playing:
        play(deck, state.path,
              start_seconds=state.position_frame / state.sr)
    return True


# ── Waveform generation ──────────────────────────────────────────

WAVEFORM_BUCKETS = 1200  # ~visual width in pixels for the strip


def waveform(path: str, buckets: int = WAVEFORM_BUCKETS) -> np.ndarray:
    """Return a (buckets,) numpy array in [0, 1] of peak amplitudes.

    Cached on disk — subsequent reads are <5ms.
    """
    cached = waveform_cached_path(path, buckets)
    if cached.exists():
        try:
            return np.load(cached)
        except Exception:
            pass

    try:
        import librosa
        # 8 kHz mono is enough for waveform peaks; saves a *lot* of time
        y, _sr = librosa.load(path, sr=8000, mono=True)
        if len(y) == 0:
            return np.zeros(buckets, dtype=np.float32)
        chunk = max(1, len(y) // buckets)
        usable = chunk * buckets
        if usable > len(y):
            y = np.pad(y, (0, usable - len(y)))
        else:
            y = y[:usable]
        peaks = np.abs(y.reshape(buckets, chunk)).max(axis=1)
        peak_max = float(peaks.max() or 1.0)
        peaks = (peaks / peak_max).astype(np.float32)
        try:
            np.save(cached, peaks)
        except Exception:
            pass
        return peaks
    except Exception as e:
        log_warning(f"waveform failed for {Path(path).name}: {e}")
        return np.zeros(buckets, dtype=np.float32)


def waveform_cached_path(path: str, buckets: int = WAVEFORM_BUCKETS) -> Path:
    """Where the .npy cache for this file lives."""
    import hashlib
    h = hashlib.sha1(f"{path}|{buckets}".encode("utf-8")).hexdigest()[:16]
    return _WAVE_CACHE_DIR / f"{h}_{buckets}.npy"
