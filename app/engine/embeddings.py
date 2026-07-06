"""
Audio embeddings — track-level vectors that capture sonic identity.

What we actually compute:
    A 256-dimensional float32 vector per track, L2-normalised so that
    `cosine_similarity(a, b) == numpy.dot(a, b)`. Two tracks whose
    embeddings are close in this space sound similar — same timbre,
    same production density, same instrument family — *independently*
    of BPM and key. Stack this on top of the heuristic transition_score
    and the Mixer suggestions stop being "any track with the same
    Camelot" and start being "any track that actually fits the vibe".

Backends, in order of preference:
    1. CLAP (LAION) via `transformers` — state of the art, ~2GB to
       install (torch + transformers + the model). Activated
       transparently if those packages are present. Better on edge
       cases (orchestral vs minimal techno, vocals vs instrumental).
    2. PANNs (CNN14 from AudioSet) via `panns_inference` — solid music
       baseline, smaller install (~200MB), good on genre.
    3. Lite — pure librosa: MFCC + chroma + spectral statistics aggregated
       over the first 60 seconds. Always available because librosa is
       already a hard dep. Decent quality for production prep, no extra
       install. This is what ships by default.

Cache:
    Each embedding lives in the `tracks.embedding` BLOB column (raw
    little-endian float32). Computing one is 0.5-2s per track on the
    lite backend, ~3-5s on CLAP CPU. We never recompute unless the user
    asks (Settings → "Réencoder toute la bibliothèque").

Public API:
    embed(path, backend="auto") -> np.ndarray   — compute + return
    cosine(a, b) -> float                       — single-track scalar sim
    pack/unpack BLOB helpers for the DB
    list_backends() -> list[str]                — which are installed
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Literal, Optional

import numpy as np

from app.logger import log_warning


EMBED_DIM = 256
DTYPE = np.float32

Backend = Literal["lite", "clap", "panns", "auto"]


# ── Backend availability detection ───────────────────────────────

_backend_cache: dict[str, bool] = {}


def _has(name: str) -> bool:
    """Check (cached) whether a heavy optional backend is importable."""
    if name in _backend_cache:
        return _backend_cache[name]
    try:
        if name == "clap":
            import transformers  # noqa: F401
            import torch  # noqa: F401
            _backend_cache[name] = True
        elif name == "panns":
            import panns_inference  # noqa: F401
            _backend_cache[name] = True
        else:
            _backend_cache[name] = False
    except Exception:
        _backend_cache[name] = False
    return _backend_cache[name]


def list_backends() -> list[str]:
    """Names of available backends, ordered by quality."""
    out = []
    if _has("clap"):
        out.append("clap")
    if _has("panns"):
        out.append("panns")
    out.append("lite")    # always present
    return out


def best_backend() -> str:
    return list_backends()[0]


# ── Lite backend (librosa, always available) ─────────────────────

def _decode_audio(path: str, target_sr: int = 22050,
                   max_seconds: float = 60.0) -> tuple[np.ndarray, int]:
    """Decode `path` to a (n,) float32 mono array at `target_sr`.

    Avoids librosa.load on purpose: that path imports numba, which is
    currently broken on NumPy 2.2. Two-tier fallback:
      1. soundfile (libsndfile) — handles WAV/FLAC/OGG and recent MP3.
      2. ffmpeg subprocess piping s16 PCM to stdout — handles every
         format the user has, since FFmpeg is a hard dep of the app.
    """
    try:
        import soundfile as sf
        data, sr = sf.read(path, dtype="float32", always_2d=False)
        if data.ndim == 2:
            data = data.mean(axis=1)        # mixdown to mono
        # Resample if needed (very rare since we'd read at file's sr)
        if sr != target_sr:
            ratio = target_sr / float(sr)
            n_new = int(round(len(data) * ratio))
            xp = np.linspace(0, 1, len(data), endpoint=False)
            x = np.linspace(0, 1, n_new, endpoint=False)
            data = np.interp(x, xp, data).astype(np.float32)
            sr = target_sr
        if max_seconds and len(data) > int(max_seconds * sr):
            data = data[: int(max_seconds * sr)]
        return data.astype(np.float32), sr
    except Exception:
        pass

    # FFmpeg fallback — works for everything ffmpeg can decode (which
    # is everything in practice). Pipes raw s16 PCM through stdout.
    import subprocess
    from app.config import get_ffmpeg
    ff = get_ffmpeg()
    if not ff:
        raise RuntimeError("no audio backend (soundfile failed, "
                           "no ffmpeg configured)")
    creationflags = 0
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        creationflags = subprocess.CREATE_NO_WINDOW
    duration_args = ["-t", str(max_seconds)] if max_seconds else []
    cmd = [ff, "-v", "error", "-i", path, *duration_args,
           "-f", "s16le", "-acodec", "pcm_s16le",
           "-ac", "1", "-ar", str(target_sr), "pipe:1"]
    proc = subprocess.run(cmd, capture_output=True, timeout=120,
                           creationflags=creationflags)
    if proc.returncode != 0 or not proc.stdout:
        raise RuntimeError(f"ffmpeg decode failed for {Path(path).name}")
    raw = np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32)
    raw /= 32768.0
    return raw, target_sr


def _embed_lite(path: str) -> np.ndarray:
    """Pure-numpy spectral fingerprint: log-mel spectrogram statistics
    + MFCC + spectral shape descriptors.

    Avoids librosa.feature.* and librosa.load — both trigger numba,
    which is currently broken on NumPy 2.2. We use soundfile (or
    ffmpeg subprocess) to decode and compute everything else with
    plain numpy. ~80 lines, 0 extra deps, ~1s per track on a 5-min
    file at 22kHz mono.
    """
    SR = 22050
    DUR_S = 60        # encode the first minute — enough to fingerprint
    N_FFT = 1024
    HOP = 512
    N_MELS = 64
    N_MFCC = 20

    y, sr = _decode_audio(path, target_sr=SR, max_seconds=DUR_S)
    if len(y) < sr // 2:
        return np.zeros(EMBED_DIM, dtype=DTYPE)

    # ── STFT → power spectrogram ──
    # numpy-only short-time Fourier with Hann window + reflective padding
    win = np.hanning(N_FFT).astype(np.float32)
    n = len(y)
    n_frames = max(1, 1 + (n - N_FFT) // HOP)
    if n < N_FFT:
        y = np.pad(y, (0, N_FFT - n))
        n = len(y)
        n_frames = 1
    frames = np.lib.stride_tricks.as_strided(
        y, shape=(n_frames, N_FFT),
        strides=(y.strides[0] * HOP, y.strides[0])).copy()
    frames *= win
    spec = np.abs(np.fft.rfft(frames, n=N_FFT, axis=1))
    power = spec ** 2

    # ── Mel filterbank (HTK-style) ──
    def hz_to_mel(hz):
        return 2595.0 * np.log10(1 + hz / 700.0)
    def mel_to_hz(m):
        return 700.0 * (10 ** (m / 2595.0) - 1)
    mel_lo, mel_hi = hz_to_mel(20.0), hz_to_mel(sr / 2)
    mel_pts = np.linspace(mel_lo, mel_hi, N_MELS + 2)
    bin_pts = np.floor((N_FFT + 1) * mel_to_hz(mel_pts) / sr).astype(int)
    bin_pts = np.clip(bin_pts, 0, N_FFT // 2)
    fb = np.zeros((N_MELS, N_FFT // 2 + 1), dtype=np.float32)
    for m in range(N_MELS):
        l, c, r = bin_pts[m], bin_pts[m + 1], bin_pts[m + 2]
        if c > l:
            fb[m, l:c] = (np.arange(l, c) - l) / max(1, c - l)
        if r > c:
            fb[m, c:r] = (r - np.arange(c, r)) / max(1, r - c)

    mel_spec = power @ fb.T                 # (frames, N_MELS)
    log_mel = np.log10(mel_spec + 1e-8)

    # MFCCs via DCT-II of the log-mel
    # Manual DCT (no scipy.fft.dct dependency)
    n_log = log_mel.shape[1]
    k = np.arange(N_MFCC).reshape(-1, 1)
    nn = np.arange(n_log).reshape(1, -1)
    dct_basis = np.cos(np.pi / n_log * (nn + 0.5) * k).astype(np.float32)
    mfccs = log_mel @ dct_basis.T           # (frames, N_MFCC)

    # ── Spectral shape descriptors per frame ──
    freqs = np.linspace(0, sr / 2, N_FFT // 2 + 1, dtype=np.float32)
    spec_sum = spec.sum(axis=1) + 1e-8
    centroid = (spec * freqs).sum(axis=1) / spec_sum
    bandwidth = np.sqrt(((freqs - centroid[:, None]) ** 2 * spec).sum(axis=1)
                          / spec_sum)
    cumspec = np.cumsum(spec, axis=1)
    rolloff_thresh = 0.85 * cumspec[:, -1:]
    rolloff_idx = np.argmax(cumspec >= rolloff_thresh, axis=1)
    rolloff = freqs[rolloff_idx]
    rms = np.sqrt(np.mean(frames ** 2, axis=1))
    zcr = np.mean(np.abs(np.diff(np.sign(frames), axis=1)) / 2, axis=1)

    # ── Aggregate: mean + std of each per-frame series ──
    chunks = []
    for arr in (mfccs.T, log_mel.T):
        chunks.append(arr.mean(axis=1))
        chunks.append(arr.std(axis=1))
    for arr_1d in (centroid, bandwidth, rolloff, rms, zcr):
        chunks.append(np.array([arr_1d.mean(), arr_1d.std()],
                                dtype=np.float32))

    vec = np.concatenate(chunks).astype(DTYPE)
    if vec.size < EMBED_DIM:
        vec = np.pad(vec, (0, EMBED_DIM - vec.size))
    else:
        vec = vec[:EMBED_DIM]
    return _normalize(vec)


# ── CLAP backend (lazy, optional) ────────────────────────────────

_clap_model = None
_clap_processor = None
_clap_lock = threading.Lock()


def _ensure_clap():
    """Load the CLAP model lazily — first call is slow (~10s) because
    the weights download from Hugging Face. Subsequent calls reuse the
    cached model."""
    global _clap_model, _clap_processor
    with _clap_lock:
        if _clap_model is not None:
            return _clap_model, _clap_processor
        from transformers import ClapModel, ClapProcessor
        # 'laion/clap-htsat-unfused' is the default music-leaning CLAP.
        # ~600MB download, then permanently in HF cache.
        _clap_model = ClapModel.from_pretrained("laion/clap-htsat-unfused")
        _clap_processor = ClapProcessor.from_pretrained("laion/clap-htsat-unfused")
        _clap_model.eval()
        return _clap_model, _clap_processor


def _embed_clap(path: str) -> np.ndarray:
    import torch
    import librosa
    model, proc = _ensure_clap()
    # CLAP wants 48kHz mono
    y, sr = librosa.load(path, sr=48000, mono=True, duration=10)
    inputs = proc(audios=y, sampling_rate=48000, return_tensors="pt")
    with torch.no_grad():
        emb = model.get_audio_features(**inputs).cpu().numpy()[0]
    # CLAP gives 512-d; downsize to our standard 256 by averaging pairs
    # so the rest of the pipeline doesn't care about backend.
    if emb.size > EMBED_DIM:
        emb = emb.reshape(EMBED_DIM, -1).mean(axis=1)
    elif emb.size < EMBED_DIM:
        emb = np.pad(emb, (0, EMBED_DIM - emb.size))
    return _normalize(emb.astype(DTYPE))


# ── PANNs backend (lazy, optional) ────────────────────────────────

_panns_inferer = None


def _embed_panns(path: str) -> np.ndarray:
    global _panns_inferer
    import librosa
    if _panns_inferer is None:
        from panns_inference import AudioTagging
        _panns_inferer = AudioTagging(checkpoint_path=None, device="cpu")
    y, sr = librosa.load(path, sr=32000, mono=True, duration=30)
    audio = y[None, :]  # (1, n)
    _, emb = _panns_inferer.inference(audio)
    emb = emb[0]
    # PANNs CNN14 gives 2048-d
    if emb.size > EMBED_DIM:
        # PCA would be cleaner but let's just bin-average for now
        emb = emb.reshape(EMBED_DIM, -1).mean(axis=1)
    return _normalize(emb.astype(DTYPE))


# ── Public API ────────────────────────────────────────────────────

def embed(path: str, backend: Backend = "auto") -> np.ndarray:
    """Compute an embedding for one audio file. Returns a (EMBED_DIM,)
    L2-normalised float32 vector. Returns zeros on hard failure rather
    than raising — keeps bulk encoders crash-resistant."""
    chosen = backend
    if chosen == "auto":
        chosen = best_backend()
    try:
        if chosen == "clap":
            return _embed_clap(path)
        if chosen == "panns":
            return _embed_panns(path)
        return _embed_lite(path)
    except Exception as e:
        log_warning(f"embed({Path(path).name}, {chosen}) failed: {e} — "
                     f"falling back to lite")
        try:
            return _embed_lite(path)
        except Exception as e2:
            log_warning(f"embed lite fallback also failed: {e2}")
            return np.zeros(EMBED_DIM, dtype=DTYPE)


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity of two L2-normalised vectors (so it's just
    a dot product). Returns 0.0 if either is null."""
    if a is None or b is None:
        return 0.0
    if a.size != b.size or a.size == 0:
        return 0.0
    # Inputs are already L2-normalised, so dot = cosine
    return float(np.clip(np.dot(a, b), -1.0, 1.0))


def _normalize(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    if n <= 0:
        return v.astype(DTYPE)
    return (v / n).astype(DTYPE)


# ── BLOB pack / unpack for the DB ─────────────────────────────────

def to_blob(vec: np.ndarray | None) -> bytes | None:
    """numpy → raw bytes for SQLite storage. None stays None."""
    if vec is None or vec.size == 0:
        return None
    return vec.astype(DTYPE).tobytes()


def from_blob(blob: bytes | None) -> np.ndarray | None:
    """SQLite BLOB → numpy. None or empty → None."""
    if not blob:
        return None
    try:
        return np.frombuffer(blob, dtype=DTYPE)
    except Exception as e:
        from app.logger import log_warning
        log_warning(f"embeddings.from_blob: corrupt BLOB "
                    f"({len(blob)} bytes): {e}")
        return None


# ── Similarity calibration ───────────────────────────────────────
# The lite backend clusters ALL dance tracks near cosine 1.0 (measured
# 2026-07-06 on the real library: mean 0.971, p5 0.886 between RANDOM
# pairs). The absolute (cos+1)*50 mapping therefore returns 95-100 for
# every pair — the 20 %-weight audio axis of transition_score was a
# near-constant, not a discriminator. Calibrating against the library's
# OWN pairwise distribution (p5 → 0, p95 → 100) restores contrast
# without changing backend.

_SIM_CAL: tuple[float, float] | None = None
_SIM_CAL_LEGACY = (-1.0, 1.0)
_SIM_CAL_MIN_VECS = 50


def calibrate_similarity(conn, *, sample_pairs: int = 500) -> tuple[float, float]:
    """(lo, hi) cosine anchors from the library's user tracks. Cached
    per process; invalidated whenever an embedding is (re)written.
    Falls back to the legacy absolute mapping below _SIM_CAL_MIN_VECS."""
    global _SIM_CAL
    if _SIM_CAL is not None:
        return _SIM_CAL
    import random
    try:
        rows = conn.execute(
            "SELECT embedding FROM tracks WHERE embedding IS NOT NULL "
            "AND COALESCE(source,'user') = 'user'").fetchall()
    except Exception:
        return _SIM_CAL_LEGACY
    vecs = [v for v in (from_blob(r["embedding"]) for r in rows)
            if v is not None]
    if len(vecs) < _SIM_CAL_MIN_VECS:
        _SIM_CAL = _SIM_CAL_LEGACY
        return _SIM_CAL
    rng = random.Random(1234)
    sims = sorted(
        cosine(*rng.sample(vecs, 2)) for _ in range(sample_pairs))
    lo = sims[int(0.05 * len(sims))]
    hi = sims[int(0.95 * len(sims))]
    _SIM_CAL = _SIM_CAL_LEGACY if hi - lo < 1e-6 else (lo, hi)
    return _SIM_CAL


def invalidate_similarity_calibration() -> None:
    global _SIM_CAL
    _SIM_CAL = None


def similarity_score(c: float, cal: tuple[float, float]) -> float:
    """Map a raw cosine to 0-100 using the calibration anchors."""
    lo, hi = cal
    if cal == _SIM_CAL_LEGACY:
        return max(0.0, min(100.0, (c + 1.0) * 50.0))
    return max(0.0, min(100.0, (c - lo) / (hi - lo) * 100.0))
