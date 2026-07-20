"""
Track-structure segmentation v2 — find the intro / body / outro
boundaries and the drops that DJs actually mix on. No Rekordbox, no
cues, no external data: pure signal analysis.

Why v1 failed (measured on the real library)
--------------------------------------------
v1 was RMS-only at 8 kHz with a 60 %-of-median loudness threshold.
On loudness-war masters the kick alone puts the FIRST bar above that
threshold → "no intro" on 39 % of tracks, "no outro" on 41 %, zero
drops on 72 %. A DJ intro isn't QUIETER on modern masters — it's
SPARSER: no hats, no leads, no vocals. RMS can't see arrangement;
the spectrum can.

The v2 idea
-----------
1. Decode at 22 050 Hz mono (Nyquist 11 kHz — enough to see hats).
2. Per 1-second window (0.5 s hop): full-band RMS **and** the
   high-frequency power ratio (share of spectral power ≥ 4 kHz,
   where hats / leads / vocals live).
3. Smooth both with a 5 s moving average.
4. Body reference = medians over the middle 50 % of the track.
5. A window is "full arrangement" when BOTH hold:
       rms  ≥ 40 % of body-median rms
       hfr  ≥ 60 % of body-median hf-ratio
6. intro_end  = start of the first ≥ 8 s PERSISTENT full run
   (a lone FX sweep or vocal shout can't fake 8 s of fullness);
   outro_start = end of the last such run, scanned backwards.
7. Drops = breakdown→drop pattern: ≥ 6 s of low fullness followed by
   a sharp combined-energy jump. Local maxima above a flat median
   (v1) simply don't exist on compressed masters.

Tracks with no HF content at all (dark ambient, old rips) fall back
to the v1 RMS-only rule so nothing regresses to nonsense.

Public API (unchanged from v1)
------------------------------
    detect_structure(path) -> dict
        {intro_end, outro_start, drops, duration} in seconds.
        intro_end == 0 and outro_start == duration on tracks too
        short to segment (< 30 s) or that fail to decode.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

# Decoder lives in embeddings (avoids librosa.load → numba breakage)
from app.engine.embeddings import _decode_audio
from app.logger import log_warning

_SR = 22050              # need ≥ ~11 kHz Nyquist to see hats
_WIN = _SR               # 1 s analysis window
_HOP = _SR // 2          # 0.5 s hop → 2 fps
_SMOOTH_HOPS = 10        # 5 s moving average
_BODY_LO_FRAC = 0.25     # body reference = middle 50 %
_BODY_HI_FRAC = 0.75
_RMS_FLOOR = 0.40        # window is loud enough at 40 % of body rms…
_HF_FLOOR = 0.60         # …and full enough at 60 % of body hf-ratio
_HF_SPLIT_HZ = 4000.0    # hats/leads/vocals live above this
_PERSIST_HOPS = 16       # fullness must hold 8 s to count
_PERSIST_RELAX = 8       # …relaxed to 4 s if nothing qualifies
_MIN_DURATION_S = 30.0
_DROP_TROUGH_HOPS = 12   # ≥ 6 s of breakdown before a drop
_DROP_TROUGH_LVL = 0.60  # trough = combined energy under 60 % of med
_DROP_JUMP_LVL = 0.35    # jump ≥ 35 % of median within 2 hops
_DROP_MAX = 4
_DROP_GAP_S = 8.0


def detect_structure(path: str) -> dict:
    """Return the intro / outro boundaries + drop hints for a track."""
    out = {
        "intro_end":   0.0,
        "outro_start": 0.0,
        "drops":       [],
        "duration":    0.0,
    }
    try:
        y, sr = _decode_audio(path, target_sr=_SR, max_seconds=None)
    except Exception as e:
        log_warning(f"segmentation: decode failed for "
                    f"{Path(path).name}: {e}")
        return out

    duration = len(y) / sr
    out["duration"] = round(duration, 2)
    out["outro_start"] = out["duration"]
    if duration < _MIN_DURATION_S:
        return out

    rms, hfr = _envelopes(y)
    n = len(rms)
    if n < 8:
        return out
    rms_s = _smooth(rms)
    hfr_s = _smooth(hfr)

    body_lo = int(n * _BODY_LO_FRAC)
    body_hi = max(body_lo + 1, int(n * _BODY_HI_FRAC))
    rms_med = float(np.median(rms_s[body_lo:body_hi]))
    hf_med = float(np.median(hfr_s[body_lo:body_hi]))
    if rms_med <= 0:
        return out

    full = rms_s >= (rms_med * _RMS_FLOOR)
    if hf_med > 1e-4:
        full &= hfr_s >= (hf_med * _HF_FLOOR)
    # else: no HF content anywhere (ambient / dark rip) → RMS-only,
    # which is exactly the v1 behaviour.

    intro_idx = _first_persistent(full[: int(n * 0.60)])
    outro_idx = _last_persistent(full, start=max(intro_idx,
                                                 int(n * 0.40)))

    intro_end_s = (intro_idx * _HOP) / sr
    outro_start_s = ((outro_idx + 1) * _HOP) / sr

    out["intro_end"] = round(min(intro_end_s, duration * 0.5), 2)
    out["outro_start"] = round(
        max(outro_start_s, duration * 0.5, out["intro_end"] + 8.0), 2)
    out["drops"] = _find_drops(rms_s, hfr_s, rms_med, hf_med,
                                body_lo, body_hi, sr=sr)
    return out


# ── Signal helpers ──────────────────────────────────────────────

def _envelopes(y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Per-window full-band RMS and high-frequency power ratio."""
    n_windows = max(1, (len(y) - _WIN) // _HOP + 1)
    rms = np.empty(n_windows, dtype=np.float32)
    hfr = np.empty(n_windows, dtype=np.float32)
    freqs = np.fft.rfftfreq(_WIN, d=1.0 / _SR)
    hf_mask = freqs >= _HF_SPLIT_HZ
    for i in range(n_windows):
        seg = y[i * _HOP: i * _HOP + _WIN]
        rms[i] = float(np.sqrt(np.mean(seg ** 2)))
        spec = np.abs(np.fft.rfft(seg)) ** 2
        total = float(spec.sum())
        hfr[i] = float(spec[hf_mask].sum() / total) if total > 0 else 0.0
    return rms, hfr


def _smooth(x: np.ndarray) -> np.ndarray:
    if len(x) < _SMOOTH_HOPS:
        return x
    kernel = np.ones(_SMOOTH_HOPS, dtype=np.float32) / _SMOOTH_HOPS
    return np.convolve(x, kernel, mode="same")


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """[(start, length)] of consecutive True runs."""
    runs, start = [], None
    for i, v in enumerate(mask):
        if v and start is None:
            start = i
        elif not v and start is not None:
            runs.append((start, i - start))
            start = None
    if start is not None:
        runs.append((start, len(mask) - start))
    return runs


def _first_persistent(mask: np.ndarray) -> int:
    """Start index of the first run ≥ PERSIST hops (relaxed once)."""
    runs = _runs(mask)
    for need in (_PERSIST_HOPS, _PERSIST_RELAX):
        for start, length in runs:
            if length >= need:
                return start
    return 0


def _last_persistent(mask: np.ndarray, *, start: int) -> int:
    """End index (inclusive) of the last run ≥ PERSIST hops after
    ``start`` (relaxed once). Falls back to the last index."""
    runs = [(s, ln) for s, ln in _runs(mask) if s + ln > start]
    for need in (_PERSIST_HOPS, _PERSIST_RELAX):
        for s, ln in reversed(runs):
            if ln >= need:
                return s + ln - 1
    return len(mask) - 1


def _find_drops(rms_s: np.ndarray, hfr_s: np.ndarray,
                rms_med: float, hf_med: float,
                body_lo: int, body_hi: int, *, sr: int) -> list[float]:
    """Breakdown→drop detector: a sustained trough of combined energy
    followed by a sharp rise. Returns drop times in seconds, ranked by
    jump size, min 8 s apart, max 4."""
    if body_hi - body_lo < _DROP_TROUGH_HOPS + 4 or rms_med <= 0:
        return []
    # Combined "fullness" energy: rms weighted by spectral richness
    # (when there's no HF signal at all, plain rms).
    if hf_med > 1e-4:
        comb = (rms_s / rms_med) * np.clip(hfr_s / hf_med, 0.0, 2.0)
    else:
        comb = rms_s / rms_med
    candidates: list[tuple[float, int]] = []
    for i in range(body_lo + _DROP_TROUGH_HOPS, body_hi - 2):
        trough = comb[i - _DROP_TROUGH_HOPS: i]
        if float(trough.mean()) >= _DROP_TROUGH_LVL:
            continue
        jump = float(comb[i + 1: i + 3].max() - trough.mean())
        if jump < _DROP_JUMP_LVL:
            continue
        candidates.append((jump, i + 1))
    candidates.sort(key=lambda t: -t[0])
    min_gap = int(_DROP_GAP_S * sr / _HOP)
    chosen: list[int] = []
    for _, idx in candidates:
        if all(abs(idx - c) >= min_gap for c in chosen):
            chosen.append(idx)
        if len(chosen) >= _DROP_MAX:
            break
    chosen.sort()
    return [round((idx * _HOP) / sr, 2) for idx in chosen]
