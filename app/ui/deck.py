"""
Deck widget — playback controls + waveform + cue points.

Embeddable inside a Toplevel (TrackEditor) or any frame. Drives a single
deck of `engine.player`. The deck identity ("A" / "B") is passed in so
the same widget can be used for crossfade preview between two tracks.

Visual structure:
    [waveform strip with playhead + cue markers]
    [time ─────●───── total]
    [▶ ⏸ ⏹]   [vol slider]   [+ cue]
    [cue chips: 1 INTRO  2 DROP  3 OUTRO …  (click to jump, right-click to delete)]
"""
from __future__ import annotations

import threading
import tkinter as tk
from typing import Callable

import customtkinter as ctk
import numpy as np

from app.config import COLORS
from app.engine import player
from app.engine.library import get_cue_points, get_beat_grid, get_drops
from app.ui.helpers import font


_WF_HEIGHT = 80


class DeckWidget(ctk.CTkFrame):
    def __init__(self, parent, *, deck: str = "A",
                 on_cues_changed: Callable[[list[dict]], None] | None = None):
        super().__init__(parent, fg_color=COLORS["bg_card"], corner_radius=10)
        self._deck = deck
        self._track_path: str = ""
        self._waveform: np.ndarray = np.zeros(1, dtype=np.float32)
        self._cues: list[dict] = []
        self._beats: list[float] = []      # beat times in seconds
        self._intro_end: float | None = None
        self._outro_start: float | None = None
        self._drops: list[float] = []
        self._on_cues_changed = on_cues_changed
        self._tick_job: str | None = None
        self._build()

    # ── UI ────────────────────────────────────────────────────────

    def _build(self):
        # Waveform canvas — uses raw tk.Canvas for speed
        self.canvas = tk.Canvas(
            self, height=_WF_HEIGHT, bg=COLORS["bg_input"],
            highlightthickness=0, bd=0, cursor="crosshair")
        self.canvas.pack(fill="x", padx=8, pady=(8, 4))
        # Click on the waveform → focus this deck (for shortcuts) AND
        # seek to the click position. One handler does both.
        def _click(e):
            self.canvas.focus_set()
            self._on_canvas_click(e)
        self.canvas.bind("<Button-1>", _click)
        self.canvas.bind("<Configure>", lambda e: self._redraw_waveform())

        # Pro-DJ keyboard shortcuts. Bound on the canvas so they only
        # fire when this deck has focus — two decks in the same Mixer
        # don't fight each other.
        #   1-8       → jump to cue points 1..8 in order
        #   space     → toggle Play / Pause
        #   Home / 0  → seek to start
        self.canvas.configure(takefocus=True, highlightthickness=0)
        self.canvas.bind("<KeyPress>", self._on_key)

        # Time row
        time_row = ctk.CTkFrame(self, fg_color="transparent")
        time_row.pack(fill="x", padx=8, pady=(0, 4))
        self.pos_label = ctk.CTkLabel(
            time_row, text="0:00", font=font(11, "bold"),
            text_color=COLORS["accent"], width=50)
        self.pos_label.pack(side="left")
        self.dur_label = ctk.CTkLabel(
            time_row, text="0:00", font=font(11),
            text_color=COLORS["text_dim"], width=50)
        self.dur_label.pack(side="right")

        # Transport row
        ctrl = ctk.CTkFrame(self, fg_color="transparent")
        ctrl.pack(fill="x", padx=8, pady=(0, 4))

        self.play_btn = ctk.CTkButton(
            ctrl, text="▶  Play", width=90, height=32,
            font=font(13, "bold"),
            fg_color=COLORS["success"], hover_color="#00a050",
            text_color=COLORS["bg_dark"],
            command=self._toggle_play)
        self.play_btn.pack(side="left", padx=2)

        ctk.CTkButton(
            ctrl, text="⏹", width=40, height=32,
            font=font(14),
            fg_color=COLORS["bg_input"], hover_color=COLORS["error"],
            text_color=COLORS["text"],
            command=self._stop).pack(side="left", padx=2)

        # Volume slider
        self.vol_var = ctk.DoubleVar(value=0.85)
        ctk.CTkLabel(ctrl, text="Vol", font=font(10),
                     text_color=COLORS["text_dim"]
                     ).pack(side="left", padx=(12, 4))
        self.vol_slider = ctk.CTkSlider(
            ctrl, from_=0, to=1, variable=self.vol_var,
            width=100, height=14,
            command=lambda v: player.set_volume(self._deck, float(v)))
        self.vol_slider.pack(side="left", padx=2)

        # Add-cue button
        ctk.CTkButton(
            ctrl, text="＋ cue", width=70, height=32,
            font=font(11),
            fg_color=COLORS["accent2"], hover_color="#e0356f",
            text_color=COLORS["on_accent2"],
            command=self._add_cue).pack(side="right", padx=2)

        # Cue chip strip
        self.cue_strip = ctk.CTkFrame(self, fg_color="transparent")
        self.cue_strip.pack(fill="x", padx=8, pady=(2, 8))

    # ── Loading ──────────────────────────────────────────────────

    def load_track(self, track: dict) -> None:
        """Load a new track. Stops any current playback first."""
        self._stop()
        self._track_path = track["path"]
        self._cues = list(get_cue_points(track))
        self._beats = list(get_beat_grid(track))   # detected beat times (s)
        # Structure boundaries from engine.segmentation. None = not yet
        # segmented (we just don't draw the markers in that case).
        ie = track.get("intro_end")
        os_ = track.get("outro_start")
        self._intro_end = float(ie) if ie is not None else None
        self._outro_start = float(os_) if os_ is not None else None
        self._drops = list(get_drops(track))
        self._waveform = np.zeros(1, dtype=np.float32)
        self.dur_label.configure(text="…")
        self.pos_label.configure(text="0:00")
        self._redraw_waveform()
        self._render_cue_chips()
        # Compute waveform off-thread so the popup pops instantly
        threading.Thread(
            target=self._wave_thread, args=(self._track_path,),
            daemon=True).start()

    def _wave_thread(self, path: str):
        wf = player.waveform(path)
        # Make sure the user hasn't switched track in the meantime
        if self._track_path == path:
            self.after(0, lambda w=wf: self._on_wave_ready(w))

    def _on_wave_ready(self, wf: np.ndarray):
        self._waveform = wf
        self._redraw_waveform()

    # ── Transport ────────────────────────────────────────────────

    def _toggle_play(self):
        if not self._track_path:
            return
        if player.is_playing(self._deck):
            player.pause(self._deck)
            self.play_btn.configure(text="▶  Play",
                                     fg_color=COLORS["success"])
        elif player.current_path(self._deck) == self._track_path:
            player.resume(self._deck)
            self.play_btn.configure(text="⏸  Pause",
                                     fg_color=COLORS["warning"])
            self._start_tick()
        else:
            ok = player.play(self._deck, self._track_path)
            if ok:
                player.set_volume(self._deck, float(self.vol_var.get()))
                dur = player.duration(self._deck)
                self.dur_label.configure(text=_fmt(dur))
                self.play_btn.configure(text="⏸  Pause",
                                         fg_color=COLORS["warning"])
                self._start_tick()

    def _stop(self):
        player.stop(self._deck)
        self.play_btn.configure(text="▶  Play", fg_color=COLORS["success"])
        self.pos_label.configure(text="0:00")
        if self._tick_job:
            try:
                self.after_cancel(self._tick_job)
            except Exception:
                pass
            self._tick_job = None
        self._redraw_waveform()  # clear playhead

    def _start_tick(self):
        if self._tick_job:
            try:
                self.after_cancel(self._tick_job)
            except Exception:
                pass
        self._tick()

    def _tick(self):
        pos = player.position(self._deck)
        dur = player.duration(self._deck)
        if dur > 0 and pos >= dur:
            self._stop()
            return
        self.pos_label.configure(text=_fmt(pos))
        # Cheap: only the playhead (1 line). Static + cues stay as-is.
        self._draw_playhead()
        # 50ms ≈ 20fps — smooth playhead without burning CPU
        self._tick_job = self.after(50, self._tick)

    # ── Waveform painting ────────────────────────────────────────
    # Three-layer model:
    #   "static"   — the waveform peaks themselves (only redrawn when
    #                the track changes or the canvas is resized)
    #   "cue"      — cue markers (only redrawn on cue add/remove)
    #   "playhead" — the moving cursor (redrawn every tick @ 50ms)
    #
    # Before this split, _tick redrew all 600 lines of the waveform 20×
    # per second on the UI thread, which made the panel chunky. Now the
    # tick only deletes + redraws ONE line (~1ms work) and the heavy
    # static layer is painted once.

    def _redraw_waveform(self):
        """Full repaint — call on track change, resize, or theme reload."""
        self.canvas.delete("all")
        self._draw_static()
        self._draw_beat_grid()
        self._draw_structure()
        self._draw_cues()
        self._draw_playhead()

    def _draw_static(self):
        c = self.canvas
        c.delete("static")
        w = max(1, c.winfo_width())
        h = _WF_HEIGHT
        wf = self._waveform
        if wf.size <= 1:
            c.create_text(w / 2, h / 2,
                          text="(chargement de la waveform…)",
                          fill=COLORS["text_dim"],
                          font=("Segoe UI", 10),
                          tags=("static",))
            return
        mid = h / 2
        n = wf.size
        scale = h * 0.45
        accent = COLORS["accent"]
        # Use coords list + create_line in batch — way faster than 600
        # individual create_line calls because Tk only marshals once.
        for i in range(0, w, 2):
            idx = int(i / w * n)
            v = float(wf[idx]) * scale
            c.create_line(i, mid - v, i, mid + v,
                          fill=accent, width=1, tags=("static",))

    def _draw_beat_grid(self):
        """Subtle vertical ticks at every beat, taller at downbeats
        (every 4th), tallest at phrase markers (every 16th = 4 bars).
        Painted under cues + playhead so it never hides them."""
        c = self.canvas
        c.delete("beat")
        if not self._beats:
            return
        dur = player.duration(self._deck) if self._track_path else 0.0
        if dur <= 0:
            # Fall back to last beat time + bar margin so we can still
            # draw before audio is loaded
            dur = self._beats[-1] + 1.0 if self._beats else 0.0
            if dur <= 0:
                return
        w = max(1, c.winfo_width())
        h = _WF_HEIGHT
        # Three styles → three colour stops + heights
        text_dim = COLORS["text_dim"]
        accent = COLORS["accent"]
        for i, t in enumerate(self._beats):
            x = int(t / dur * w)
            if x < 0 or x > w:
                continue
            if i % 16 == 0:           # phrase (every 4 bars)
                c.create_line(x, 0, x, h, fill=accent, width=1,
                              tags=("beat",))
            elif i % 4 == 0:          # downbeat
                c.create_line(x, 0, x, h, fill=accent, width=1,
                              dash=(2, 4), tags=("beat",))
            else:                     # off-beat — tiny tick top/bottom only
                c.create_line(x, 0, x, 4, fill=text_dim, width=1,
                              tags=("beat",))
                c.create_line(x, h - 4, x, h, fill=text_dim, width=1,
                              tags=("beat",))

    def _draw_structure(self):
        """Vertical green wash up to intro_end and from outro_start.
        Tiny labels above the waveform mark where each boundary is.
        Painted under cues + playhead so they never hide them."""
        c = self.canvas
        c.delete("structure")
        if self._intro_end is None and self._outro_start is None:
            return
        dur = player.duration(self._deck) if self._track_path else 0.0
        if dur <= 0:
            # Use the structure's own duration as fallback before audio loads
            if self._outro_start:
                dur = max(dur, self._outro_start + 8.0)
            if dur <= 0:
                return
        w = max(1, c.winfo_width())
        h = _WF_HEIGHT
        # Subtle washes (filled rectangles via 4-pt polyline trick) +
        # small top labels so the user knows what they're looking at.
        success = COLORS["success"]
        warning = COLORS["warning"]

        if self._intro_end and self._intro_end > 0:
            x = int(self._intro_end / dur * w)
            x = max(2, min(x, w - 2))
            # Vertical line at intro_end
            c.create_line(x, 0, x, h, fill=success, width=1,
                           dash=(4, 3), tags=("structure",))
            c.create_text(x - 2, h - 4, text="◀ INTRO",
                           fill=success, anchor="se",
                           font=("Segoe UI", 8, "bold"),
                           tags=("structure",))

        if self._outro_start and self._outro_start < dur:
            x = int(self._outro_start / dur * w)
            x = max(2, min(x, w - 2))
            c.create_line(x, 0, x, h, fill=warning, width=1,
                           dash=(4, 3), tags=("structure",))
            c.create_text(x + 2, h - 4, text="OUTRO ▶",
                           fill=warning, anchor="sw",
                           font=("Segoe UI", 8, "bold"),
                           tags=("structure",))

        # Drops — small downward triangles at the top of the canvas
        # (subtle, doesn't compete with cue markers below)
        accent2 = COLORS["accent2"]
        for t_drop in self._drops:
            x = int(t_drop / dur * w)
            if x < 2 or x > w - 2:
                continue
            c.create_polygon(x - 4, 0, x + 4, 0, x, 6,
                              fill=accent2, outline="",
                              tags=("structure",))

    def _draw_cues(self):
        c = self.canvas
        c.delete("cue")
        w = max(1, c.winfo_width())
        h = _WF_HEIGHT
        dur = player.duration(self._deck) if self._track_path else 0.0
        if dur <= 0:
            return
        accent2 = COLORS["accent2"]
        for cue in self._cues:
            t = float(cue.get("position", 0.0))
            x = int(t / dur * w)
            c.create_line(x, 0, x, h,
                          fill=accent2, width=2, tags=("cue",))
            c.create_text(x + 3, 8,
                          text=cue.get("label", "")[:8],
                          fill=accent2, anchor="w",
                          font=("Segoe UI", 8, "bold"),
                          tags=("cue",))

    def _draw_playhead(self):
        """The only thing redrawn at 20fps. Single delete + single line."""
        c = self.canvas
        c.delete("playhead")
        if not self._track_path:
            return
        if player.current_path(self._deck) != self._track_path:
            return
        dur = player.duration(self._deck)
        if dur <= 0:
            return
        w = max(1, c.winfo_width())
        h = _WF_HEIGHT
        pos = player.position(self._deck)
        px = int(pos / dur * w)
        c.create_line(px, 0, px, h,
                      fill=COLORS["warning"], width=2,
                      tags=("playhead",))

    def _on_key(self, event):
        """DJ keyboard shortcuts. Only fires when canvas has focus."""
        if not self._track_path:
            return
        sym = (event.keysym or "").lower()
        # Map digit-1..digit-8 → cue index 0..7 (so 1 = first cue)
        if sym in ("1", "2", "3", "4", "5", "6", "7", "8"):
            idx = int(sym) - 1
            if 0 <= idx < len(self._cues):
                self._jump_to(self._cues[idx])
            return "break"   # consume so Tk doesn't bubble it up
        if sym == "space":
            self._toggle_play()
            return "break"
        if sym in ("home", "0"):
            try:
                player.seek(self._deck, 0.0)
                self.play_btn.configure(text="⏸  Pause",
                                         fg_color=COLORS["warning"])
                self._start_tick()
            except Exception:
                pass
            return "break"

    def _on_canvas_click(self, event):
        """Click on waveform → seek to that position."""
        if not self._track_path:
            return
        w = max(1, self.canvas.winfo_width())
        dur = player.duration(self._deck)
        if dur <= 0:
            # No audio loaded yet — load it (fast) then seek
            ok = player.play(self._deck, self._track_path)
            if not ok:
                return
            dur = player.duration(self._deck)
            if dur <= 0:
                return
        target = (event.x / w) * dur
        player.seek(self._deck, target)
        self.play_btn.configure(text="⏸  Pause", fg_color=COLORS["warning"])
        self._start_tick()

    # ── Cue points ───────────────────────────────────────────────

    def _add_cue(self):
        if not self._track_path:
            return
        # Use the current playback position whether playing OR paused
        pos = player.position(self._deck)
        dur = player.duration(self._deck) or 1.0
        ratio = pos / dur
        if ratio < 0.15:
            default_label = "INTRO"
        elif ratio < 0.45:
            default_label = "BUILD"
        elif ratio < 0.65:
            default_label = "DROP"
        elif ratio < 0.85:
            default_label = "BREAK"
        else:
            default_label = "OUTRO"
        existing = {c.get("label") for c in self._cues}
        suffix = 2
        label = default_label
        while label in existing:
            label = f"{default_label}{suffix}"
            suffix += 1
        self._cues.append({"label": label, "position": round(pos, 2)})
        self._cues.sort(key=lambda c: c.get("position", 0))
        self._render_cue_chips()
        # Only the cue layer changes — keep the static waveform painted
        self._draw_cues()
        self._draw_playhead()
        self._notify_cues()

    def _render_cue_chips(self):
        for w in self.cue_strip.winfo_children():
            w.destroy()
        if not self._cues:
            ctk.CTkLabel(
                self.cue_strip,
                text="(aucun cue — joue, puis « + cue » à un moment clé)",
                text_color=COLORS["text_dim"],
                font=font(10)
            ).pack(anchor="w")
            return
        for i, cue in enumerate(self._cues):
            chip = ctk.CTkFrame(self.cue_strip, fg_color=COLORS["accent2"],
                                 corner_radius=10)
            chip.pack(side="left", padx=3, pady=2)
            ctk.CTkButton(
                chip, text=f"{cue['label']} · {_fmt(cue['position'])}",
                font=font(10, "bold"),
                fg_color="transparent", hover_color="#e0356f",
                text_color=COLORS["on_accent2"],
                width=110, height=24,
                command=lambda c=cue: self._jump_to(c)
            ).pack(side="left", padx=(4, 0))
            ctk.CTkButton(
                chip, text="×", width=20, height=24,
                font=font(11, "bold"),
                fg_color="transparent", hover_color="#e0356f",
                text_color=COLORS["on_accent2"],
                command=lambda idx=i: self._delete_cue(idx)
            ).pack(side="left", padx=(0, 4))

    def _jump_to(self, cue: dict):
        if not self._track_path:
            return
        player.seek(self._deck, float(cue.get("position", 0.0)))
        self.play_btn.configure(text="⏸  Pause", fg_color=COLORS["warning"])
        self._start_tick()

    def _delete_cue(self, idx: int):
        if 0 <= idx < len(self._cues):
            del self._cues[idx]
            self._render_cue_chips()
            self._draw_cues()
            self._draw_playhead()
            self._notify_cues()

    def _notify_cues(self):
        if self._on_cues_changed:
            try:
                self._on_cues_changed(list(self._cues))
            except Exception:
                pass

    @property
    def cues(self) -> list[dict]:
        return list(self._cues)

    def destroy(self):
        self._stop()
        super().destroy()


def _fmt(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 60}:{s % 60:02d}"
