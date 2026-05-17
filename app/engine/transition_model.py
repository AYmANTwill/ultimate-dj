"""
AI Level 4 — custom transition model trained on real DJ pair data.

Status: skeleton + dataset extractor. Training itself is opt-in (needs
torch + a few hours CPU on a 5 k-set corpus). The inference path is
already wired to be a no-op when no model file exists, so the rest of
the app keeps working unchanged.

The model
---------
A small **Siamese** network:

    track_features  →  shared encoder  →  embedding (64-d)
                                          ↓
            (outro_emb_A) · (intro_emb_B)  →  scalar score

Trained with **contrastive loss** on pairs::

    Positives — (outro of track i, intro of track i+1) for every set
                scraped from 1001tracklists. These are real DJ
                transitions; by definition they "work".
    Negatives — (outro of A, intro of Z) where A and Z were never
                played together. ~5 negatives per positive.

Why Siamese rather than a simple MLP?
- Both inputs share the same encoder → learns a *distance metric* in
  feature space rather than a per-pair classifier
- Generalises to tracks the model never saw (just compute their
  embedding once)
- Compact (~200 k params) → trainable on CPU, deployable everywhere

Public surface
--------------
    extract_pairs(conn) -> list[tuple[fa, fb, label]]
        Read every cached tracklist, build positive + negative pairs,
        encode each track's outro + intro features (using existing
        embeddings + segmentation columns).

    train(pairs, epochs=20, out_path="data/models/transition.pt")
        Standalone training script entry — runs only if `torch` is
        installed. Saves the model state-dict + meta to disk.

    score(track_a, track_b) -> float | None
        Score one transition with the trained model. Returns None when
        no model file exists (caller should fall back to the heuristic
        + cooccurrence path).
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import numpy as np

from app.config import DATA_DIR
from app.engine.embeddings import EMBED_DIM, from_blob
from app.engine.library import get_drops
from app.logger import log_info, log_warning


_MODEL_DIR = DATA_DIR / "models"
_MODEL_PATH = _MODEL_DIR / "transition.pt"
_META_PATH = _MODEL_DIR / "transition.meta.json"

# Output dimension of the Siamese encoder. Smaller than the input
# embedding so the model is forced to compress to "what matters for
# transitions" rather than copying the input through.
_OUT_DIM = 64
# Slice context around boundary points (seconds): we want the
# audio CHARACTER right at the mix point, not the whole track.
_BOUNDARY_WINDOW_S = 12.0


# ── Feature extraction ───────────────────────────────────────────

def _track_pair_features(track_a: dict, track_b: dict
                           ) -> tuple[np.ndarray, np.ndarray] | None:
    """Build the (outro_A, intro_B) feature pair the model consumes.

    For now each side is just the track's audio embedding (the same
    one stored in `tracks.embedding`) concatenated with simple
    contextual scalars: BPM, energy, intro_end / outro_start position
    as fraction of duration, drop count.

    Future iteration could compute a separate embedding from the
    actual outro/intro slice rather than the whole-track embedding —
    requires re-encoding which we skip for v0.
    """
    emb_a = from_blob(track_a.get("embedding"))
    emb_b = from_blob(track_b.get("embedding"))
    if emb_a is None or emb_b is None:
        return None
    if emb_a.size != EMBED_DIM or emb_b.size != EMBED_DIM:
        return None

    def _ctx(t: dict, role: str) -> np.ndarray:
        dur = float(t.get("duration") or 1.0)
        ie = (t.get("intro_end") or 0.0) / dur
        os_ = (t.get("outro_start") or dur) / dur
        bpm = float(t.get("bpm") or 0) / 200.0    # normalise to ~[0,1]
        ene = float(t.get("energy") or 0) / 10.0
        n_drops = float(len(get_drops(t)))
        # 'role' bit so the encoder knows whether this is meant to be
        # an outro slice or an intro slice
        role_bit = 1.0 if role == "outro" else 0.0
        return np.array([ie, os_, bpm, ene, n_drops, role_bit],
                          dtype=np.float32)

    fa = np.concatenate([emb_a, _ctx(track_a, "outro")])
    fb = np.concatenate([emb_b, _ctx(track_b, "intro")])
    return fa, fb


def feature_dim() -> int:
    return EMBED_DIM + 6     # 6 context scalars


# ── Pair dataset extraction ──────────────────────────────────────

def extract_pairs(conn: sqlite3.Connection,
                   *, neg_per_pos: int = 5,
                   limit: int | None = None,
                   include_user_feedback: bool = True,
                   feedback_repeat: int = 3,
                   ) -> list[tuple[np.ndarray, np.ndarray, int]]:
    """Walk track_pairs (built by engine.cooccurrence) and emit a
    training set.

    Positives = pairs already in track_pairs (real-DJ co-plays).
    Negatives = randomly sampled non-pairs of the same library.
    Each example = (feat_a, feat_b, label) with label ∈ {0, 1}.

    When ``include_user_feedback`` is set (default), every 👍/👎 stored
    in ``engine.feedback`` is folded in as a high-confidence training
    example and ``feedback_repeat``-times oversampled so the user's
    personal taste is weighted heavier than the ambient 1001tracklists
    signal (a few hand-rated pairs would otherwise be drowned out by
    thousands of co-occurrence rows).
    """
    rows = conn.execute(
        "SELECT path_a, path_b FROM track_pairs LIMIT 100000"
    ).fetchall()
    if not rows:
        log_warning("transition_model.extract_pairs: track_pairs empty "
                    "— run cooccurrence.rebuild() first")

    # Index local tracks once so we can pull rows quickly
    track_rows = {
        r["path"]: dict(r) for r in conn.execute(
            "SELECT * FROM tracks "
            "WHERE embedding IS NOT NULL").fetchall()
    }
    if not track_rows:
        log_warning("extract_pairs: no encoded tracks in library")
        return []

    paths = list(track_rows.keys())
    rng = np.random.default_rng(seed=42)

    examples: list[tuple[np.ndarray, np.ndarray, int]] = []
    for r in rows:
        a, b = r["path_a"], r["path_b"]
        if a not in track_rows or b not in track_rows:
            continue
        pos = _track_pair_features(track_rows[a], track_rows[b])
        if pos is None:
            continue
        examples.append((pos[0], pos[1], 1))
        # Pull `neg_per_pos` random non-co-occurring partners for `a`
        for _ in range(neg_per_pos):
            z = paths[int(rng.integers(0, len(paths)))]
            if z == a or z == b:
                continue
            neg = _track_pair_features(track_rows[a], track_rows[z])
            if neg is None:
                continue
            examples.append((neg[0], neg[1], 0))
        if limit and len(examples) >= limit:
            break

    # ── L5 → L4 bridge: fold in explicit user feedback ────────────
    user_added = 0
    if include_user_feedback:
        try:
            from app.engine.feedback import iter_for_training
            for path_a, path_b, label in iter_for_training():
                if path_a not in track_rows or path_b not in track_rows:
                    continue
                feats = _track_pair_features(
                    track_rows[path_a], track_rows[path_b])
                if feats is None:
                    continue
                # Oversample so a handful of hand-rated pairs aren't
                # drowned by tens of thousands of cooccurrence rows
                for _ in range(max(1, feedback_repeat)):
                    examples.append((feats[0], feats[1], label))
                    user_added += 1
        except Exception as e:
            log_warning(f"extract_pairs: feedback fold-in skipped: {e}")

    log_info(
        f"extract_pairs: {len(examples)} examples "
        f"({sum(1 for e in examples if e[2] == 1)} positives, "
        f"{user_added} from user feedback)")
    return examples


# ── Training (opt-in — needs torch) ──────────────────────────────

def train(pairs: list[tuple[np.ndarray, np.ndarray, int]] | None = None,
          *, epochs: int = 20, batch_size: int = 64,
          lr: float = 1e-3) -> bool:
    """Train the Siamese model. Returns True on success.

    Skipped silently if torch isn't installed — the rest of the app
    keeps working with the heuristic + cooccurrence stack.
    """
    try:
        import torch
        from torch import nn
    except ImportError:
        log_warning("transition_model.train: torch not installed, "
                    "skipping (pip install torch to enable)")
        return False

    if pairs is None:
        from app.engine.library import get_connection
        pairs = extract_pairs(get_connection())
    if not pairs:
        log_warning("transition_model.train: no training examples")
        return False

    # ── Tiny Siamese ──
    in_dim = feature_dim()

    class Encoder(nn.Module):
        def __init__(self):
            super().__init__()
            self.net = nn.Sequential(
                nn.Linear(in_dim, 256), nn.ReLU(),
                nn.Linear(256, 128), nn.ReLU(),
                nn.Linear(128, _OUT_DIM),
            )
        def forward(self, x):
            return nn.functional.normalize(self.net(x), dim=-1)

    enc = Encoder()
    opt = torch.optim.Adam(enc.parameters(), lr=lr)

    # Stack as tensors
    A = torch.tensor(np.stack([p[0] for p in pairs]))
    B = torch.tensor(np.stack([p[1] for p in pairs]))
    Y = torch.tensor(np.array([p[2] for p in pairs], dtype=np.float32))
    n = len(pairs)

    enc.train()
    for ep in range(epochs):
        # Shuffle each epoch
        idx = torch.randperm(n)
        total_loss = 0.0
        for i in range(0, n, batch_size):
            sl = idx[i:i + batch_size]
            a = enc(A[sl])
            b = enc(B[sl])
            sim = (a * b).sum(dim=-1)            # cosine of normalised
            # BCE on the [-1,1] cosine, mapped to [0,1]
            pred = (sim + 1) / 2
            loss = nn.functional.binary_cross_entropy(
                pred, Y[sl])
            opt.zero_grad()
            loss.backward()
            opt.step()
            total_loss += float(loss) * len(sl)
        log_info(f"epoch {ep + 1}/{epochs}  loss={total_loss / n:.4f}")

    _MODEL_DIR.mkdir(parents=True, exist_ok=True)
    torch.save(enc.state_dict(), _MODEL_PATH)
    _META_PATH.write_text(json.dumps({
        "version": 1, "in_dim": in_dim, "out_dim": _OUT_DIM,
        "n_pairs": n, "epochs": epochs,
    }), encoding="utf-8")
    log_info(f"transition_model.train: saved {_MODEL_PATH}")
    return True


# ── Inference ────────────────────────────────────────────────────

_model_cache = None


def _load_model():
    global _model_cache
    if _model_cache is not None:
        return _model_cache
    if not _MODEL_PATH.exists():
        return None
    try:
        import torch
        from torch import nn
    except ImportError:
        return None
    in_dim = feature_dim()

    class Encoder(nn.Module):
        def __init__(self):
            super().__init__()
            self.net = nn.Sequential(
                nn.Linear(in_dim, 256), nn.ReLU(),
                nn.Linear(256, 128), nn.ReLU(),
                nn.Linear(128, _OUT_DIM),
            )
        def forward(self, x):
            return nn.functional.normalize(self.net(x), dim=-1)

    enc = Encoder()
    try:
        enc.load_state_dict(torch.load(_MODEL_PATH))
        enc.eval()
        _model_cache = enc
        return enc
    except Exception as e:
        log_warning(f"transition_model: failed to load {_MODEL_PATH}: {e}")
        return None


def is_ready() -> bool:
    return _MODEL_PATH.exists()


def score(track_a: dict, track_b: dict) -> float | None:
    """Score one transition with the trained model. Returns None when
    no model file exists or the inputs lack embeddings — callers fall
    back to the heuristic + cooccurrence path.

    Output is in [0, 100] for consistency with the rest of the
    transition pipeline.
    """
    enc = _load_model()
    if enc is None:
        return None
    feats = _track_pair_features(track_a, track_b)
    if feats is None:
        return None
    try:
        import torch
        with torch.no_grad():
            a = enc(torch.tensor(feats[0]).unsqueeze(0))
            b = enc(torch.tensor(feats[1]).unsqueeze(0))
            cos = float((a * b).sum())
        # cos ∈ [-1, 1] → [0, 100]
        return round(max(0.0, min(100.0, (cos + 1.0) * 50.0)), 1)
    except Exception:
        return None
