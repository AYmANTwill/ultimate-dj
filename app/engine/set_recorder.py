"""
Set Recorder (path B) — capture the STRUCTURE of a live set.

The DDJ-FLX4 hands its knob/fader stream to Rekordbox exclusively, so we
can't read the controller directly. But Rekordbox keeps its own record
of what you actually played (the history session in master.db), and we
already read that read-only for the Live mode. This recorder polls it in
real time and logs the ordered track sequence with timing — the "set
document" the player agent will later replay.

    rec = SetRecorder()
    rec.start()               # begin watching the live history session
    ...                       # you DJ your set
    doc = rec.stop()          # returns + saves the set document

Set-document schema (JSON in data/recorded_sets/)::

    {"name": "Set 2026-08-27 21:40",
     "started_at": "2026-08-27T21:40:03Z",
     "source": "rekordbox-history",
     "tracks": [
        {"pos": 1, "artist": "Yvnnis", "title": "EMOTICONE",
         "t_offset_s": 0.0, "bpm": 150.0, "key": null},
        ...
     ]}

t_offset_s = seconds from the first captured track (approximate — the
history updates with ~1 min lag, refined later via PRO DJ LINK / audio).
"""
from __future__ import annotations

import json
import re
import threading
import time

from app.config import DATA_DIR
from app.logger import log_info, log_warning

_SETS_DIR = DATA_DIR / "recorded_sets"
_POLL_S = 5.0
_EXT_RE = re.compile(r"\.(mp3|wav|flac|m4a|aac|ogg|aiff?)\s*$", re.IGNORECASE)


class SetRecorder:
    """Polls Rekordbox's live history session and records the track
    sequence with timestamps. Read-only against Rekordbox."""

    def __init__(self, poll_interval: float = _POLL_S):
        self._interval = poll_interval
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._tracks: list[dict] = []
        self._t0 = 0.0
        self._started_iso = ""
        self._error = ""
        self._history_id = None

    # ── public (any thread) ─────────────────────────────────────

    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive()
                    and not self._stop.is_set())

    def snapshot(self) -> dict:
        with self._lock:
            return {"running": self.is_running(),
                    "n_tracks": len(self._tracks),
                    "tracks": list(self._tracks),
                    "error": self._error}

    def start(self, on_track=None) -> bool:
        from app.engine import rekordbox_bridge as rb
        if not rb.is_available():
            self._error = "pyrekordbox / Rekordbox indisponible"
            log_warning("set_recorder: " + self._error)
            return False
        if self.is_running():
            return True
        self._stop.clear()
        self._tracks = []
        self._error = ""
        self._thread = threading.Thread(
            target=self._run, args=(on_track,), daemon=True,
            name="set-recorder")
        self._thread.start()
        return True

    def stop(self, save: bool = True) -> dict:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)
        doc = self._build_doc()
        if save and doc["tracks"]:
            self._save(doc)
        return doc

    # ── worker ──────────────────────────────────────────────────

    def _run(self, on_track) -> None:
        from datetime import datetime, timezone

        from app.engine import rekordbox_bridge as rb
        try:
            db = rb._open_db()
        except Exception as e:
            with self._lock:
                self._error = f"ouverture Rekordbox : {str(e)[:90]}"
            log_warning("set_recorder: " + self._error)
            return

        self._t0 = time.perf_counter()
        self._started_iso = datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
        # Anchor on the history session that is newest WHEN we start, so
        # we record THIS set, not an old one.
        try:
            hists = list(db.get_history())
            cur = max(hists, key=lambda h: str(getattr(h, "DateCreated", "")))
            self._history_id = cur.ID
        except Exception as e:
            with self._lock:
                self._error = f"lecture historique : {str(e)[:90]}"
            return
        log_info(f"set_recorder: recording history {self._history_id}")

        # Baseline: the tracks already in this session BEFORE we started
        # (an earlier set). We only want what's played from now on, so
        # seed seen_pos with them — they won't be logged.
        try:
            seen_pos: set[int] = {
                int(s.TrackNo or 0)
                for s in db.get_history_songs(HistoryID=self._history_id)}
            log_info(f"set_recorder: baseline {len(seen_pos)} morceaux "
                     "ignorés (déjà joués avant l'enregistrement)")
        except Exception:
            seen_pos = set()
        while not self._stop.is_set():
            try:
                songs = sorted(
                    db.get_history_songs(HistoryID=self._history_id),
                    key=lambda s: int(s.TrackNo or 0))
                for s in songs:
                    pos = int(s.TrackNo or 0)
                    if pos in seen_pos:
                        continue
                    seen_pos.add(pos)
                    c = s.Content
                    entry = {
                        "pos": pos,
                        "artist": (getattr(c, "ArtistName", None) or "").strip()
                                  if c else "",
                        "title": _clean(getattr(c, "Title", None) or "")
                                 if c else "",
                        "t_offset_s": round(time.perf_counter() - self._t0, 1),
                        "bpm": float(getattr(c, "BPM", 0) or 0) / 100.0
                               if c and getattr(c, "BPM", None) else None,
                        "key": None,
                    }
                    with self._lock:
                        self._tracks.append(entry)
                    log_info(f"set_recorder: +{entry['pos']} "
                             f"{entry['artist']} — {entry['title']}")
                    if on_track:
                        try:
                            on_track(entry)
                        except Exception:
                            pass
            except Exception as e:
                with self._lock:
                    self._error = str(e)[:120]
                log_warning(f"set_recorder poll: {e}")
            self._stop.wait(self._interval)

    # ── document ────────────────────────────────────────────────

    def _build_doc(self) -> dict:
        with self._lock:
            tracks = list(self._tracks)
        name = f"Set {self._started_iso[:16].replace('T', ' ')}" \
            if self._started_iso else "Set"
        return {"name": name, "started_at": self._started_iso,
                "source": "rekordbox-history", "tracks": tracks}

    def _save(self, doc: dict):
        _SETS_DIR.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^0-9A-Za-z_-]+", "-", doc["name"]).strip("-") or "set"
        p = _SETS_DIR / f"{safe}.json"
        try:
            p.write_text(json.dumps(doc, ensure_ascii=False, indent=1),
                         encoding="utf-8")
            log_info(f"set_recorder: saved {p.name} "
                     f"({len(doc['tracks'])} tracks)")
        except OSError as e:
            log_warning(f"set_recorder save {p}: {e}")


def _clean(title: str) -> str:
    return _EXT_RE.sub("", title or "").strip()
