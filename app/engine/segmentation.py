"""
Track-structure segmentation — find the intro / body / outro boundaries
that DJs actually mix on.

Why this exists
---------------
Mixing a track from the middle of its drop into the middle of another
track's drop sounds awful — pros mix from one track's *outro* into the
next track's *intro*. To suggest sane mix points (and to score
transitions on the right slice of audio rather than the whole track)
we need to know where intro ends and outro begins.

Heuristic
---------
1. Decode the file at low SR (8 kHz mono — saves a ton of time, structure
   doesn't need full-band).
2. Compute RMS energy in 1-second windows with 50 % overlap.
3. Smooth with a 5-second moving average.
4. Take the median energy of the *middle 50 %* of the track as the
   "body" reference. Any window with energy ≥ 60 % of that is "loud".
5. ``intro_end`` = first loud window from the start.
6. ``outro_start`` = last loud window from the end.
7. ``drops`` (best-effort) = strong onset peaks inside the body.

Works robustly across techno / house / dnb / hip-hop / trance because
the threshold is *relative* to each track's own loudness — no absolute
dB tuning needed.

Public API
----------
    detect_structure(path) -> dict
        Returns {intro_end, outro_start, drops, duration} in seconds.
        intro_end == 0 and outro_start == duration on tracks too short
        to segment (< 30 s) or that fail to decode.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np

# Decoder lives in embeddings (avoids librosa.load → numba breakage)
from app.engine.embeddings import _decode_audio
from app.logger import log_warning


# Decode SR — low because we only need energy envelope structure
_SR = 8000
# Window / hop for the RMS envelope
_WIN = _SR              # 1 s window
_HOP = _SR // 2         # 0.5 s hop  → 2 fps
# Smoothing kernel (in hops)
_SMOOTH_HOPS = 10       # 5 s moving average
# Body sample = middle 50 % of the track
_BODY_LO_FRAC = 0.25
_BODY_HI_FRAC = 0.75
# A window is "loud" if its smoothed RMS is >= this × body median
_LOUD_THRESHOLD = 0.60
# Min track length we'll bother to segment — anything shorter falls
# back to (0, duration)
_MIN_DURATION_S = 30.0


def detect_structure(path: str) -> dict:
    """Return the intro / outro boundaries + drop hints for a track."""
    out = {
        "intro_end":   0.0,
        "outro_start": 0.0,
        "drops":       [],
        "duration":    0.0,
    }
    try:
        # max_seconds=None → decode the whole file; structure needs the tail
        y, sr = _decode_audio(path, target_sr=_SR, max_seconds=None)
    except Exception as e:
        log_warning(f"segmentation: decode failed for "
                     f"{Path(path).name}: {e}")
        return out

    duration = len(y) / sr
    out["duration"] = round(duration, 2)
    out["outro_start"] = out["duration"]
    if duration < _MIN_DURATION_S:
        # Way too short to have a meaningful intro/outro — bail out
        # cleanly so callers can still tag the file as "segmented".
        return out

    # ── RMS envelope ──
    n_windows = max(1, (len(y) - _WIN) // _HOP + 1)
    rms = np.empty(n_windows, dtype=np.float32)
    for i in range(n_windows):
        seg = y[i * _HOP: i * _HOP + _WIN]
        rms[i] = float(np.sqrt(np.mean(seg ** 2)))

    # Moving-average smoother (same shape, edges padded by repetition)
    if n_windows >= _SMOOTH_HOPS:
        kernel = np.ones(_SMOOTH_HOPS, dtype=np.float32) / _SMOOTH_HOPS
        smooth = np.convolve(rms, kernel, mode="same")
    else:
        smooth = rms

    # ── Body reference + threshold ──
    body_lo = int(n_windows * _BODY_LO_FRAC)
    body_hi = int(n_windows * _BODY_HI_FRAC)
    body = smooth[body_lo:body_hi] if body_hi > body_lo else smooth
    if body.size == 0 or float(body.max()) <= 0:
        return out
    body_med = float(np.median(body))
    if body_med <= 0:
        return out
    threshold = body_med * _LOUD_THRESHOLD

    # ── intro_end: first loud window in the first 60 % of the track ──
    intro_search_end = int(n_windows * 0.60)
    intro_end_idx = 0
    for i in range(intro_search_end):
        if smooth[i] >= threshold:
            intro_end_idx = i
            break
    intro_end_s = (intro_end_idx * _HOP) / sr

    # ── outro_start: last loud window in the second half ──
    outro_search_start = max(intro_end_idx, int(n_windows * 0.40))
    outro_start_idx = n_windows - 1
    for i in range(n_windows - 1, outro_search_start, -1):
        if smooth[i] >= threshold:
            outro_start_idx = i
            break
    outro_start_s = ((outro_start_idx + 1) * _HOP) / sr

    # ── Drops: top energy peaks inside the body ──
    drops = _peak_drops(smooth, body_lo, body_hi, body_med, sr=sr,
                          max_drops=4)

    out["intro_end"]   = round(min(intro_end_s,
                                     duration * 0.5), 2)
    out["outro_start"] = round(max(outro_start_s,
                                     duration * 0.5,
                                     out["intro_end"] + 8.0), 2)
    out["drops"] = drops
    return out


def _peak_drops(smooth: np.ndarray, body_lo: int, body_hi: int,
                 body_med: float, *, sr: int, max_drops: int) -> list[float]:
    """Crude drop detection: local maxima inside the body that are
    significantly above the median. Returned as seconds.

    Anti-clustering: enforce a minimum 8-second gap between two
    detected drops so we don't list every snare hit."""
    if body_hi - body_lo < 4:
        return []
    body = smooth[body_lo:body_hi]
    threshold = body_med * 1.35
    candidates: list[tuple[int, float]] = []
    for i in range(1, len(body) - 1):
        v = body[i]
        if v >= threshold and v > body[i - 1] and v >= body[i + 1]:
            abs_idx = body_lo + i
            candidates.append((abs_idx, float(v)))
    # Sort by amplitude descending, then enforce 8 s spacing
    candidates.sort(key=lambda x: -x[1])
    min_gap_hops = int(8 * sr / _HOP)
    chosen: list[int] = []
    for idx, _ in candidates:
        if all(abs(idx - c) >= min_gap_hops for c in chosen):
            chosen.append(idx)
        if len(chosen) >= max_drops:
            break
    chosen.sort()
    return [round((idx * _HOP) / sr, 2) for idx in chosen]
