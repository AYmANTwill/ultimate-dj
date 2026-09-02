"""
Reconstruct what happened on each deck from the captured MIDI gestures.

The raw gesture stream (from midi_recorder) is timestamped controller
events. This turns it into two things the autonomous agent needs:

  * a TECHNIQUE TIMELINE — the ordered, reliable list of DJ moves
    (play/pause, cue, beat-jump, loop in/out/reloop, sync, load, FX),
    each with a time and deck. These are exact: they are button presses.

  * a per-deck POSITION estimate — best-effort playhead. It advances in
    real time while the deck plays and is CORRECTED by the moves above
    (pause freezes it, cue resets it, a beat-jump shifts it by N beats
    given the BPM). Not sample-accurate, but it survives jumps instead
    of assuming you played straight through.

    from app.engine.deck_state import reconstruct
    result = reconstruct(gestures, bpm={1: 128.0, 2: 174.0})

``gestures`` is the list saved by the recorder (each has t, kind, ch,
num, val and — if present — ctrl label). Position needs a BPM per deck
(from KUVO / rekordbox later); without it, only the timeline is built.
"""
from __future__ import annotations

# Beat-jump pad → number of beats (FLX4 default beat-jump layout: the
# 8 pads are 1, 2, 4, 8, 16, 32, 64, 128... but the row is user-set;
# this is the common default, override via jump_beats).
_DEFAULT_JUMP_BEATS = {1: 1, 2: 2, 3: 4, 4: 8, 5: 16, 6: 32, 7: 64, 8: 128}


def _deck_of(label: str) -> int | None:
    if label.endswith("D1"):
        return 1
    if label.endswith("D2"):
        return 2
    return None


def _is_press(ev: dict) -> bool:
    # note_on with velocity>0 = press; buttons send 127 on press, 0 on
    # release. CC "moves" are continuous, handled separately.
    return ev.get("kind") == "note_on" and ev.get("val", 0) > 0


def reconstruct(gestures: list[dict], bpm: dict | None = None,
                jump_beats: dict | None = None) -> dict:
    """Return {timeline, decks}. ``bpm`` maps deck→BPM for position;
    ``jump_beats`` maps beat-jump pad index→beats."""
    bpm = bpm or {}
    jumps = jump_beats or _DEFAULT_JUMP_BEATS

    timeline: list[dict] = []
    decks = {1: _new_deck(), 2: _new_deck()}

    for ev in gestures:
        label = ev.get("ctrl") or ""
        d = _deck_of(label)
        t = float(ev.get("t", 0.0))

        # advance position for both playing decks up to this event
        for dk, st in decks.items():
            _advance(st, t)

        if d is None:
            continue
        st = decks[d]
        base = label.rsplit(" D", 1)[0]

        if base == "PLAY" and _is_press(ev):
            st["playing"] = not st["playing"]
            _tech(timeline, t, d, "pause" if not st["playing"] else "play",
                  st["pos"])
        elif base == "CUE" and _is_press(ev):
            st["pos"] = st.get("cue", 0.0)
            _tech(timeline, t, d, "cue", st["pos"])
        elif base == "SYNC" and _is_press(ev):
            _tech(timeline, t, d, "sync", st["pos"])
        elif base.startswith("pad beatjump") and _is_press(ev):
            idx = _pad_index(base)
            beats = jumps.get(idx, 4)
            b = bpm.get(d)
            if b:
                st["pos"] = max(0.0, st["pos"] + beats * 60.0 / b)
            _tech(timeline, t, d, f"beatjump+{beats}", st["pos"])
        elif base.startswith("LOOP") or base == "RELOOP":
            if _is_press(ev):
                _tech(timeline, t, d, base.lower(), st["pos"])
        elif base.startswith("LOAD") and _is_press(ev):
            st.update(_new_deck())   # new track loaded → reset
            _tech(timeline, t, d, "load", 0.0)
        elif base.startswith("pad hotcue") and _is_press(ev):
            _tech(timeline, t, d, base, st["pos"])
        elif base.startswith("FX on") and _is_press(ev):
            _tech(timeline, t, d, "fx", st["pos"])

    return {"timeline": timeline,
            "decks": {k: {"final_pos_s": round(v["pos"], 1),
                          "playing": v["playing"]}
                      for k, v in decks.items()}}


def _new_deck() -> dict:
    return {"playing": False, "pos": 0.0, "cue": 0.0, "_last_t": 0.0}


def _advance(st: dict, t: float) -> None:
    dt = t - st["_last_t"]
    if dt > 0:
        if st["playing"]:
            st["pos"] += dt
        st["_last_t"] = t


def _tech(timeline: list, t: float, deck: int, action: str,
          pos: float) -> None:
    timeline.append({"t": round(t, 2), "deck": deck, "action": action,
                     "pos_s": round(pos, 1)})


def _pad_index(base: str) -> int:
    digits = "".join(c for c in base if c.isdigit())
    return int(digits) if digits else 0
