"""
SQLite library manager + Camelot wheel logic + transition scoring.

Thread model: each thread gets its own connection (kept in thread-local
storage). The DB runs in WAL mode so concurrent readers don't block on a
single writer — Library.refresh() can run while Analyze is upserting.
"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Optional

from app.config import DB_FILE, CAMELOT_MAP, CAMELOT_KEYS, load_config

AUDIO_EXTS = {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac"}


# ── Database ─────────────────────────────────────────────────────

_local = threading.local()
_init_lock = threading.Lock()
_schema_ready = False


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Idempotent schema + pragmas — only the first connection runs the
    expensive CREATE / PRAGMA work, everyone else just opens."""
    global _schema_ready
    with _init_lock:
        if _schema_ready:
            return
        # WAL: readers and writers don't block each other.
        # synchronous=NORMAL: durable enough for a music library, ~3× faster.
        # mmap: skip syscalls for small reads.
        for pragma in (
            "journal_mode = WAL",
            "synchronous = NORMAL",
            "temp_store = MEMORY",
            "mmap_size = 268435456",   # 256 MB
            "cache_size = -20000",     # 20 MB page cache
        ):
            try:
                conn.execute(f"PRAGMA {pragma}")
            except sqlite3.DatabaseError:
                pass
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tracks (
                path           TEXT PRIMARY KEY,
                title          TEXT,
                bpm            REAL,
                key            TEXT,
                camelot        TEXT,
                energy         REAL,
                duration       REAL,
                rating         INTEGER DEFAULT 0,
                genre          TEXT,
                tags           TEXT,
                cue_points     TEXT,
                key_confidence REAL,
                bpm_locked     INTEGER DEFAULT 0,
                added_at       INTEGER
            )
        """)
        # Saved setlists — slots stored as JSON [{path, score, locked}, …]
        conn.execute("""
            CREATE TABLE IF NOT EXISTS setlists (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL UNIQUE,
                created_at  INTEGER,
                updated_at  INTEGER,
                slots_json  TEXT NOT NULL
            )
        """)
        # Trash — soft-delete buffer for bulk removal. Library hides
        # rows that have a row in `trash` (matched by path), so the user
        # can Undo within 30 days. After that the trash row is purged
        # and the metadata is permanently gone (the file may also be
        # gone if the user opted in to "delete files too").
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trash (
                path         TEXT PRIMARY KEY,
                deleted_at   INTEGER NOT NULL,
                track_json   TEXT NOT NULL,
                file_deleted INTEGER DEFAULT 0
            )
        """)
        # Co-occurrence weights between local tracks, mined from real
        # DJ sets scraped via engine.tracklists. Rebuilt by
        # engine.cooccurrence.rebuild() — schema ensured there too,
        # this CREATE is just so the queries in transition_score don't
        # fail on a fresh install with no scraped data.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS track_pairs (
                path_a TEXT NOT NULL,
                path_b TEXT NOT NULL,
                weight REAL NOT NULL DEFAULT 0,
                sets   INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (path_a, path_b)
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_track_pairs_a "
            "ON track_pairs(path_a)")
        # Add columns to old DBs that pre-date the new schema
        existing_cols = {row[1] for row in conn.execute(
            "PRAGMA table_info(tracks)").fetchall()}
        for col, decl in [
            ("rating",         "INTEGER DEFAULT 0"),
            ("genre",          "TEXT"),
            ("tags",           "TEXT"),
            ("cue_points",     "TEXT"),
            ("key_confidence", "REAL"),
            ("bpm_locked",     "INTEGER DEFAULT 0"),
            ("added_at",       "INTEGER"),
            ("beat_grid",      "TEXT"),     # JSON list of beat times (s)
            # Set to 1 when the file's container is corrupt (typical:
            # WAV with ID3 prefix from the legacy write_tags bug).
            # Library shows a ⚠ badge so the user can run Repair.
            ("corrupt",        "INTEGER DEFAULT 0"),
            # Audio embedding (L2-normalised float32 vector). Used by
            # transition_score for AI-aware similarity. NULL = not yet
            # encoded; bulk_encode() in engine.embeddings populates it.
            ("embedding",      "BLOB"),
            ("embedding_backend", "TEXT"),  # 'lite'/'clap'/'panns'
            # Structure boundaries (seconds) — populated by
            # engine.segmentation.detect_structure during analyse.
            # Used by the Mixer to suggest mix points and to score
            # outro_A vs intro_B rather than whole-track audio.
            ("intro_end",      "REAL"),
            ("outro_start",    "REAL"),
            ("drops",          "TEXT"),    # JSON list of seconds
        ]:
            if col not in existing_cols:
                try:
                    conn.execute(f"ALTER TABLE tracks ADD COLUMN {col} {decl}")
                except sqlite3.DatabaseError:
                    pass
        # Backfill added_at for tracks that don't have it yet (uses 0
        # as sentinel for "unknown" — they'll sort to the bottom of
        # 'recent' lists, which is correct behaviour)
        conn.execute("UPDATE tracks SET added_at = 0 WHERE added_at IS NULL")

        # Indexes for common filters
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tracks_bpm ON tracks(bpm)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tracks_camelot ON tracks(camelot)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tracks_rating ON tracks(rating)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tracks_added_at ON tracks(added_at)")
        conn.commit()
        _schema_ready = True


def get_connection() -> sqlite3.Connection:
    """Per-thread SQLite connection. Safe to call from any thread."""
    conn = getattr(_local, "conn", None)
    if conn is not None:
        return conn

    DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        str(DB_FILE),
        check_same_thread=False,   # we manage threading ourselves
        timeout=10.0,              # wait for locks instead of failing fast
    )
    conn.row_factory = sqlite3.Row
    _ensure_schema(conn)
    _local.conn = conn
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """Apply the engine's schema to an arbitrary connection (e.g. tests).

    Unlike `_ensure_schema` this does NOT check the global flag — every
    call ensures the target connection has the full schema, idempotently.
    """
    # Run all pragmas + DDL on this conn (safe to repeat: CREATE IF NOT
    # EXISTS, ALTER ... ADD COLUMN inside try/except, etc.)
    for pragma in ("journal_mode = WAL",
                   "synchronous = NORMAL"):
        try:
            conn.execute(f"PRAGMA {pragma}")
        except sqlite3.DatabaseError:
            pass
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tracks (
            path           TEXT PRIMARY KEY,
            title          TEXT,
            bpm            REAL,
            key            TEXT,
            camelot        TEXT,
            energy         REAL,
            duration       REAL,
            rating         INTEGER DEFAULT 0,
            genre          TEXT,
            tags           TEXT,
            cue_points     TEXT,
            key_confidence REAL,
            bpm_locked     INTEGER DEFAULT 0,
            added_at       INTEGER
        )
    """)
    # Same auxiliary tables as the production schema so all_tracks()'s
    # NOT-IN-trash subquery works in tests too.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS setlists (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL UNIQUE,
            created_at  INTEGER,
            updated_at  INTEGER,
            slots_json  TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trash (
            path         TEXT PRIMARY KEY,
            deleted_at   INTEGER NOT NULL,
            track_json   TEXT NOT NULL,
            file_deleted INTEGER DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS track_pairs (
            path_a TEXT NOT NULL,
            path_b TEXT NOT NULL,
            weight REAL NOT NULL DEFAULT 0,
            sets   INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (path_a, path_b)
        )
    """)
    existing_cols = {row[1] for row in conn.execute(
        "PRAGMA table_info(tracks)").fetchall()}
    for col, decl in [
        ("rating",         "INTEGER DEFAULT 0"),
        ("genre",          "TEXT"),
        ("tags",           "TEXT"),
        ("cue_points",     "TEXT"),
        ("key_confidence", "REAL"),
        ("bpm_locked",     "INTEGER DEFAULT 0"),
        ("added_at",       "INTEGER"),
        ("beat_grid",      "TEXT"),     # JSON list of beat times in s
        ("corrupt",        "INTEGER DEFAULT 0"),
        ("embedding",      "BLOB"),
        ("embedding_backend", "TEXT"),
        ("intro_end",      "REAL"),
        ("outro_start",    "REAL"),
        ("drops",          "TEXT"),
    ]:
        if col not in existing_cols:
            try:
                conn.execute(f"ALTER TABLE tracks ADD COLUMN {col} {decl}")
            except sqlite3.DatabaseError:
                pass
    conn.commit()


def upsert_track(conn: sqlite3.Connection, info: dict):
    """Insert or update a track. Auto-stamps added_at on first insert.
    If the track has bpm_locked=1, the BPM column is NOT overwritten by
    a fresh analysis — the DJ's manual override wins."""
    import json
    import time
    payload = dict(info)
    payload.setdefault("added_at", int(time.time()))
    payload.setdefault("key_confidence", None)
    # Serialise beat_grid (list[float] → JSON string)
    beat_grid = payload.get("beat_grid")
    payload["beat_grid"] = (json.dumps(list(beat_grid))
                              if beat_grid else None)
    # Same for drops list (segmentation output)
    drops = payload.get("drops")
    payload["drops"] = (json.dumps(list(drops))
                          if drops else None)
    payload.setdefault("intro_end", None)
    payload.setdefault("outro_start", None)
    # Don't blast user-set fields — leave them NULL on insert, let the
    # UPDATE clause skip them via COALESCE
    conn.execute("""
        INSERT INTO tracks
            (path, title, bpm, key, camelot, energy, duration,
             key_confidence, added_at, beat_grid,
             intro_end, outro_start, drops)
        VALUES
            (:path, :title, :bpm, :key, :camelot, :energy, :duration,
             :key_confidence, :added_at, :beat_grid,
             :intro_end, :outro_start, :drops)
        ON CONFLICT(path) DO UPDATE SET
            title          = :title,
            bpm            = CASE WHEN tracks.bpm_locked = 1
                                  THEN tracks.bpm ELSE :bpm END,
            key            = :key,
            camelot        = :camelot,
            energy         = :energy,
            duration       = :duration,
            key_confidence = :key_confidence,
            beat_grid      = COALESCE(:beat_grid, tracks.beat_grid),
            intro_end      = COALESCE(:intro_end, tracks.intro_end),
            outro_start    = COALESCE(:outro_start, tracks.outro_start),
            drops          = COALESCE(:drops, tracks.drops)
    """, payload)
    conn.commit()


def set_structure(conn: sqlite3.Connection, path: str, *,
                   intro_end: float, outro_start: float,
                   drops: list[float] | None = None) -> None:
    """Persist segmentation output for one track."""
    import json
    drops_json = json.dumps(list(drops)) if drops else None
    conn.execute(
        "UPDATE tracks SET intro_end = ?, outro_start = ?, drops = ? "
        "WHERE path = ?",
        (float(intro_end), float(outro_start), drops_json, path))
    conn.commit()


def get_drops(track: dict) -> list[float]:
    """Decode the drops JSON column into a list of seconds."""
    raw = track.get("drops")
    if not raw:
        return []
    try:
        import json
        return [float(t) for t in json.loads(raw)]
    except Exception:
        return []


def tracks_without_structure(conn: sqlite3.Connection,
                              limit: int | None = None) -> list[dict]:
    """Tracks that haven't been segmented yet. Used by Settings'
    'Detect intros/outros' bulk button to retrofit older entries
    that pre-date the segmentation column."""
    q = ("SELECT * FROM tracks "
         "WHERE intro_end IS NULL "
         "AND COALESCE(corrupt, 0) = 0 "
         "ORDER BY added_at DESC")
    if limit is not None:
        q += f" LIMIT {int(limit)}"
    return [dict(r) for r in conn.execute(q).fetchall()]


def structure_count(conn: sqlite3.Connection) -> tuple[int, int]:
    """(segmented, total non-corrupt) for the Settings status line."""
    total = int(conn.execute(
        "SELECT COUNT(*) FROM tracks "
        "WHERE COALESCE(corrupt, 0) = 0").fetchone()[0])
    done = int(conn.execute(
        "SELECT COUNT(*) FROM tracks "
        "WHERE intro_end IS NOT NULL "
        "AND COALESCE(corrupt, 0) = 0").fetchone()[0])
    return done, total


def get_beat_grid(track: dict) -> list[float]:
    """Decode the beat_grid JSON column into a list of beat times (s)."""
    raw = track.get("beat_grid")
    if not raw:
        return []
    try:
        import json
        return [float(t) for t in json.loads(raw)]
    except Exception:
        return []


# ── DJ metadata setters (used by Library/Mixer pages) ─────────────

def set_rating(conn: sqlite3.Connection, path: str, rating: int) -> None:
    """Star rating 0-5. Used by DJs to mark A/B/C tier tracks."""
    conn.execute("UPDATE tracks SET rating = ? WHERE path = ?",
                 (max(0, min(5, int(rating))), path))
    conn.commit()


def set_genre(conn: sqlite3.Connection, path: str, genre: str) -> None:
    conn.execute("UPDATE tracks SET genre = ? WHERE path = ?",
                 ((genre or "").strip(), path))
    conn.commit()


def set_tags(conn: sqlite3.Connection, path: str, tags: list[str]) -> None:
    """Free-form tags, stored as comma-separated string."""
    s = ",".join(t.strip() for t in tags if t.strip())
    conn.execute("UPDATE tracks SET tags = ? WHERE path = ?", (s, path))
    conn.commit()


def override_bpm(conn: sqlite3.Connection, path: str, bpm: float,
                 lock: bool = True) -> None:
    """Manually set BPM. lock=True prevents future analysis overwrites."""
    conn.execute(
        "UPDATE tracks SET bpm = ?, bpm_locked = ? WHERE path = ?",
        (round(float(bpm), 1), 1 if lock else 0, path))
    conn.commit()


def set_cue_points(conn: sqlite3.Connection, path: str,
                    cues: list[dict]) -> None:
    """Save cue points as JSON. Each cue: {label: str, position: float}."""
    import json
    conn.execute("UPDATE tracks SET cue_points = ? WHERE path = ?",
                 (json.dumps(cues, ensure_ascii=False), path))
    conn.commit()


def get_cue_points(track: dict) -> list[dict]:
    """Decode the cue_points JSON column into a list of dicts."""
    raw = track.get("cue_points")
    if not raw:
        return []
    import json
    try:
        return list(json.loads(raw))
    except Exception:
        return []


def set_embedding(conn: sqlite3.Connection, path: str,
                   vec, backend: str = "lite") -> None:
    """Persist an audio embedding for a track. `vec` is a numpy float32
    array; we store the raw bytes + backend name so the next sync knows
    what produced it."""
    from app.engine.embeddings import to_blob
    blob = to_blob(vec)
    conn.execute(
        "UPDATE tracks SET embedding = ?, embedding_backend = ? "
        "WHERE path = ?", (blob, backend, path))
    conn.commit()


def get_embedding(track: dict):
    """Return the numpy embedding for `track` or None if not yet
    encoded. Cheap — no DB hit, just deserialises the BLOB."""
    from app.engine.embeddings import from_blob
    return from_blob(track.get("embedding"))


def tracks_without_embedding(conn: sqlite3.Connection,
                              limit: int | None = None) -> list[dict]:
    """Tracks that haven't been encoded yet. Used by the bulk encoder."""
    q = ("SELECT * FROM tracks "
         "WHERE (embedding IS NULL OR length(embedding) = 0) "
         "AND COALESCE(corrupt, 0) = 0 "
         "ORDER BY added_at DESC")
    if limit is not None:
        q += f" LIMIT {int(limit)}"
    rows = conn.execute(q).fetchall()
    return [dict(r) for r in rows]


def embedding_count(conn: sqlite3.Connection) -> tuple[int, int]:
    """(encoded, total non-corrupt) for progress display."""
    total = int(conn.execute(
        "SELECT COUNT(*) FROM tracks "
        "WHERE COALESCE(corrupt, 0) = 0").fetchone()[0])
    done = int(conn.execute(
        "SELECT COUNT(*) FROM tracks "
        "WHERE embedding IS NOT NULL AND length(embedding) > 0 "
        "AND COALESCE(corrupt, 0) = 0").fetchone()[0])
    return done, total


def mark_corrupt(conn: sqlite3.Connection, path: str,
                  corrupt: bool = True) -> None:
    """Flag/unflag a track as having a broken container — Library shows
    a ⚠ badge so the DJ can run the Repair tool."""
    conn.execute("UPDATE tracks SET corrupt = ? WHERE path = ?",
                 (1 if corrupt else 0, path))
    conn.commit()


def corrupt_count(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM tracks WHERE corrupt = 1").fetchone()
    return int(row[0] if row else 0)


def recent_tracks(conn: sqlite3.Connection, limit: int = 10) -> list[dict]:
    """Most recently added tracks, newest first."""
    rows = conn.execute(
        "SELECT * FROM tracks ORDER BY added_at DESC, rowid DESC LIMIT ?",
        (limit,)).fetchall()
    return [dict(r) for r in rows]


def unrated_count(conn: sqlite3.Connection) -> int:
    """Count tracks the DJ hasn't rated yet (rating = 0 or NULL)."""
    row = conn.execute(
        "SELECT COUNT(*) FROM tracks "
        "WHERE rating IS NULL OR rating = 0").fetchone()
    return int(row[0] if row else 0)


# ── Saved setlists ────────────────────────────────────────────────

def save_setlist(conn: sqlite3.Connection, name: str,
                 slots: list[tuple[dict, float, bool]]) -> int:
    """Persist a setlist by name. Overwrites if a setlist with that name
    already exists. Returns the row id.

    `slots` is the SetlistPage's internal model — list of
    (track_dict, score, locked). We store only path/score/locked so the
    track data stays normalised in the tracks table; on load we re-fetch
    the latest tracks (so renamed/edited fields show up correctly).
    """
    import json
    import time
    payload = json.dumps([
        {"path": t.get("path"),
         "score": float(score),
         "locked": bool(locked)}
        for (t, score, locked) in slots
    ], ensure_ascii=False)
    now = int(time.time())
    cur = conn.execute("SELECT id FROM setlists WHERE name = ?", (name,))
    row = cur.fetchone()
    if row:
        conn.execute(
            "UPDATE setlists SET slots_json = ?, updated_at = ? "
            "WHERE id = ?",
            (payload, now, row[0]))
        conn.commit()
        return int(row[0])
    cur = conn.execute(
        "INSERT INTO setlists (name, created_at, updated_at, slots_json) "
        "VALUES (?, ?, ?, ?)",
        (name, now, now, payload))
    conn.commit()
    return int(cur.lastrowid)


def list_setlists(conn: sqlite3.Connection) -> list[dict]:
    """Return all saved setlists with metadata (name, count, updated_at)."""
    import json
    rows = conn.execute(
        "SELECT id, name, created_at, updated_at, slots_json "
        "FROM setlists ORDER BY updated_at DESC").fetchall()
    out = []
    for r in rows:
        try:
            count = len(json.loads(r["slots_json"]))
        except Exception:
            count = 0
        out.append({
            "id": r["id"],
            "name": r["name"],
            "created_at": r["created_at"],
            "updated_at": r["updated_at"],
            "count": count,
        })
    return out


def load_setlist(conn: sqlite3.Connection,
                 name: str) -> list[tuple[dict, float, bool]]:
    """Load a setlist by name. Returns the slot list with FRESH track
    dicts pulled from the tracks table (so any rating/genre/BPM changes
    made after the setlist was saved are visible)."""
    import json
    row = conn.execute(
        "SELECT slots_json FROM setlists WHERE name = ?", (name,)).fetchone()
    if not row:
        return []
    try:
        raw_slots = json.loads(row["slots_json"])
    except Exception:
        return []
    # Re-fetch each track from the tracks table (slots only store paths)
    out: list[tuple[dict, float, bool]] = []
    for s in raw_slots:
        path = s.get("path")
        if not path:
            continue
        tr = conn.execute(
            "SELECT * FROM tracks WHERE path = ?", (path,)).fetchone()
        if tr is None:
            # Track was deleted from the library — skip rather than crash
            continue
        out.append((dict(tr), float(s.get("score", 0)),
                    bool(s.get("locked", False))))
    return out


def delete_setlist(conn: sqlite3.Connection, name: str) -> bool:
    """Drop a saved setlist. Returns True if a row was deleted."""
    cur = conn.execute("DELETE FROM setlists WHERE name = ?", (name,))
    conn.commit()
    return cur.rowcount > 0


# ── Trash / soft-delete ───────────────────────────────────────────

_TRASH_TTL_DAYS = 30


def trash_tracks(conn: sqlite3.Connection, paths: list[str],
                  *, file_deleted: bool = False) -> int:
    """Move tracks to trash (soft-delete).

    The track row is kept in `tracks` but marked-out by an entry in
    `trash`. Library hides trashed rows by default; `restore_from_trash`
    brings them back.

    Set ``file_deleted=True`` if the user also asked to remove the file
    from disk — restore can bring back the DB row but obviously not the
    file. Caller is responsible for the actual `os.remove`.
    """
    import json
    import time
    if not paths:
        return 0
    now = int(time.time())
    rows = conn.execute(
        f"SELECT * FROM tracks WHERE path IN ({','.join('?' * len(paths))})",
        paths).fetchall()
    n = 0
    for r in rows:
        d = dict(r)
        conn.execute(
            "INSERT OR REPLACE INTO trash "
            "(path, deleted_at, track_json, file_deleted) "
            "VALUES (?, ?, ?, ?)",
            (d["path"], now,
             json.dumps(d, ensure_ascii=False, default=str),
             1 if file_deleted else 0))
        n += 1
    conn.commit()
    return n


def list_trash(conn: sqlite3.Connection) -> list[dict]:
    """Trashed entries (newest first) with metadata for the UI list."""
    import json
    rows = conn.execute(
        "SELECT path, deleted_at, track_json, file_deleted "
        "FROM trash ORDER BY deleted_at DESC").fetchall()
    out = []
    for r in rows:
        try:
            track = json.loads(r["track_json"])
        except Exception:
            track = {"path": r["path"], "title": Path(r["path"]).stem}
        track["_deleted_at"] = r["deleted_at"]
        track["_file_deleted"] = bool(r["file_deleted"])
        out.append(track)
    return out


def trash_count(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM trash").fetchone()[0])


def trashed_paths(conn: sqlite3.Connection) -> set[str]:
    """Set of paths currently in trash (used to filter Library queries)."""
    return {r[0] for r in conn.execute("SELECT path FROM trash").fetchall()}


def restore_from_trash(conn: sqlite3.Connection,
                        paths: list[str]) -> int:
    """Bring tracks back from trash. Doesn't restore the file on disk —
    only the DB metadata. Returns count restored."""
    if not paths:
        return 0
    placeholders = ",".join("?" * len(paths))
    cur = conn.execute(
        f"DELETE FROM trash WHERE path IN ({placeholders})", paths)
    conn.commit()
    return cur.rowcount


def empty_trash(conn: sqlite3.Connection) -> int:
    """Permanently drop all trashed entries from BOTH tables.
    The DB rows can no longer be recovered after this."""
    paths = [r[0] for r in conn.execute("SELECT path FROM trash").fetchall()]
    if not paths:
        return 0
    placeholders = ",".join("?" * len(paths))
    conn.execute(f"DELETE FROM tracks WHERE path IN ({placeholders})", paths)
    conn.execute("DELETE FROM trash")
    conn.commit()
    return len(paths)


def purge_old_trash(conn: sqlite3.Connection,
                     ttl_days: int = _TRASH_TTL_DAYS) -> int:
    """Auto-prune entries older than `ttl_days`. Called on app startup."""
    import time
    cutoff = int(time.time() - ttl_days * 86400)
    rows = conn.execute(
        "SELECT path FROM trash WHERE deleted_at < ?",
        (cutoff,)).fetchall()
    if not rows:
        return 0
    paths = [r[0] for r in rows]
    placeholders = ",".join("?" * len(paths))
    conn.execute(f"DELETE FROM tracks WHERE path IN ({placeholders})", paths)
    conn.execute("DELETE FROM trash WHERE deleted_at < ?", (cutoff,))
    conn.commit()
    return len(paths)


def all_tracks(conn: sqlite3.Connection) -> list[dict]:
    """All non-trashed tracks. Trashed tracks are surfaced by
    `list_trash()` instead — Library's normal views skip them so the
    user can Undo a bulk delete within 30 days."""
    rows = conn.execute(
        "SELECT * FROM tracks "
        "WHERE path NOT IN (SELECT path FROM trash) "
        "ORDER BY title COLLATE NOCASE"
    ).fetchall()
    return [dict(r) for r in rows]


def search_tracks(
    conn: sqlite3.Connection,
    text: str = "",
    key: str = "",
    bpm_min: float = 0,
    bpm_max: float = 999,
    energy_min: float = 0,
    energy_max: float = 10,
) -> list[dict]:
    q = ("SELECT * FROM tracks "
         "WHERE path NOT IN (SELECT path FROM trash)")
    params: dict = {}
    if text:
        q += " AND title LIKE :text"
        params["text"] = f"%{text}%"
    if key:
        q += " AND (camelot = :key OR key LIKE :keylike)"
        params["key"] = key
        params["keylike"] = f"%{key}%"
    q += " AND bpm BETWEEN :bmin AND :bmax"
    params["bmin"] = bpm_min
    params["bmax"] = bpm_max
    q += " AND energy BETWEEN :emin AND :emax"
    params["emin"] = energy_min
    params["emax"] = energy_max
    q += " ORDER BY title COLLATE NOCASE"
    rows = conn.execute(q, params).fetchall()
    return [dict(r) for r in rows]


def delete_track(conn: sqlite3.Connection, path: str):
    conn.execute("DELETE FROM tracks WHERE path = ?", (path,))
    conn.commit()


def track_count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]


# ── Library sync ─────────────────────────────────────────────────

def scan_audio_files(folders: list[str]) -> list[Path]:
    """Return all audio files (recursively) inside the given folders."""
    found: list[Path] = []
    seen: set[str] = set()
    for root in folders:
        if not root:
            continue
        p = Path(root)
        if not p.is_dir():
            continue
        for f in p.rglob("*"):
            if f.suffix.lower() in AUDIO_EXTS:
                key = str(f.resolve())
                if key not in seen:
                    seen.add(key)
                    found.append(f)
    return found


def _normalise_title(title: str) -> str:
    """Strip leading numbering and punctuation noise for dup matching."""
    import re
    s = (title or "").lower()
    s = re.sub(r"^\d+\s*[-_.\s]+", "", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(s.split())


def find_duplicates(conn: sqlite3.Connection) -> list[list[dict]]:
    """
    Return groups of duplicate tracks.
    Two tracks are considered duplicates if their normalised title matches
    AND their BPM is within 1 unit AND their Camelot codes are equal.
    """
    rows = [dict(r) for r in conn.execute("SELECT * FROM tracks").fetchall()]
    buckets: dict[tuple, list[dict]] = {}
    for r in rows:
        key = (
            _normalise_title(r.get("title", "")),
            round(float(r.get("bpm") or 0)),
            r.get("camelot") or "",
        )
        if not key[0]:
            continue
        buckets.setdefault(key, []).append(r)

    groups = [g for g in buckets.values() if len(g) > 1]
    # Sort by group size desc, then title
    groups.sort(key=lambda g: (-len(g), g[0]["title"].lower()))
    return groups


def duplicate_count(conn: sqlite3.Connection) -> int:
    """
    Fast approximate duplicate count for the dashboard.
    Equivalent to ``sum(len(g) - 1 for g in find_duplicates())`` but pushed
    into SQLite — no row materialisation in Python, scales to 100k tracks.

    Counts tracks that share the same (rounded BPM, camelot) bucket as
    another track. The Python-side normalised-title comparison is skipped
    here for speed; the dashboard tile is a hint, not a definitive list.
    """
    row = conn.execute("""
        WITH grouped AS (
            SELECT COUNT(*) AS n
            FROM tracks
            WHERE COALESCE(camelot, '') != ''
              AND COALESCE(title, '')   != ''
            GROUP BY ROUND(COALESCE(bpm, 0)), COALESCE(camelot, '')
            HAVING COUNT(*) > 1
        )
        SELECT COALESCE(SUM(n - 1), 0) FROM grouped
    """).fetchone()
    return int(row[0] if row else 0)


def sync_library(conn: sqlite3.Connection,
                 folders: list[str] | None = None) -> dict:
    """
    Reconcile the DB against on-disk audio files.

    Returns {'new': [paths to analyse], 'orphans_removed': N, 'total': N}.
    """
    if folders is None:
        # Pick up primary + extras + download folder via the helper
        from app.config import get_music_roots
        folders = get_music_roots()

    on_disk = {str(p.resolve()): p for p in scan_audio_files(folders)}
    db_paths = {row["path"] for row in
                conn.execute("SELECT path FROM tracks").fetchall()}

    # Orphans: in DB but missing on disk
    orphans = [p for p in db_paths if p not in on_disk]
    for p in orphans:
        conn.execute("DELETE FROM tracks WHERE path = ?", (p,))
    if orphans:
        conn.commit()

    # New: on disk but absent from DB (need analysis)
    new_paths = [str(on_disk[k]) for k in on_disk if k not in db_paths]

    return {
        "new": new_paths,
        "orphans_removed": len(orphans),
        "total": len(on_disk),
    }


# ── Camelot wheel / harmonic mixing ─────────────────────────────

def compatible_camelot(code: str) -> list[str]:
    """Return Camelot codes harmonically compatible with the given code."""
    if not code or len(code) < 2:
        return []
    num = int(code[:-1])
    letter = code[-1].upper()
    compat = [code]                            # same key
    compat.append(f"{(num % 12) + 1}{letter}")  # +1 semitone
    compat.append(f"{((num - 2) % 12) + 1}{letter}")  # -1 semitone
    # Modal switch (A <-> B)
    other = "B" if letter == "A" else "A"
    compat.append(f"{num}{other}")
    return list(dict.fromkeys(compat))  # dedupe preserving order


def _key_match_score(cam_a: str, cam_b: str) -> float:
    """Graduated Camelot compatibility (0-100). Replaces the previous
    binary 0/100 — that was the root of "transitions only correlate
    by BPM": once you'd filtered the binary key match, ordering came
    down to BPM closeness only."""
    if not cam_a or not cam_b or len(cam_a) < 2 or len(cam_b) < 2:
        return 30.0    # unknown key — neutral, neither penalty nor reward
    if cam_a == cam_b:
        return 100.0
    try:
        n_a, l_a = int(cam_a[:-1]), cam_a[-1].upper()
        n_b, l_b = int(cam_b[:-1]), cam_b[-1].upper()
    except (ValueError, IndexError):
        return 30.0
    same_letter = (l_a == l_b)
    diff = abs(((n_a - n_b) + 6) % 12 - 6)   # distance on the 12-step wheel
    if same_letter:
        # Same scale (both major or both minor)
        if diff == 0:    return 100.0
        if diff == 1:    return 90.0          # ±1 semitone — energy boost mix
        if diff == 2:    return 60.0
        if diff == 3:    return 35.0          # diagonal — uncommon
        if diff == 5:    return 30.0          # subdominant
        if diff == 7:    return 25.0          # tritone away
        return 10.0
    else:
        # A↔B same number = relative major/minor swap (mood switch). DJ-classic.
        if diff == 0:    return 95.0
        # ±1 with letter swap = "energy boost" rule
        if diff == 1:    return 70.0
        if diff == 2:    return 40.0
        return 15.0


def _bpm_match_score(bpm_a: float, bpm_b: float) -> float:
    """Tighter, more realistic BPM scoring.
    - within 1% → 100 (mix without pitch change)
    - within 3% → 80  (gentle pitch — typical DJ range)
    - within 6% → 50
    - beyond     → drops fast
    Half/double-time gets 70 cap (works for some genre crossings, e.g.
    DnB ↔ house, but it's NOT the same energy as a true match)."""
    if bpm_a <= 0 or bpm_b <= 0:
        return 40.0
    ratio = abs(bpm_a - bpm_b) / bpm_a
    if ratio <= 0.01:    direct = 100.0
    elif ratio <= 0.03:  direct = 80.0 + (0.03 - ratio) * (20 / 0.02)
    elif ratio <= 0.06:  direct = 50.0 + (0.06 - ratio) * (30 / 0.03)
    else:                direct = max(0.0, 50.0 - (ratio - 0.06) * 500)
    # Half/double-time match — capped because the energy is different
    half_double = 0.0
    for mult in (0.5, 2.0):
        target = bpm_a * mult
        r = abs(target - bpm_b) / target
        if r <= 0.02:
            half_double = max(half_double, 70.0 - r * 1000)
    return max(direct, half_double)


def _energy_flow_score(e_a: float, e_b: float) -> float:
    """Reward small upward energy steps (DJ build-up), neutral on flat,
    penalise big drops. e is on a 0-10 scale."""
    if e_a is None or e_b is None:
        return 50.0
    delta = e_b - e_a
    if -0.3 <= delta <= 0.6:        return 100.0   # flat or gentle build
    if 0.6 < delta <= 1.5:          return 85.0    # noticeable build — fine
    if 1.5 < delta <= 3.0:          return 60.0    # big jump — risky
    if -1.0 <= delta < -0.3:        return 75.0    # mild drop — OK
    if -2.5 <= delta < -1.0:        return 45.0    # big drop — usually a wash
    return 20.0


def transition_score(track_a: dict, track_b: dict) -> float:
    """How well track_b follows track_a (0-100).

    Re-balanced from the old (key 50 / bpm 40 / energy 10) which had a
    binary key score and led to transitions ranked almost only by BPM.

    New weighting (sums to 100 so a perfect base match scores 100):
        Key (graduated)   40%   — was 50, dropped to make room for AI
        BPM (tighter)     30%   — was 35
        Energy flow       10%   — was 15
        Audio similarity  20%   — NEW: cosine of CLAP/lite embeddings
        Genre match       +10% bonus when same genre family
        Rating ratchet    -10% to +5% based on B's rating
        Same-artist      -8 raw points so back-to-back same artist
                         is discouraged unless every other axis matches.

    The 20% audio-similarity weight is the AI layer: it rewards tracks
    that *sound* alike even when their tags say otherwise (e.g. two
    "deep tech" cuts in different keys with very different BPMs but
    matching production density). Without computed embeddings the
    weight is silently redistributed across the heuristic axes.
    """
    key = _key_match_score(track_a.get("camelot", ""),
                           track_b.get("camelot", ""))
    bpm = _bpm_match_score(float(track_a.get("bpm") or 0),
                            float(track_b.get("bpm") or 0))
    energy = _energy_flow_score(track_a.get("energy"),
                                 track_b.get("energy"))

    # Audio similarity from embeddings — graceful degrade to 0 weight
    # if either track hasn't been encoded yet.
    emb_a = get_embedding(track_a)
    emb_b = get_embedding(track_b)
    has_emb = emb_a is not None and emb_b is not None
    audio_sim = 0.0
    if has_emb:
        from app.engine.embeddings import cosine
        # Map cosine [-1, 1] to [0, 100]; 0 sim → 50, perfect → 100,
        # opposite → 0. Most music pairs land in [0.3, 0.95].
        c = cosine(emb_a, emb_b)
        audio_sim = max(0.0, min(100.0, (c + 1.0) * 50.0))

    if has_emb:
        base = (key * 0.40
                + bpm * 0.30
                + energy * 0.10
                + audio_sim * 0.20)
    else:
        # Re-distribute the 20% audio weight proportionally over the
        # heuristic axes so un-encoded tracks aren't penalised vs
        # encoded ones — they just lose access to the AI signal.
        base = key * 0.50 + bpm * 0.35 + energy * 0.15

    # ── Co-occurrence boost (AI level 2) ──────────────────────────
    # If both tracks appear together in real DJ sets (engine.cooccurrence
    # builds the matrix from 1001tracklists scrapes), add up to +15
    # raw points. Capped so a hot pair can't drown out a bad key match.
    # Silently 0 when no scrape data is available — graceful degrade.
    try:
        from app.engine.cooccurrence import cooccurrence_score
        cooc = cooccurrence_score(_thread_conn(), track_a.get("path", ""),
                                    track_b.get("path", ""))
    except Exception:
        cooc = 0.0
    coop_bonus = min(15.0, cooc * 0.15)   # cooc is already 0-100

    # Genre family bonus — substring match keeps it loose enough that
    # "tech house" + "house" or "afro tech" + "afro house" still lift.
    g_a = (track_a.get("genre") or "").lower().strip()
    g_b = (track_b.get("genre") or "").lower().strip()
    genre_bonus = 0.0
    if g_a and g_b:
        if g_a == g_b or g_a in g_b or g_b in g_a:
            genre_bonus = 10.0
        else:
            # Common family roots — split on whitespace and intersect
            tokens_a = set(g_a.split())
            tokens_b = set(g_b.split())
            if tokens_a & tokens_b:
                genre_bonus = 5.0

    # Rating modifier — DJs avoid going from a peak track (5★) to a
    # filler (1★). Reward going UP or staying, penalise going down.
    r_a = int(track_a.get("rating") or 0)
    r_b = int(track_b.get("rating") or 0)
    rating_mod = 0.0
    if r_a > 0 and r_b > 0:
        if r_b >= r_a:
            rating_mod = 5.0
        elif r_b == r_a - 1:
            rating_mod = -3.0
        else:
            rating_mod = -10.0

    # Same-artist back-to-back — small penalty unless other axes match
    artist_a = _artist_from_title(track_a.get("title") or "")
    artist_b = _artist_from_title(track_b.get("title") or "")
    same_artist_pen = -8.0 if artist_a and artist_a == artist_b else 0.0

    # ── L4: trained Siamese model (opt-in) ────────────────────────
    # If the user trained engine.transition_model.train() at least
    # once, the saved model bumps the final score by up to ±10 raw
    # points based on its learned outro→intro cosine. The model file
    # may not exist (most users) — score() returns None and we skip.
    try:
        from app.engine.transition_model import score as _model_score
        m = _model_score(track_a, track_b)
    except Exception:
        m = None
    model_bonus = 0.0
    if m is not None:
        # Map model output [0, 100] → [-10, +10]
        model_bonus = (m - 50.0) * 0.20

    score = (base + genre_bonus + rating_mod
              + same_artist_pen + coop_bonus + model_bonus)
    return round(max(0.0, min(100.0, score)), 1)


def transition_score_breakdown(track_a: dict, track_b: dict) -> dict:
    """Same calculation as transition_score, but returns the per-axis
    breakdown so the UI can explain WHY a transition scored what it
    scored. Designed to be displayed as a tooltip / details popup.

    Returns::

        {
          "key":          {"score": 0-100, "weight": 0.40-0.50, "label": "8A → 9A"},
          "bpm":          {"score": 0-100, "weight": 0.30-0.35, "label": "124→126 (+1.6%)"},
          "energy":       {"score": 0-100, "weight": 0.10-0.15, "label": "6.0→6.5 (build)"},
          "audio":        {"score": 0-100, "weight": 0.0-0.20,  "label": "cosine 0.84"},
          "genre_bonus":  0|5|10,
          "rating_mod":   -10..+5,
          "same_artist":  -8|0,
          "cooc_bonus":   0..15,
          "total":        0-100,
        }
    """
    key = _key_match_score(track_a.get("camelot", ""),
                           track_b.get("camelot", ""))
    bpm = _bpm_match_score(float(track_a.get("bpm") or 0),
                            float(track_b.get("bpm") or 0))
    energy = _energy_flow_score(track_a.get("energy"),
                                 track_b.get("energy"))
    emb_a = get_embedding(track_a)
    emb_b = get_embedding(track_b)
    has_emb = emb_a is not None and emb_b is not None
    if has_emb:
        from app.engine.embeddings import cosine
        c = cosine(emb_a, emb_b)
        audio_sim = max(0.0, min(100.0, (c + 1.0) * 50.0))
        audio_label = f"cosine {c:.2f}"
        weights = {"key": 0.40, "bpm": 0.30, "energy": 0.10, "audio": 0.20}
    else:
        audio_sim = 0.0
        audio_label = "(track non encodée)"
        weights = {"key": 0.50, "bpm": 0.35, "energy": 0.15, "audio": 0.0}

    try:
        from app.engine.cooccurrence import cooccurrence_score
        cooc = cooccurrence_score(_thread_conn(),
                                    track_a.get("path", ""),
                                    track_b.get("path", ""))
    except Exception:
        cooc = 0.0
    coop_bonus = round(min(15.0, cooc * 0.15), 1)

    g_a = (track_a.get("genre") or "").lower().strip()
    g_b = (track_b.get("genre") or "").lower().strip()
    genre_bonus = 0.0
    if g_a and g_b:
        if g_a == g_b or g_a in g_b or g_b in g_a:
            genre_bonus = 10.0
        elif set(g_a.split()) & set(g_b.split()):
            genre_bonus = 5.0

    r_a = int(track_a.get("rating") or 0)
    r_b = int(track_b.get("rating") or 0)
    rating_mod = 0.0
    if r_a > 0 and r_b > 0:
        if r_b >= r_a:
            rating_mod = 5.0
        elif r_b == r_a - 1:
            rating_mod = -3.0
        else:
            rating_mod = -10.0

    artist_a = _artist_from_title(track_a.get("title") or "")
    artist_b = _artist_from_title(track_b.get("title") or "")
    same_artist = -8.0 if artist_a and artist_a == artist_b else 0.0

    return {
        "key": {
            "score": round(key, 1), "weight": weights["key"],
            "label": f"{track_a.get('camelot', '?')} → "
                     f"{track_b.get('camelot', '?')}",
        },
        "bpm": {
            "score": round(bpm, 1), "weight": weights["bpm"],
            "label": f"{(track_a.get('bpm') or 0):.0f} → "
                     f"{(track_b.get('bpm') or 0):.0f}",
        },
        "energy": {
            "score": round(energy, 1), "weight": weights["energy"],
            "label": f"{(track_a.get('energy') or 0):.1f} → "
                     f"{(track_b.get('energy') or 0):.1f}",
        },
        "audio": {
            "score": round(audio_sim, 1), "weight": weights["audio"],
            "label": audio_label,
        },
        "genre_bonus":  genre_bonus,
        "rating_mod":   rating_mod,
        "same_artist":  same_artist,
        "cooc_bonus":   coop_bonus,
        "total":        transition_score(track_a, track_b),
    }


def _thread_conn():
    """Helper for transition_score's cooccurrence lookup — uses the
    thread-local DB conn so we don't pay a fresh connect per scoring
    call. Falls back to a fresh connect if the thread doesn't have one
    cached yet."""
    return get_connection()


def _artist_from_title(title: str) -> str:
    """Heuristic artist extraction from a title string formatted like
    'Artist - Track'. Returns lowercased artist or '' if no separator."""
    if " - " in title:
        return title.split(" - ", 1)[0].strip().lower()
    return ""


def find_transitions(
    conn: sqlite3.Connection,
    track: dict,
    limit: int = 10,
) -> list[tuple[dict, float]]:
    """Find best transitions from the given track."""
    candidates = all_tracks(conn)
    scored = []
    for c in candidates:
        if c["path"] == track["path"]:
            continue
        s = transition_score(track, c)
        scored.append((c, s))
    scored.sort(key=lambda x: -x[1])
    return scored[:limit]


def build_setlist_auto(
    conn: sqlite3.Connection,
    start_track: dict,
    length: int = 10,
) -> list[tuple[dict, float]]:
    """Auto-build a setlist starting from a track, greedily picking best transitions."""
    setlist = [(start_track, 100.0)]
    used = {start_track["path"]}
    current = start_track

    for _ in range(length - 1):
        candidates = all_tracks(conn)
        best, best_score = None, -1
        for c in candidates:
            if c["path"] in used:
                continue
            s = transition_score(current, c)
            if s > best_score:
                best, best_score = c, s
        if best is None:
            break
        setlist.append((best, best_score))
        used.add(best["path"])
        current = best

    return setlist
