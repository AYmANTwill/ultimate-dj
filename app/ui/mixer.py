"""
Mixer page — find harmonic transitions between tracks AND preview the
blend in real time via the dual-deck crossfader.

Workflow:
1. Pick a track on the left  → loads on Deck A + finds transitions
2. Click any transition       → loads on Deck B
3. Crossfader slider mixes A↔B (equal-power curve)
4. ▶ on each deck plays/pauses independently

Performance:
- Track list and transition list are both FastList (ttk.Treeview).
- all_tracks() and find_transitions() run off the UI thread — selecting
  a track in a 5000-track library never freezes the UI.
"""
from __future__ import annotations

import threading

import customtkinter as ctk

from app.config import COLORS
from app.engine import player
from app.engine.library import (
    get_connection, all_tracks, find_transitions, transition_score,
)
from app.logger import log_error
from app.ui.deck import DeckWidget
from app.ui.fastlist import FastList
from app.ui.helpers import font


class MixerPage(ctk.CTkFrame):
    def __init__(self, parent):
        super().__init__(parent, fg_color=COLORS["bg_dark"])
        self._tracks: list[dict] = []
        self._filtered: list[dict] = []
        self._selected: dict | None = None         # current Deck A
        self._b_track: dict | None = None          # current Deck B
        self._transitions: list[tuple[dict, float]] = []
        self._build_ui()

    def _build_ui(self):
        ctk.CTkLabel(
            self, text="Mixer",
            font=font(26, "bold"),
            text_color=COLORS["text"],
        ).pack(anchor="w", padx=30, pady=(24, 4))
        ctk.CTkLabel(
            self, text="Select a track to find harmonically compatible transitions",
            font=font(13),
            text_color=COLORS["text_dim"],
        ).pack(anchor="w", padx=30, pady=(0, 16))

        # Outer vertical PanedWindow — top half holds the two list
        # columns, bottom half holds the dual decks. The user can drag
        # the gutter between them to give more vertical room to either.
        import tkinter as tk
        self._main_pane = tk.PanedWindow(
            self, orient="vertical",
            bg=COLORS["bg_dark"], sashwidth=6, sashrelief="flat",
            bd=0, opaqueresize=False)
        self._main_pane.pack(fill="both", expand=True, padx=30, pady=(0, 16))

        # Top half — horizontal split between Library list and Transitions
        # list. Drag the vertical gutter to widen one at the expense of
        # the other. Min sizes prevent collapse to 0.
        self._cols_pane = tk.PanedWindow(
            self._main_pane, orient="horizontal",
            bg=COLORS["bg_dark"], sashwidth=6, sashrelief="flat",
            bd=0, opaqueresize=False)
        self._main_pane.add(self._cols_pane,
                             minsize=200, height=440, stretch="always")

        # Left: track selector ──────────────────────────────────────
        left = ctk.CTkFrame(self._cols_pane,
                             fg_color=COLORS["bg_card"], corner_radius=12)
        self._cols_pane.add(left, minsize=240, width=420, stretch="always")

        ctk.CTkLabel(left, text="Your Library",
                     font=font(14, "bold"),
                     text_color=COLORS["accent"]).pack(anchor="w", padx=12, pady=(10, 4))

        self.search_entry = ctk.CTkEntry(
            left, placeholder_text="Filter...", height=32,
            fg_color=COLORS["bg_input"], border_color=COLORS["bg_input"],
            text_color=COLORS["text"])
        self.search_entry.pack(fill="x", padx=12, pady=(0, 6))
        self.search_entry.bind("<KeyRelease>", lambda e: self._filter_tracks())

        self.lib_table = FastList(
            left,
            [("title", "Title", 240),
             ("bpm",   "BPM",    60),
             ("cam",   "Cam",    60)],
            on_select=lambda rows: self._select_track(rows[0]) if rows else None,
            height=18,
        )
        self.lib_table.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        # Right: transitions ────────────────────────────────────────
        right = ctk.CTkFrame(self._cols_pane,
                              fg_color=COLORS["bg_card"], corner_radius=12)
        self._cols_pane.add(right, minsize=240, width=520, stretch="always")

        ctk.CTkLabel(right, text="Best Transitions",
                     font=font(14, "bold"),
                     text_color=COLORS["accent2"]).pack(anchor="w", padx=12, pady=(10, 4))

        self.selected_label = ctk.CTkLabel(
            right, text="Select a track from the left",
            text_color=COLORS["text_dim"], font=font(12))
        self.selected_label.pack(anchor="w", padx=12, pady=(0, 6))

        self.tx_table = FastList(
            right,
            [("score", "Score", 60),
             ("title", "Title", 240),
             ("bpm",   "BPM",    60),
             ("cam",   "Cam",    60)],
            on_select=lambda rows: self._load_b(rows[0]) if rows else None,
            height=12,
        )
        self.tx_table.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        # ── Dual-deck preview (bottom pane of the vertical sash) ─
        # Wrapping the explanatory header + the decks_row in a single
        # CTkFrame so they live as one PanedWindow pane that can be
        # resized as a unit.
        bottom_pane = ctk.CTkFrame(self._main_pane, fg_color="transparent")
        # Start tall enough that BOTH decks show waveform + transport
        # (Play / Stop / + cue) without clipping — the user can shrink
        # if they want via the sash. Was 240, but ~80px of buttons were
        # cut off below the time labels.
        self._main_pane.add(
            bottom_pane, minsize=240, height=340, stretch="never")

        ctk.CTkLabel(
            bottom_pane,
            text="Preview de transition — charge ta track sur Deck A, "
                 "clique une transition à droite pour la coller sur Deck B, "
                 "et bouge le crossfader pour entendre le mix.",
            font=font(11), text_color=COLORS["text_dim"],
            justify="left", wraplength=900,
        ).pack(anchor="w", pady=(0, 4))

        self.decks_row = ctk.CTkFrame(
            bottom_pane, fg_color=COLORS["bg_card"], corner_radius=12)
        self.decks_row.pack(fill="both", expand=True)
        # No pack_propagate(False) — the PanedWindow pane controls
        # height now, so child decks fill the available space

        self.deck_a = None
        self.deck_b = None
        self.xfade = None
        self._decks_built = False

    def on_show(self):
        # Defer DB load so the page paints first
        self.after_idle(lambda: threading.Thread(
            target=self._load_thread, daemon=True).start())
        # Build the dual decks lazily — they're heavy (~26 CTk widgets total)
        if not self._decks_built:
            self.after_idle(self._build_decks)

    def on_hide(self):
        # Stop both decks when the user navigates away — otherwise audio
        # keeps playing in the background which is jarring.
        try:
            player.stop("A")
            player.stop("B")
        except Exception:
            pass

    def _build_decks(self):
        if self._decks_built:
            return
        deck_a_col = ctk.CTkFrame(self.decks_row, fg_color="transparent")
        deck_a_col.pack(side="left", fill="both", expand=True,
                         padx=(8, 4), pady=8)
        ctk.CTkLabel(deck_a_col, text="Deck A · current",
                     font=font(11, "bold"),
                     text_color=COLORS["accent"]
                     ).pack(anchor="w", padx=4)
        self.deck_a = DeckWidget(deck_a_col, deck="A")
        self.deck_a.pack(fill="x", padx=2, pady=(2, 0))

        deck_b_col = ctk.CTkFrame(self.decks_row, fg_color="transparent")
        deck_b_col.pack(side="right", fill="both", expand=True,
                         padx=(4, 8), pady=8)
        # Header row: "Deck B · next" label + Sync to A button + status
        b_head = ctk.CTkFrame(deck_b_col, fg_color="transparent")
        b_head.pack(fill="x", padx=4)
        ctk.CTkLabel(b_head, text="Deck B · next",
                     font=font(11, "bold"),
                     text_color=COLORS["accent2"]
                     ).pack(side="left")
        # Discover the available time-stretch backend up front so we
        # can show its name on the Sync button. rubberband = high-quality
        # (Pioneer/NI), ffmpeg = decent fallback shipped with the app.
        try:
            backend_name = "rubberband" if player._has_rubberband() else "ffmpeg"
        except Exception:
            backend_name = "ffmpeg"
        self._stretch_backend = backend_name

        self.sync_btn = ctk.CTkButton(
            b_head,
            text=f"⇅ Sync to A · {backend_name}",
            width=180, height=24,
            font=font(10, "bold"),
            fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
            text_color=COLORS["bg_dark"],
            command=self._sync_b_to_a,
            state="disabled")
        self.sync_btn.pack(side="right")
        # Friendly hint when on the lower-quality backend so the user
        # knows why the stretch sounds "smeared" on bigger ratios.
        if backend_name != "rubberband":
            hint = ctk.CTkLabel(
                b_head,
                text="(installe rubberband.exe pour qualité +)",
                font=font(9),
                text_color=COLORS["text_dim"])
            hint.pack(side="right", padx=4)
        self.sync_status = ctk.CTkLabel(
            b_head, text="", font=font(10),
            text_color=COLORS["text_dim"])
        self.sync_status.pack(side="right", padx=8)

        self.deck_b = DeckWidget(deck_b_col, deck="B")
        self.deck_b.pack(fill="x", padx=2, pady=(2, 0))

        # Crossfader
        xfade_row = ctk.CTkFrame(self.decks_row, fg_color="transparent")
        xfade_row.pack(fill="x", padx=12, pady=(0, 8))
        ctk.CTkLabel(xfade_row, text="A", font=font(12, "bold"),
                     text_color=COLORS["accent"]
                     ).pack(side="left", padx=(0, 6))
        self.xfade_var = ctk.DoubleVar(value=0.0)
        self.xfade = ctk.CTkSlider(
            xfade_row, from_=0, to=1, variable=self.xfade_var,
            command=lambda v: player.crossfade(float(v)),
            progress_color=COLORS["accent2"])
        self.xfade.pack(side="left", fill="x", expand=True, padx=4)
        ctk.CTkLabel(xfade_row, text="B", font=font(12, "bold"),
                     text_color=COLORS["accent2"]
                     ).pack(side="right", padx=(6, 0))
        self._decks_built = True
        # Update sync indicator periodically
        self.after(500, self._refresh_sync_indicator)

    def _load_thread(self):
        try:
            tracks = all_tracks(get_connection())
        except Exception as e:
            log_error("mixer.load failed", e)
            tracks = []
        self.after(0, lambda t=tracks: self._on_loaded(t))

    def _on_loaded(self, tracks: list[dict]):
        self._tracks = tracks
        self._filtered = tracks
        self._render_lib_table(tracks)

    def _render_lib_table(self, tracks: list[dict]):
        rows = [
            ((t["title"] or "?")[:60],
             f"{(t['bpm'] or 0):.0f}",
             t["camelot"] or "?")
            for t in tracks
        ]
        self.lib_table.set_rows(rows)

    def _filter_tracks(self):
        q = self.search_entry.get().strip().lower()
        if not q:
            self._filtered = self._tracks
        else:
            self._filtered = [t for t in self._tracks
                              if q in (t["title"] or "").lower()]
        self._render_lib_table(self._filtered)

    def _select_track(self, row: tuple):
        # Find the track dict matching the row by title prefix (it's
        # what the FastList knows). The list is small enough to scan.
        title_shown = row[0]
        # Take the first track whose displayed title matches
        match = next(
            (t for t in self._filtered
             if (t["title"] or "?")[:60] == title_shown),
            None,
        )
        if not match:
            return
        self._selected = match
        self.selected_label.configure(
            text=f"{match['title']}  —  {(match['bpm'] or 0):.0f} BPM  |  "
                 f"{match['key']}  ({match['camelot']})  |  "
                 f"Energy {(match['energy'] or 0):.1f}",
            text_color=COLORS["text"])

        # Load the selected track on Deck A — make sure the lazy deck
        # is built first (in case the user clicks before idle has fired)
        if not self._decks_built:
            self._build_decks()
        self.deck_a.load_track(match)

        # Compute transitions off the UI thread — O(N) over the library
        threading.Thread(
            target=self._tx_thread, args=(match,), daemon=True).start()

    def _tx_thread(self, track: dict):
        try:
            transitions = find_transitions(get_connection(), track, limit=20)
        except Exception as e:
            log_error("find_transitions failed", e)
            transitions = []
        self.after(0, lambda tx=transitions: self._render_transitions(tx))

    def _load_b(self, row: tuple):
        """Load the chosen transition target on Deck B for crossfade test."""
        # Score column at index 0, title at index 1
        title_shown = row[1] if len(row) > 1 else ""
        # Find the transition whose track title matches
        match = next(
            (t for (t, _s) in self._transitions
             if (t["title"] or "?")[:60] == title_shown),
            None,
        )
        if match:
            if not self._decks_built:
                self._build_decks()
            self.deck_b.load_track(match)
            self._b_track = match
            # Sync to A is now possible
            try:
                self.sync_btn.configure(state="normal")
            except Exception:
                pass

    def _sync_b_to_a(self):
        """Time-stretch Deck B's audio so its tempo matches Deck A's BPM.

        Runs in a worker thread because rubberband / librosa take
        ~1-3s on a 5-minute track. The sync_btn shows "Syncing…" until
        the stretched audio is swapped in.
        """
        if not self._selected:
            self.sync_status.configure(
                text="(charge d'abord une track sur Deck A)",
                text_color=COLORS["warning"])
            return
        target_bpm = float(self._selected.get("bpm") or 0)
        if target_bpm <= 0:
            self.sync_status.configure(
                text="(BPM Deck A inconnu — relance l'analyse)",
                text_color=COLORS["error"])
            return

        backend = "rubberband" if player._has_rubberband() else "librosa"
        self.sync_btn.configure(state="disabled", text="Syncing…")
        self.sync_status.configure(
            text=f"stretching via {backend}…",
            text_color=COLORS["accent"])

        def work():
            try:
                ok = player.sync_to("B", target_bpm)
            except Exception as e:
                log_error("sync_to failed", e)
                ok = False
            self.after(0, lambda o=ok: self._after_sync(o, backend))

        threading.Thread(target=work, daemon=True).start()

    def _after_sync(self, ok: bool, backend: str):
        self.sync_btn.configure(state="normal", text="⇅ Sync to A")
        if ok:
            self.sync_status.configure(
                text=f"synced ({backend})",
                text_color=COLORS["success"])
        else:
            self.sync_status.configure(
                text="échec — vérifie les BPM",
                text_color=COLORS["error"])

    def _refresh_sync_indicator(self):
        """Update the BPM-mismatch hint at most twice a second.
        Shows "Δ +3 BPM" when out of sync, green checkmark when in sync."""
        try:
            a_bpm = float((self._selected or {}).get("bpm") or 0)
            b_bpm = float(getattr(self, "_b_track", {}).get("bpm") or 0)
        except Exception:
            a_bpm = b_bpm = 0
        if a_bpm > 0 and b_bpm > 0 and self._decks_built:
            delta = b_bpm - a_bpm
            if abs(delta) < 0.5:
                txt = "✓ in sync"
                col = COLORS["success"]
            else:
                sign = "+" if delta > 0 else ""
                txt = f"Δ {sign}{delta:.1f} BPM"
                col = COLORS["warning"]
            try:
                self.sync_status.configure(text=txt, text_color=col)
            except Exception:
                pass
        # Re-arm the periodic refresh
        self.after(500, self._refresh_sync_indicator)

    def _render_transitions(self, transitions: list[tuple[dict, float]]):
        # Cache for _load_b so we don't have to re-query the DB
        self._transitions = list(transitions)
        rows = []
        tags = []
        for t, score in transitions:
            rows.append((
                f"{score:.0f}",
                (t["title"] or "?")[:60],
                f"{(t['bpm'] or 0):.0f}",
                t["camelot"] or "?",
            ))
            if score >= 80:
                tags.append(("ok",))
            elif score >= 50:
                tags.append(("warn",))
            else:
                tags.append(("err",))
        self.tx_table.set_rows(rows, row_tags=tags)
