"""
Enregistre un set (voie B) — lit l'historique temps réel de Rekordbox
et note la séquence de morceaux. Aucun contrôleur requis.

    python record_set.py

Démarre-le AVANT ton set, mixe normalement dans Rekordbox, puis appuie
sur Entrée (ou Ctrl+C) pour arrêter et sauver le document du set dans
data/recorded_sets/.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.engine.set_recorder import SetRecorder  # noqa: E402


def main() -> None:
    rec = SetRecorder(poll_interval=5.0)
    if not rec.start(on_track=lambda e: print(
            f"  ♪ #{e['pos']:>2}  {e['artist']} — {e['title']}")):
        print("Impossible de démarrer (Rekordbox / pyrekordbox ?).")
        print("Erreur :", rec.snapshot().get("error"))
        return
    print("● ENREGISTREMENT — mixe ton set dans Rekordbox.")
    print("  (Entrée pour arrêter et sauver)")
    try:
        input()
    except KeyboardInterrupt:
        pass
    doc = rec.stop(save=True)
    print(f"\n■ Set sauvé : {doc['name']} — {len(doc['tracks'])} morceaux")
    print("  data/recorded_sets/")


if __name__ == "__main__":
    main()
