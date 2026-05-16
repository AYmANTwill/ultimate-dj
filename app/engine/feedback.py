"""
AI Level 5 — active learning from the user's 👍 / 👎 on transitions.

Two roles in the system
-----------------------
1. **Instant override** — recorded feedback is consulted on every
   ``transition_score`` call. A 👍 adds +12 raw points to that exact
   pair, a 👎 subtracts 25 (penalty bigger so the DJ can effectively
   *ban* a transition with a single click). No re-training required.
2. **Training signal** — the same rows are folded into
   ``engine.transition_model.extract_pairs`` as high-confidence
   positives / negatives so the next re-train of the L4 Siamese model
   learns the user's personal preferences alongside the
   1001tracklists co-play data.

Storage
-------
Two layers, deliberate redundancy:

- ``transition_feedback`` SQL table, primary key (path_a, path_b),
  last write wins. Optimised for the per-call lookup in transition_score.
- ``data/feedback.jsonl`` append-only audit log. Every event captured
  with its timestamp + source so the DJ can review (and we can repair
  the table if it ever gets corrupted).

Public API
----------
    record(path_a, path_b, like, *, source="mixer")
        Persist a feedback event. ``like`` is +1, -1 or 0 (neutral
        / clear). Same pair re-rated → overwrites.
    score_modifier(path_a, path_b) -> float
        +12 if 👍, -25 if 👎, 0 if neutral / unrated.
    state(path_a, path_b) -> int     (-1 / 0 / +1)
    count() -> dict                   {"total": N, "likes": N, "dislikes": N}
    list_recent(limit=50) -> list[dict]
    iter_for_training() -> Iterator[(path_a, path_b, label_int)]
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Iterator

from app.config import DATA_DIR
from app.logger import log_warning


_LOG_FILE = DATA_DIR / "feedback.jsonl"
# Score modifiers — penalty stronger than reward so the DJ can ban a
# transition with one click, but a like just nudges it without
# overshadowing key/BPM mismatch signals
_LIKE_BONUS = 12.0
_DISLIKE_PENALTY = -25.0


# ── Schema ───────────────────────────────────────────────────────

def ensure_schema(conn: sqlite3.Connection) -> None:
    """Idempotent. Called from engine.library schema init too so the
    table is always present when transition_score looks it up."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS transition_feedback (
            path_a     TEXT NOT NULL,
            path_b     TEXT NOT NULL,
            like_score INTEGER NOT NULL,
            created_at INTEGER NOT NULL,
            source     TEXT,
            PRIMARY KEY (path_a, path_b)
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_feedback_a "
        "ON transition_feedback(path_a)")
    conn.commit()


# ── Recording ────────────────────────────────────────────────────

def _append_log(entry: dict) -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass     # log is best-effort, the SQL row is the source of truth


def record(path_a: str, path_b: str, like: int, *,
            source: str = "mixer") -> None:
    """Record a feedback event. ``like`` is +1, -1 or 0 (clear).

    Same (a,b) re-rated → overwrites. Symmetric: we also store (b,a)
    so the lookup is bidirectional without join logic on every score
    call. Re-rating a 👍 to 👎 is just a normal write.
    """
    if not path_a or not path_b or path_a == path_b:
        return
    if like not in (-1, 0, 1):
        return
    from app.engine.library import get_connection
    conn = get_connection()
    ensure_schema(conn)
    now = int(time.time())
    if like == 0:
        # Neutral / clear → drop the row entirely so subsequent
        # lookups return 0
        conn.execute(
            "DELETE FROM transition_feedback "
            "WHERE (path_a = ? AND path_b = ?) "
            "OR (path_a = ? AND path_b = ?)",
            (path_a, path_b, path_b, path_a))
    else:
        for a, b in ((path_a, path_b), (path_b, path_a)):
            conn.execute(
                "INSERT OR REPLACE INTO transition_feedback "
                "(path_a, path_b, like_score, created_at, source) "
                "VALUES (?, ?, ?, ?, ?)",
                (a, b, like, now, source))
    conn.commit()
    _append_log({
        "ts": now, "path_a": path_a, "path_b": path_b,
        "like": like, "source": source,
    })


# ── Lookup (called on every transition_score) ───────────────────

def state(path_a: str, path_b: str) -> int:
    """-1 (dislike), 0 (neutral / unrated), or +1 (like)."""
    if not path_a or not path_b or path_a == path_b:
        return 0
    try:
        from app.engine.library import get_connection
        row = get_connection().execute(
            "SELECT like_score FROM transition_feedback "
            "WHERE path_a = ? AND path_b = ?",
            (path_a, path_b)).fetchone()
    except sqlite3.OperationalError:
        return 0
    if not row:
        return 0
    try:
        return int(row[0])
    except (TypeError, ValueError):
        return 0


def score_modifier(path_a: str, path_b: str) -> float:
    """Raw score modifier to apply in transition_score::

        +12  on 👍   (encourage the choice)
        -25  on 👎   (effectively bans the transition)
          0  otherwise
    """
    s = state(path_a, path_b)
    if s > 0:
        return _LIKE_BONUS
    if s < 0:
        return _DISLIKE_PENALTY
    return 0.0


# ── Stats / listing for the UI ──────────────────────────────────

def count() -> dict:
    """Aggregate counts for the Settings status line. Counts are
    halved because every event stores both (a,b) and (b,a) — the
    user-facing number is the unique-pair count."""
    try:
        from app.engine.library import get_connection
        conn = get_connection()
        likes = int(conn.execute(
            "SELECT COUNT(*) FROM transition_feedback "
            "WHERE like_score = 1").fetchone()[0])
        dislikes = int(conn.execute(
            "SELECT COUNT(*) FROM transition_feedback "
            "WHERE like_score = -1").fetchone()[0])
    except sqlite3.OperationalError:
        likes = dislikes = 0
    return {
        "total":    (likes + dislikes) // 2,
        "likes":    likes // 2,
        "dislikes": dislikes // 2,
    }


def list_recent(limit: int = 50) -> list[dict]:
    """Recent unique pairs (one row per pair, newest first)."""
    try:
        from app.engine.library import get_connection
        rows = get_connection().execute(
            "SELECT path_a, path_b, like_score, created_at, source "
            "FROM transition_feedback "
            # Keep only one direction per unique pair
            "WHERE path_a < path_b "
            "ORDER BY created_at DESC LIMIT ?",
            (limit,)).fetchall()
    except sqlite3.OperationalError:
        return []
    return [dict(r) for r in rows]


def iter_for_training() -> Iterator[tuple[str, str, int]]:
    """Yield (path_a, path_b, label) tuples for the L4 trainer.
    label = 1 (like) or 0 (dislike). Unique pairs only — no double
    counting from the symmetric storage."""
    try:
        from app.engine.library import get_connection
        rows = get_connection().execute(
            "SELECT path_a, path_b, like_score "
            "FROM transition_feedback WHERE path_a < path_b").fetchall()
    except sqlite3.OperationalError:
        return
    for r in rows:
        label = 1 if r["like_score"] > 0 else 0
        yield (r["path_a"], r["path_b"], label)
