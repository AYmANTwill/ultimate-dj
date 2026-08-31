"""
Enregistre un set COMPLET :
  * la séquence de morceaux (via l'historique Rekordbox, voie B)
  * ET chaque geste du contrôleur DDJ-FLX4 knob-par-knob (via MIDI +
    la SysEx de réveil, voie A) — même pendant que Rekordbox tourne.

    python record_set.py

Démarre-le AVANT ton set, mixe normalement dans Rekordbox, puis appuie
sur Entrée pour arrêter et sauver le document dans data/recorded_sets/.
"""
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.config import DATA_DIR  # noqa: E402
from app.engine import midi_recorder as mr  # noqa: E402
from app.engine.set_recorder import SetRecorder  # noqa: E402


def main() -> None:
    tracks = SetRecorder(poll_interval=5.0)
    ok = tracks.start(on_track=lambda e: print(
        f"  ♪ #{e['pos']:>2}  {e['artist']} — {e['title']}"))
    if not ok:
        print("Rekordbox / pyrekordbox indispo :", tracks.snapshot().get("error"))

    gestures = None
    if mr.available() and mr.find_controller():
        gestures = mr.MidiRecorder()
        shown = set()

        def on_ev(ev):
            lb = mr.label(ev)
            if lb not in shown:
                shown.add(lb)
                print(f"  🎛️  {lb}")
        if gestures.start(on_event=on_ev):
            print(f"  contrôleur : {gestures.port_name} (réveil MIDI envoyé)")
        else:
            gestures = None
    else:
        print("  (pas de contrôleur MIDI — seuls les morceaux sont captés)")

    print("\n● ENREGISTREMENT — mixe ton set. (Entrée pour arrêter)")
    t0 = time.time()
    try:
        input()
    except KeyboardInterrupt:
        pass

    tdoc = tracks.stop(save=False)
    events = gestures.stop() if gestures else []
    doc = {
        "name": tdoc.get("name") or "Set",
        "started_at": tdoc.get("started_at", ""),
        "duration_s": round(time.time() - t0, 1),
        "tracks": tdoc.get("tracks", []),
        "gestures": [{**e, "ctrl": mr.label(e)} for e in events],
    }
    _save(doc)
    n_ctrl = len({g["ctrl"] for g in doc["gestures"]})
    print(f"\n■ Set sauvé : {doc['name']}")
    print(f"   {len(doc['tracks'])} morceaux · {len(events)} gestes "
          f"({n_ctrl} contrôles) · {doc['duration_s']}s")


def _save(doc: dict) -> None:
    d = DATA_DIR / "recorded_sets"
    d.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^0-9A-Za-z_-]+", "-", doc["name"]).strip("-") or "set"
    (d / f"{safe}.json").write_text(
        json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    print("   ->", d / f"{safe}.json")


if __name__ == "__main__":
    main()
