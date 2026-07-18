"""
Live mode engine — detect what Rekordbox is playing and suggest what
to play next, in real time.

How detection works (source B, works with Rekordbox alone on a PC):
Rekordbox appends each played track to today's HISTORY session in
master.db roughly a minute after it starts playing. A daemon thread
polls that session every few seconds through rekordbox_bridge
(READ-ONLY); a new row = "now playing" event.

Suggestions are set-aware: candidates are the user's library minus
everything already played this session, ranked by
library.transition_score against the current track — which already
blends key/BPM/energy, the calibrated CLAP audio axis and the
co-occurrence bonus (now fed by the user's own imported sets).

The UI never touches threads: it calls session.snapshot() from the
Tk mainloop and renders whatever state is there.
"""
from __future__ import annotations

import threading

from app.logger import log_info, log_warning

_POLL_INTERVAL_S = 5.0
_SUGGESTION_LIMIT = 10


class LiveSession:
    """One live set. start() spawns the poller, stop() ends it."""

    def __init__(self, poll_interval: float = _POLL_INTERVAL_S):
        self._interval = poll_interval
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._state: dict = {
            "active": False,
            "history": "",
            "played": [],        # [{title, matched}]
            "current": None,     # title of last matched track
            "suggestions": [],   # [{title, path, score, bpm, camelot}]
            "error": "",
        }

    # ── Public API (any thread) ─────────────────────────────────

    def snapshot(self) -> dict:
        with self._lock:
            s = dict(self._state)
            s["played"] = list(self._state["played"])
            s["suggestions"] = list(self._state["suggestions"])
            return s

    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive()
                    and not self._stop.is_set())

    def start(self) -> None:
        if self.is_running():
            return
        self._stop.clear()
        with self._lock:
            self._state["error"] = ""
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="live-poller")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            self._state["active"] = False

    # ── Poller internals (worker thread only) ───────────────────

    def _run(self) -> None:
        try:
            from app.engine import rekordbox_bridge as rb
            if not rb.is_available():
                with self._lock:
                    self._state["error"] = ("pyrekordbox ou Rekordbox "
                                            "indisponible")
                return
            db = rb._open_db()
        except Exception as e:
            log_warning(f"live: ouverture Rekordbox impossible: {e}")
            with self._lock:
                self._state["error"] = f"ouverture Rekordbox : {str(e)[:90]}"
            return

        # Library index built in THIS thread (sqlite conns are
        # thread-local in engine.library).
        from app.engine.library import get_connection
        conn = get_connection()
        lib = {rb._norm_path(r["path"]): dict(r) for r in conn.execute(
            "SELECT * FROM tracks "
            "WHERE COALESCE(source, 'user') = 'user'").fetchall()}

        with self._lock:
            self._state["active"] = True
        log_info("live: session démarrée — en attente de Rekordbox")

        last_hid, last_count = None, -1
        while not self._stop.is_set():
            try:
                hists = list(db.get_history())
                if hists:
                    h = max(hists,
                            key=lambda x: str(getattr(x, "DateCreated", "")))
                    songs = sorted(
                        db.get_history_songs(HistoryID=h.ID),
                        key=lambda s: int(s.TrackNo or 0))
                    if h.ID != last_hid or len(songs) != last_count:
                        last_hid, last_count = h.ID, len(songs)
                        self._on_history(h, songs, lib)
            except Exception as e:
                log_warning(f"live poller: {e}")
                with self._lock:
                    self._state["error"] = str(e)[:120]
            self._stop.wait(self._interval)

    def _on_history(self, h, songs, lib: dict) -> None:
        from app.engine import rekordbox_bridge as rb
        played = []
        for s in songs:
            c = s.Content
            if c is None:
                continue
            folder = getattr(c, "FolderPath", "") or ""
            row = lib.get(rb._norm_path(folder)) if folder else None
            title = ((row or {}).get("title")
                     or rb._clean_title(getattr(c, "Title", "") or ""))
            played.append({"title": title, "row": row})

        current = next((p for p in reversed(played) if p["row"]), None)
        suggestions = (self._suggest(current["row"], played, lib)
                       if current else [])
        with self._lock:
            st = self._state
            st["history"] = str(getattr(h, "Name", "") or "")
            st["played"] = [{"title": p["title"],
                             "matched": p["row"] is not None}
                            for p in played]
            st["current"] = (current["title"] if current
                             else (played[-1]["title"] if played else None))
            st["suggestions"] = suggestions
        log_info(f"live: {len(played)} joués — now playing: "
                 f"{self._state['current']}")

    def _suggest(self, cur_row: dict, played: list, lib: dict,
                 limit: int = _SUGGESTION_LIMIT) -> list[dict]:
        from app.engine.library import transition_score
        played_paths = {p["row"]["path"] for p in played if p["row"]}
        scored = []
        for row in lib.values():
            if row["path"] in played_paths:
                continue
            try:
                sc = transition_score(cur_row, row)
            except Exception:
                continue
            scored.append((sc, row))
        scored.sort(key=lambda x: -x[0])
        return [{"title": r.get("title") or r["path"],
                 "path": r["path"],
                 "score": round(s, 1),
                 "bpm": r.get("bpm") or 0,
                 "camelot": r.get("camelot") or "?"}
                for s, r in scored[:limit]]
