"""
DJ Recorder — capture the controller's MIDI stream.

A Pioneer DDJ-FLX4 (and most USB controllers) exposes a standard MIDI
input that fires a message for every jog, fader, knob, pad and button.
This module listens to that stream and timestamps every event, which
later becomes the "set document" the player agent will replay.

Optional dependency: ``mido`` + ``python-rtmidi`` (installed on demand,
like pyrekordbox). Everything degrades gracefully when they're absent
so importing this never breaks the app.

Two entry points:
    probe(duration)   — learn THIS controller's map: which CC is which
                        fader/knob, which note is which button. Prints a
                        summary while you move each control.
    MidiRecorder      — a live session: start() opens the port on a
                        daemon thread and timestamps every message from
                        t0; stop() returns the event list; save() writes
                        it as JSON.

Event schema (one dict per message)::

    {"t": 12.482,        # seconds since capture start
     "kind": "cc",       # cc | note_on | note_off | pitch | other
     "ch": 0,            # MIDI channel 0-15 (deck / section)
     "num": 25,          # CC number or note number
     "val": 127,         # value 0-127 (fader position, velocity…)
     "raw": [176,25,127]}
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from app.logger import log_info, log_warning

# Controller names we recognise as DJ gear (substring, case-insensitive).
_CONTROLLER_HINTS = ("ddj", "xdj", "flx", "pioneer", "rekordbox", "cdj",
                     "djm", "traktor", "rane", "denon")

# DDJ-FLX4 wake-up: knobs/faders are SILENT until this SysEx is sent to
# the controller's MIDI OUTPUT — it makes every analog control report
# its position and doubles as a keep-alive (resend every few seconds).
# Reverse-engineered by the Mixxx project. mido wraps F0…F7 itself, so
# we pass the inner data bytes only.
_FLX4_INIT_SYSEX = [0x00, 0x40, 0x05, 0x00, 0x00, 0x04, 0x05, 0x00,
                    0x50, 0x02]
_KEEPALIVE_S = 3.0

# DDJ-FLX4 control map (from Mixxx Pioneer-DDJ-FLX4.midi.xml). Keyed by
# (kind, channel, num) → human label. CC numbers are the 7-bit MSB; the
# 14-bit LSB arrives on num+0x20 and is labelled "… (fin)".
_FLX4_MAP: dict[tuple, str] = {}


def _fill_flx4_map() -> None:
    """Complete DDJ-FLX4 map from Mixxx's Pioneer-DDJ-FLX4.midi.xml.
    Channels: deck1 note=0x90/cc=0xB0 (ch0), deck2 0x91/0xB1 (ch1),
    mixer cc 0xB6 (ch6) + browser notes 0x96 (ch6), FX 0x94/0x95
    (ch4/ch5), deck1 pads 0x97 (ch7), deck2 pads 0x99 (ch9)."""
    cc, note = "cc", "note_on"
    for ch, d in ((0, 1), (1, 2)):
        # analog (CC)
        _FLX4_MAP[(cc, ch, 0x00)] = f"tempo D{d}"
        _FLX4_MAP[(cc, ch, 0x04)] = f"trim D{d}"
        _FLX4_MAP[(cc, ch, 0x07)] = f"EQ hi D{d}"
        _FLX4_MAP[(cc, ch, 0x0B)] = f"EQ mid D{d}"
        _FLX4_MAP[(cc, ch, 0x0F)] = f"EQ low D{d}"
        _FLX4_MAP[(cc, ch, 0x13)] = f"volume D{d}"
        _FLX4_MAP[(cc, ch, 0x21)] = f"jog-ring D{d}"
        _FLX4_MAP[(cc, ch, 0x22)] = f"jog D{d}"
        _FLX4_MAP[(cc, ch, 0x23)] = f"jog-vinyl-off D{d}"
        _FLX4_MAP[(cc, ch, 0x29)] = f"jog-search D{d}"
        # transport / buttons (note)
        _FLX4_MAP[(note, ch, 0x0B)] = f"PLAY D{d}"
        _FLX4_MAP[(note, ch, 0x0C)] = f"CUE D{d}"
        _FLX4_MAP[(note, ch, 0x58)] = f"SYNC D{d}"
        _FLX4_MAP[(note, ch, 0x3F)] = f"SHIFT D{d}"
        _FLX4_MAP[(note, ch, 0x36)] = f"jog-touch D{d}"
        _FLX4_MAP[(note, ch, 0x54)] = f"casque D{d}"
        _FLX4_MAP[(note, ch, 0x68)] = f"quantize D{d}"
        # loops
        _FLX4_MAP[(note, ch, 0x10)] = f"LOOP-IN D{d}"
        _FLX4_MAP[(note, ch, 0x11)] = f"LOOP-OUT D{d}"
        _FLX4_MAP[(note, ch, 0x4D)] = f"RELOOP D{d}"
        _FLX4_MAP[(note, ch, 0x50)] = f"reloop-stop D{d}"
        _FLX4_MAP[(note, ch, 0x4C)] = f"loop-in-adj D{d}"
        _FLX4_MAP[(note, ch, 0x4E)] = f"loop-out-adj D{d}"
        _FLX4_MAP[(note, ch, 0x51)] = f"cue-call< D{d}"
        _FLX4_MAP[(note, ch, 0x53)] = f"cue-call> D{d}"
    # performance pads (8 per mode): deck1 ch7, deck2 ch9
    for ch, d in ((7, 1), (9, 2)):
        for i in range(8):
            _FLX4_MAP[(note, ch, 0x00 + i)] = f"pad hotcue{i + 1} D{d}"
            _FLX4_MAP[(note, ch, 0x20 + i)] = f"pad beatjump{i + 1} D{d}"
            _FLX4_MAP[(note, ch, 0x30 + i)] = f"pad sampler{i + 1} D{d}"
            _FLX4_MAP[(note, ch, 0x40 + i)] = f"pad stem{i + 1} D{d}"
            _FLX4_MAP[(note, ch, 0x60 + i)] = f"pad beatloop{i + 1} D{d}"
    # mixer (channel 6)
    _FLX4_MAP[(cc, 6, 0x1F)] = "crossfader"
    _FLX4_MAP[(cc, 6, 0x0C)] = "casque mix"
    _FLX4_MAP[(cc, 6, 0x17)] = "filter D1"
    _FLX4_MAP[(cc, 6, 0x18)] = "filter D2"
    _FLX4_MAP[(cc, 6, 0x40)] = "browse-rotate"
    _FLX4_MAP[(note, 6, 0x41)] = "browse-press"
    _FLX4_MAP[(note, 6, 0x46)] = "LOAD D1"
    _FLX4_MAP[(note, 6, 0x47)] = "LOAD D2"
    # beat FX (ch4 = deck1 side, ch5 = deck2 side)
    _FLX4_MAP[(cc, 4, 0x02)] = "FX level/depth"
    _FLX4_MAP[(note, 4, 0x47)] = "FX on D1"
    _FLX4_MAP[(note, 5, 0x47)] = "FX on D2"
    _FLX4_MAP[(note, 4, 0x63)] = "FX select"
    _FLX4_MAP[(note, 4, 0x10)] = "FX assign D1"
    _FLX4_MAP[(note, 5, 0x11)] = "FX assign D2"


_fill_flx4_map()


def label(ev: dict) -> str:
    """Human name for a captured event, using the FLX4 map. LSB bytes
    (num = MSB+0x20) are folded onto their control with '(fin)'."""
    k = (ev["kind"], ev["ch"], ev["num"])
    if k in _FLX4_MAP:
        return _FLX4_MAP[k]
    msb = (ev["kind"], ev["ch"], ev["num"] - 0x20)
    if ev["num"] >= 0x20 and msb in _FLX4_MAP:
        return _FLX4_MAP[msb] + " (fin)"
    return f"{ev['kind']}:ch{ev['ch']}:num{ev['num']}"


def available() -> bool:
    """True when the MIDI backend is importable."""
    try:
        import mido  # noqa: F401
        import rtmidi  # noqa: F401
        return True
    except Exception:
        return False


def list_inputs() -> list[str]:
    if not available():
        return []
    import mido
    try:
        return list(mido.get_input_names())
    except Exception as e:
        log_warning(f"midi: list_inputs failed: {e}")
        return []


def find_controller(names: list[str] | None = None) -> str | None:
    """Pick the first input port that looks like a DJ controller."""
    names = names if names is not None else list_inputs()
    for n in names:
        low = n.lower()
        if any(h in low for h in _CONTROLLER_HINTS):
            return n
    return names[0] if names else None


def _find_output() -> str | None:
    if not available():
        return None
    import mido
    try:
        for n in mido.get_output_names():
            if any(h in n.lower() for h in _CONTROLLER_HINTS):
                return n
    except Exception:
        pass
    return None


def _decode(msg) -> dict:
    """mido Message → our flat event dict (without the timestamp)."""
    t = msg.type
    if t == "control_change":
        return {"kind": "cc", "ch": msg.channel, "num": msg.control,
                "val": msg.value, "raw": list(msg.bytes())}
    if t in ("note_on", "note_off"):
        return {"kind": t, "ch": msg.channel, "num": msg.note,
                "val": msg.velocity, "raw": list(msg.bytes())}
    if t == "pitchwheel":
        return {"kind": "pitch", "ch": msg.channel, "num": -1,
                "val": msg.pitch, "raw": list(msg.bytes())}
    return {"kind": t, "ch": getattr(msg, "channel", -1), "num": -1,
            "val": -1, "raw": list(msg.bytes())}


class MidiRecorder:
    """Live capture session on one MIDI input port."""

    def __init__(self, port_name: str | None = None):
        self.port_name = port_name or find_controller()
        self._events: list[dict] = []
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._t0 = 0.0

    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self, on_event=None) -> bool:
        if not available():
            log_warning("midi: mido/python-rtmidi not installed")
            return False
        if not self.port_name:
            log_warning("midi: no MIDI input port found")
            return False
        if self.is_running():
            return True
        self._events = []
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, args=(on_event,), daemon=True,
            name="midi-recorder")
        self._thread.start()
        return True

    def _run(self, on_event) -> None:
        import mido
        try:
            port = mido.open_input(self.port_name)
        except Exception as e:
            log_warning(f"midi: cannot open {self.port_name!r}: {e}")
            return
        # Open the controller OUTPUT and send the wake-up SysEx — without
        # it the FLX4 stays silent. Best-effort: capture still runs if
        # the output can't be opened (another controller may not need it).
        outp = None
        out_name = _find_output()
        if out_name:
            try:
                outp = mido.open_output(out_name)
                outp.send(mido.Message("sysex", data=_FLX4_INIT_SYSEX))
                log_info(f"midi: wake-up SysEx sent to {out_name!r}")
            except Exception as e:
                log_warning(f"midi: output/SysEx failed: {e}")
        self._t0 = time.perf_counter()
        last_wake = self._t0
        log_info(f"midi: recording from {self.port_name!r}")
        try:
            with port:
                while not self._stop.is_set():
                    for msg in port.iter_pending():
                        ev = _decode(msg)
                        ev["t"] = round(time.perf_counter() - self._t0, 4)
                        self._events.append(ev)
                        if on_event:
                            try:
                                on_event(ev)
                            except Exception:
                                pass
                    now = time.perf_counter()
                    if outp is not None and now - last_wake > _KEEPALIVE_S:
                        try:
                            outp.send(mido.Message("sysex",
                                                   data=_FLX4_INIT_SYSEX))
                        except Exception:
                            pass
                        last_wake = now
                    time.sleep(0.002)
        except Exception as e:
            log_warning(f"midi: capture loop ended: {e}")
        finally:
            if outp is not None:
                try:
                    outp.close()
                except Exception:
                    pass

    def stop(self) -> list[dict]:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        return list(self._events)

    def save(self, path: str | Path, meta: dict | None = None) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        payload = {"port": self.port_name, "meta": meta or {},
                   "events": self._events}
        p.write_text(json.dumps(payload, indent=1), encoding="utf-8")
        return p


def probe(duration: float = 30.0) -> dict:
    """Learn this controller's map. Opens the controller port and, for
    ``duration`` seconds, records every message; returns a summary of
    the distinct controls seen (so we can label CC 25 = 'crossfader'
    etc.). Run it while moving ONE control at a time.

    Returns {port, n_events, controls: {label: {count, min, max}}}.
    Prints a live line per new control seen.
    """
    if not available():
        return {"error": "mido/python-rtmidi non installés"}
    port = find_controller()
    if not port:
        return {"error": "aucun contrôleur MIDI détecté (branché ?)"}

    seen: dict[tuple, dict] = {}

    def on_event(ev):
        key = (ev["kind"], ev["ch"], ev["num"])
        s = seen.get(key)
        if s is None:
            seen[key] = {"count": 1, "min": ev["val"], "max": ev["val"],
                         "label": label(ev)}
            print(f"  {label(ev):16}  ch{ev['ch']:<2} num{ev['num']:<3} "
                  f"val{ev['val']}")
        else:
            s["count"] += 1
            s["min"] = min(s["min"], ev["val"])
            s["max"] = max(s["max"], ev["val"])

    rec = MidiRecorder(port)
    print(f"Sonde MIDI sur {port!r} — bouge tes contrôles pendant "
          f"{duration:.0f}s…")
    if not rec.start(on_event=on_event):
        return {"error": f"impossible d'ouvrir {port!r} "
                         "(Rekordbox le tient peut-être — ferme-le ou teste "
                         "avec Rekordbox ouvert)"}
    time.sleep(duration)
    events = rec.stop()
    controls = {f"{k[0]}:ch{k[1]}:num{k[2]}": v
                for k, v in sorted(seen.items())}
    print(f"\n{len(events)} messages, {len(controls)} contrôles distincts.")
    return {"port": port, "n_events": len(events), "controls": controls}
