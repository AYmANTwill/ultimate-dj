"""
Smoke tests for Ultimate DJ.
Run from project root:
    python -m tests.test_smoke
Tests don't touch the network and don't require ffmpeg/Node.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def check(name: str, fn):
    try:
        fn()
        PASSED.append(name)
        print(f"  OK   {name}")
    except Exception as e:
        FAILED.append((name, f"{e.__class__.__name__}: {e}"))
        print(f"  FAIL {name}  ->  {e}")
        traceback.print_exc()


# ── Tests ───────────────────────────────────────────────────────

def t_imports():
    import app.config
    import app.engine.library
    import app.engine.analyzer
    import app.engine.discovery
    import app.engine.spotify
    import app.engine.downloader
    # UI imports require customtkinter
    try:
        import customtkinter  # noqa
        import app.ui.app  # noqa
        import app.ui.settings  # noqa
        import app.ui.library  # noqa
    except ImportError:
        pass  # OK: UI deps may not be installed in CI


def t_themes_present():
    from app.config import THEMES, COLORS, apply_theme
    assert "Cyan Night" in THEMES
    assert "Mono" in THEMES
    assert {"bg_dark", "accent", "text"} <= set(THEMES["Cyan Night"].keys())
    apply_theme("Mono")
    assert COLORS["accent"] == THEMES["Mono"]["accent"]
    apply_theme("Cyan Night")  # reset


def t_camelot_compat():
    from app.engine.library import compatible_camelot
    out = compatible_camelot("8A")
    # Should include same key, +1 (9A), -1 (7A), and 8B (mood switch)
    assert "8A" in out
    assert "9A" in out
    assert "7A" in out
    assert "8B" in out
    # Wrap-around
    out12 = compatible_camelot("12A")
    assert "1A" in out12   # +1 wraps to 1
    assert "11A" in out12  # -1


def t_transition_score():
    from app.engine.library import transition_score
    a = {"camelot": "8A", "bpm": 124, "energy": 6}
    b = {"camelot": "9A", "bpm": 124, "energy": 6}  # +1, perfect harmonic
    c = {"camelot": "3B", "bpm": 88,  "energy": 1}  # incompatible
    sa = transition_score(a, b)
    sc = transition_score(a, c)
    assert sa > sc, f"expected sa({sa}) > sc({sc})"
    assert sa >= 90, f"expected near-perfect, got {sa}"


def t_db_roundtrip():
    """Use a temp DB to check upsert / read paths."""
    from app.engine import library

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        # Use the engine's schema so new columns (rating, bpm_locked, …)
        # match what upsert_track expects.
        library.init_schema(conn)
        info = {"path": "/x.mp3", "title": "X", "bpm": 124, "key": "A minor",
                "camelot": "8A", "energy": 6.0, "duration": 200.0}
        library.upsert_track(conn, info)
        assert library.track_count(conn) == 1
        assert library.all_tracks(conn)[0]["camelot"] == "8A"

        # Setter round-trips
        library.set_rating(conn, "/x.mp3", 4)
        assert library.all_tracks(conn)[0]["rating"] == 4

        library.override_bpm(conn, "/x.mp3", 174.0, lock=True)
        row = library.all_tracks(conn)[0]
        assert abs(row["bpm"] - 174.0) < 0.05
        assert row["bpm_locked"] == 1
        # Re-running upsert with the old BPM must NOT overwrite
        library.upsert_track(conn, info)
        row = library.all_tracks(conn)[0]
        assert abs(row["bpm"] - 174.0) < 0.05, "bpm_locked failed to protect override"
        conn.close()


def t_sync_library_orphan_detection():
    """Sync should detect entries whose file is missing."""
    from app.engine import library

    with tempfile.TemporaryDirectory() as tmp:
        tmp_p = Path(tmp)
        # one real file, one missing
        real = tmp_p / "song.mp3"
        real.write_bytes(b"")
        missing = str(tmp_p / "ghost.mp3")

        db_path = tmp_p / "lib.db"
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        library.init_schema(conn)
        for p in (str(real.resolve()), missing):
            conn.execute(
                "INSERT INTO tracks (path, title, bpm, key, camelot, "
                "energy, duration) VALUES (?, 'T', 120, 'C major', '8B', 5, 200)",
                (p,))
        conn.commit()

        result = library.sync_library(conn, [str(tmp_p)])
        assert result["orphans_removed"] == 1, result
        assert result["total"] >= 1
        conn.close()


def t_taste_profile_io():
    from app.engine import discovery
    t = discovery._load_taste()
    assert "liked_artists" in t
    assert "liked_bpm_range" in t


def t_find_duplicates():
    """Two tracks with same normalised title + same BPM/cam = duplicates."""
    from app.engine import library

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "dup.db"
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        library.init_schema(conn)
        rows = [
            ("/a/Song.mp3",      "Song",       124, "G minor", "6A", 8, 200),
            ("/b/01 - Song.mp3", "01 - Song",  124, "G minor", "6A", 8, 200),
            ("/c/Other.mp3",     "Other",      128, "C major", "8B", 7, 180),
        ]
        for r in rows:
            conn.execute(
                "INSERT INTO tracks (path, title, bpm, key, camelot, "
                "energy, duration) VALUES (?,?,?,?,?,?,?)", r)
        conn.commit()

        groups = library.find_duplicates(conn)
        assert len(groups) == 1, f"expected 1 group, got {len(groups)}"
        assert len(groups[0]) == 2
        # SQL-side count should match
        assert library.duplicate_count(conn) == 1
        conn.close()


def t_camelot_keys_inverse():
    from app.config import CAMELOT_MAP, CAMELOT_KEYS
    for key, code in CAMELOT_MAP.items():
        assert CAMELOT_KEYS.get(code), code


def t_export_formats():
    """M3U8 / Rekordbox XML / Serato crate exporters produce valid output."""
    from app.engine import export

    tracks = [
        {"path": "/m/song1.mp3", "title": "Song 1",
         "bpm": 124, "key": "C major", "camelot": "8B",
         "energy": 5, "duration": 240, "rating": 4, "genre": "tech house"},
        {"path": "/m/song2.mp3", "title": "Song 2",
         "bpm": 126, "key": "G major", "camelot": "9B",
         "energy": 6, "duration": 220, "rating": 0},
    ]
    with tempfile.TemporaryDirectory() as tmp:
        m3u = Path(tmp) / "p.m3u8"
        export.export_m3u8(tracks, m3u, "Test")
        assert m3u.exists() and "EXTM3U" in m3u.read_text(encoding="utf-8")

        xml = Path(tmp) / "rb.xml"
        export.export_rekordbox_xml(tracks, xml, "Test")
        text = xml.read_text(encoding="utf-8")
        assert "DJ_PLAYLISTS" in text and "AverageBpm" in text
        # 4★ = rating 204 in Rekordbox
        assert 'Rating="204"' in text

        crate = Path(tmp) / "test.crate"
        export.export_serato_crate(tracks, crate)
        data = crate.read_bytes()
        assert data.startswith(b"vrsn") and b"otrk" in data


def t_track_metadata_helpers():
    """rating / genre / tags / cue points round-trip through the DB."""
    from app.engine import library

    with tempfile.TemporaryDirectory() as tmp:
        conn = sqlite3.connect(str(Path(tmp) / "meta.db"))
        conn.row_factory = sqlite3.Row
        library.init_schema(conn)
        info = {"path": "/m/x.mp3", "title": "X", "bpm": 124,
                 "key": "C major", "camelot": "8B", "energy": 5, "duration": 200}
        library.upsert_track(conn, info)

        library.set_rating(conn, "/m/x.mp3", 5)
        library.set_genre(conn, "/m/x.mp3", "techno")
        library.set_tags(conn, "/m/x.mp3", ["dark", "peak time"])
        library.set_cue_points(conn, "/m/x.mp3",
                                [{"label": "DROP", "position": 64.5}])

        row = library.all_tracks(conn)[0]
        assert row["rating"] == 5
        assert row["genre"] == "techno"
        assert "dark" in (row["tags"] or "")
        cues = library.get_cue_points(row)
        assert cues and cues[0]["label"] == "DROP"

        # recent_tracks should include this one
        assert library.recent_tracks(conn)[0]["path"] == "/m/x.mp3"
        # unrated_count = 0 (we just rated it)
        assert library.unrated_count(conn) == 0
        conn.close()


# ── Run ─────────────────────────────────────────────────────────

def main() -> int:
    print("\nUltimate DJ — smoke tests\n" + "-" * 40)
    check("imports load",                t_imports)
    check("themes table + apply_theme",  t_themes_present)
    check("camelot compat wheel",        t_camelot_compat)
    check("transition score ordering",   t_transition_score)
    check("DB upsert / read / lock",     t_db_roundtrip)
    check("sync_library orphan removal", t_sync_library_orphan_detection)
    check("taste profile loads",         t_taste_profile_io)
    check("find_duplicates groups",      t_find_duplicates)
    check("camelot map invertible",      t_camelot_keys_inverse)
    check("export m3u8/rekordbox/serato", t_export_formats)
    check("rating/genre/tags/cues",      t_track_metadata_helpers)

    print("-" * 40)
    print(f"  {len(PASSED)} passed, {len(FAILED)} failed")
    for name, err in FAILED:
        print(f"   FAIL {name}: {err}")
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
