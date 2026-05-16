"""
Standalone script to train the L4 transition model from the user's
scraped tracklists + encoded library.

Prerequisites
-------------
1. Spotify creds set + Discover used to scrape ~50+ DJ sets
2. Settings → AI · Co-occurrence → Reconstruire la matrice
3. Settings → AI · Embeddings audio → Encoder les nouveaux
4. ``pip install torch`` (the only optional dep this needs)

Usage
-----
    python train_transition_model.py [--epochs 20] [--neg 5]

Output
------
``data/models/transition.pt`` (state-dict) + ``transition.meta.json``.
The Mixer's transition_score will pick it up automatically on the
next call.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--epochs", type=int, default=20)
    parser.add_argument("--neg", type=int, default=5,
                         help="negative samples per positive pair")
    parser.add_argument("--limit", type=int, default=None,
                         help="cap total examples (debug)")
    args = parser.parse_args()

    from app.engine import transition_model
    from app.engine.library import get_connection

    print("Extracting training pairs from track_pairs + tracks…")
    pairs = transition_model.extract_pairs(
        get_connection(), neg_per_pos=args.neg, limit=args.limit)
    if not pairs:
        print("No training data — check that you've run the scraper "
              "(Discover) AND the cooccurrence rebuild AND the "
              "embedding encoder.")
        return 1
    print(f"Got {len(pairs)} examples. Training {args.epochs} epochs…")

    ok = transition_model.train(pairs, epochs=args.epochs)
    if not ok:
        print("Training skipped (torch missing?). Install with "
              "`pip install torch` and re-run.")
        return 1
    print("Done. The Mixer will use the model on the next score call.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
