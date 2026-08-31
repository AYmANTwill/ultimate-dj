"""
MIDI probe — learn your controller's map (step 1 of the DJ Recorder).

Run it with your DDJ plugged in, THEN move your controls one at a time
(crossfader, each channel fader, EQ knobs, tempo, play/cue, pads, FX).
Each new control prints a line; at the end you get the full list.

    python midi_probe.py            # 30s
    python midi_probe.py 60         # 60s

IMPORTANT: try it BOTH with Rekordbox closed AND open. On Windows a
MIDI input is often exclusive — this tells us whether we can capture
your moves while Rekordbox is also using the controller (if not, we add
a MIDI router). Send me the output either way.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.engine import midi_recorder  # noqa: E402


def main() -> None:
    dur = float(sys.argv[1]) if len(sys.argv) > 1 else 30.0
    if not midi_recorder.available():
        print("mido / python-rtmidi manquants — installe :")
        print("   python -m pip install mido python-rtmidi")
        return
    print("Ports MIDI d'entrée :", midi_recorder.list_inputs())
    res = midi_recorder.probe(dur)
    print("\n=== RÉSUMÉ (copie-colle-moi ça) ===")
    print(json.dumps(res, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
