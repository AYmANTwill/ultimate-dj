"""
TrackEditor — modal popup to edit DJ metadata for a single track.

Covers the pro-DJ essentials that were missing from the read-only Library:
- Star rating (0–5)  ← A/B/C tier system
- Genre + free-form tags
- BPM override with ×2 / ÷2 quick buttons + manual entry + tap-tempo
- Lock BPM so future re-analysis can't overwrite it
- Cue points (added in a later patch — UI hooks already wired)
- Audio preview (added in a later patch — placeholder area present)

The popup reads the track row, exposes editable fields, and on Save writes
to the DB through the small setter functions in `engine.library`. It also
pushes BPM/key changes back to the file's ID3 tags so Rekordbox/Serato
see them on next import.
"""
from __future__ import annotations

import time
from pathlib import Path

import customtkinter as ctk

from app.config import COLORS
from app.engine.analyzer import write_tags
from app.engine.library import (
    get_connection, set_rating, set_genre, set_tags,
    override_bpm, set_cue_points, get_cue_points,
)
from app.ui.helpers import font


class TrackEditor(ctk.CTkToplevel):
    """Modal track editor. Pass `on_save` to refresh the caller's view."""

    def __init__(self, parent, track: dict, *, on_save=None, deck: str = "A"):
        super().__init__(parent)
        self.title(f"Track — {(track.get('title') or '?')[:50]}")
        self.geometry("980x780")
        self.configure(fg_color=COLORS["bg_dark"])
        self.transient(parent)
        try:
            self.grab_set()
        except Exception:
            pass

        self._track = dict(track)  # snapshot
        self._on_save = on_save
        self._cues: list[dict] = list(get_cue_points(track))
        self._deck = deck
        # Tap-tempo state — taps are timestamps in seconds
        self._taps: list[float] = []
        self._deck_widget = None  # filled in _build

        # Make sure mixer stops when popup closes
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build()

    # ── UI ────────────────────────────────────────────────────────

    def _build(self):
        # Header with title + path
        hdr = ctk.CTkFrame(self, fg_color="transparent")
        hdr.pack(fill="x", padx=20, pady=(20, 6))
        ctk.CTkLabel(
            hdr, text=self._track.get("title") or "?",
            font=font(18, "bold"),
            text_color=COLORS["text"], anchor="w"
        ).pack(anchor="w")
        ctk.CTkLabel(
            hdr, text=str(Path(self._track["path"]).name),
            font=font(11), text_color=COLORS["text_dim"], anchor="w"
        ).pack(anchor="w")

        # ── Deck (waveform + transport + cues) ──────────────────
        from app.ui.deck import DeckWidget
        self._deck_widget = DeckWidget(
            self, deck=self._deck,
            on_cues_changed=self._on_cues_changed)
        self._deck_widget.pack(fill="x", padx=20, pady=(8, 12))
        self._deck_widget.load_track(self._track)

        # Two-column body
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=20, pady=10)

        left = ctk.CTkFrame(body, fg_color=COLORS["bg_card"], corner_radius=10)
        left.pack(side="left", fill="both", expand=True, padx=(0, 10))

        right = ctk.CTkFrame(body, fg_color=COLORS["bg_card"], corner_radius=10)
        right.pack(side="right", fill="both", expand=True)

        # Left column: rating, genre, tags ──────────────────────────
        self._section(left, "Rating")
        self._build_rating(left)

        self._section(left, "Genre")
        self.genre_entry = ctk.CTkEntry(
            left, height=32,
            placeholder_text="e.g. tech house, dnb, afro tech…",
            fg_color=COLORS["bg_input"],
            border_color=COLORS["bg_input"],
            text_color=COLORS["text"])
        self.genre_entry.pack(fill="x", padx=12, pady=(0, 8))
        if self._track.get("genre"):
            self.genre_entry.insert(0, self._track["genre"])

        self._section(left, "Tags")
        self.tags_entry = ctk.CTkEntry(
            left, height=32,
            placeholder_text="comma,separated,tags",
            fg_color=COLORS["bg_input"],
            border_color=COLORS["bg_input"],
            text_color=COLORS["text"])
        self.tags_entry.pack(fill="x", padx=12, pady=(0, 8))
        if self._track.get("tags"):
            self.tags_entry.insert(0, self._track["tags"])

        # Right column: BPM override + tap-tempo ────────────────────
        self._section(right, "BPM")
        # Make it clear this is metadata, not a tempo transform on the audio
        ctk.CTkLabel(
            right,
            text="Étiquette uniquement — ne change pas le tempo de "
                 "l'audio. Pour vraiment time-stretch, utilise le "
                 "Mixer → Sync to A.",
            font=font(10),
            text_color=COLORS["text_dim"],
            justify="left", wraplength=350
        ).pack(anchor="w", padx=12, pady=(0, 4))
        cur_bpm = float(self._track.get("bpm") or 0)
        bpm_row = ctk.CTkFrame(right, fg_color="transparent")
        bpm_row.pack(fill="x", padx=12, pady=(0, 4))

        self.bpm_var = ctk.StringVar(value=f"{cur_bpm:.1f}")
        self.bpm_entry = ctk.CTkEntry(
            bpm_row, width=110, height=36, textvariable=self.bpm_var,
            font=font(16, "bold"),
            justify="center",
            fg_color=COLORS["bg_input"],
            border_color=COLORS["bg_input"],
            text_color=COLORS["warning"])
        self.bpm_entry.pack(side="left", padx=(0, 6))

        # ×2 / ÷2 quick fixes for half/double-time detection bugs
        for txt, fn in [("÷2", lambda: self._scale_bpm(0.5)),
                        ("×2", lambda: self._scale_bpm(2.0))]:
            ctk.CTkButton(
                bpm_row, text=txt, width=46, height=36,
                font=font(14, "bold"),
                fg_color=COLORS["bg_input"],
                hover_color=COLORS["accent"],
                text_color=COLORS["text"],
                command=fn).pack(side="left", padx=2)

        # Lock so future analysis doesn't overwrite the manual value.
        # We keep a reference to the CTkCheckBox so _set_lock() can call
        # .select()/.deselect() — CTk's BooleanVar.set() doesn't always
        # repaint the box (known issue with CTkCheckBox + BooleanVar).
        self.lock_var = ctk.BooleanVar(
            value=bool(self._track.get("bpm_locked")))
        self.lock_check = ctk.CTkCheckBox(
            right, text="Verrouiller le BPM (analyse ne pourra plus l'écraser)",
            variable=self.lock_var,
            font=font(11), text_color=COLORS["text_dim"],
            checkbox_height=16, checkbox_width=16,
            fg_color=COLORS["accent"])
        self.lock_check.pack(anchor="w", padx=12, pady=(2, 8))
        # Re-paint the visual according to the loaded state
        if self.lock_var.get():
            self.lock_check.select()
        else:
            self.lock_check.deselect()

        # Tap-tempo ──────────────────────────────────────────────
        self._section(right, "Tap-tempo")
        tap_row = ctk.CTkFrame(right, fg_color="transparent")
        tap_row.pack(fill="x", padx=12, pady=(0, 4))
        ctk.CTkButton(
            tap_row, text="TAP", width=120, height=44,
            font=font(16, "bold"),
            fg_color=COLORS["accent2"], hover_color="#e0356f",
            text_color=COLORS["on_accent2"],
            command=self._on_tap).pack(side="left")
        self.tap_label = ctk.CTkLabel(
            tap_row, text="appuie au rythme (4+ taps)",
            font=font(11), text_color=COLORS["text_dim"])
        self.tap_label.pack(side="left", padx=10)
        ctk.CTkButton(
            tap_row, text="reset", width=60, height=28,
            font=font(10),
            fg_color="transparent", hover_color=COLORS["bg_input"],
            text_color=COLORS["text_dim"],
            command=self._reset_taps).pack(side="right")

        # Key + confidence display ──────────────────────────────────
        self._section(right, "Key")
        kc = self._track.get("key_confidence")
        kc_text = ""
        kc_color = COLORS["text_dim"]
        if kc is not None:
            try:
                pct = int(round(float(kc) * 100))
                kc_text = f"  ·  {pct}% confiance"
                kc_color = (COLORS["success"] if pct >= 75
                            else COLORS["warning"] if pct >= 50
                            else COLORS["error"])
            except Exception:
                pass
        ctk.CTkLabel(
            right,
            text=f"{self._track.get('key') or '?'}  "
                 f"({self._track.get('camelot') or '?'}){kc_text}",
            font=font(14, "bold"),
            text_color=kc_color
        ).pack(anchor="w", padx=12, pady=(0, 12))

        # Footer: Cancel / Save ─────────────────────────────────────
        # Reflect the user's tag-write opt-in (Settings → Interop) so
        # the DJ knows whether saving will mutate the file or only the
        # internal DB. Critical for « ne pollue pas Rekordbox » trust.
        from app.config import should_write_tags
        will_write = should_write_tags()
        if will_write:
            tag_msg = ("⚠ Save écrira aussi dans les tags ID3/RIFF du fichier "
                       "(Settings → Interop = ON). Rekordbox importera "
                       "ces valeurs.")
            tag_color = COLORS["warning"]
        else:
            tag_msg = ("✓ Save garde tout dans la DB d'Ultimate DJ — "
                       "les fichiers audio ne sont pas modifiés. Rekordbox "
                       "fera sa propre analyse à l'import.")
            tag_color = COLORS["success"]
        ctk.CTkLabel(
            self, text=tag_msg,
            font=font(10), text_color=tag_color,
            justify="left", wraplength=920, anchor="w",
        ).pack(anchor="w", padx=20, pady=(0, 4))

        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", padx=20, pady=(6, 18))
        ctk.CTkButton(
            bar, text="Annuler", width=110, height=36,
            fg_color=COLORS["bg_card"], hover_color=COLORS["bg_input"],
            text_color=COLORS["text"],
            command=self.destroy
        ).pack(side="right", padx=4)
        ctk.CTkButton(
            bar, text="Enregistrer", width=160, height=36,
            font=font(13, "bold"),
            fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
            text_color=COLORS["bg_dark"],
            command=self._save
        ).pack(side="right", padx=4)

    def _section(self, parent, title: str):
        ctk.CTkLabel(
            parent, text=title,
            font=font(12, "bold"),
            text_color=COLORS["accent"]
        ).pack(anchor="w", padx=12, pady=(12, 2))

    # ── Rating widget ────────────────────────────────────────────

    def _build_rating(self, parent):
        self._rating = int(self._track.get("rating") or 0)
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=12, pady=(0, 8))
        self._star_btns: list[ctk.CTkButton] = []
        for i in range(1, 6):
            btn = ctk.CTkButton(
                row, text="★", width=42, height=38,
                font=font(22, "bold"),
                fg_color="transparent",
                hover_color=COLORS["bg_input"],
                text_color=(COLORS["warning"] if i <= self._rating
                            else COLORS["text_dim"]),
                command=lambda r=i: self._set_rating(r))
            btn.pack(side="left", padx=2)
            self._star_btns.append(btn)
        ctk.CTkButton(
            row, text="clear", width=60, height=24,
            font=font(10),
            fg_color="transparent", hover_color=COLORS["bg_input"],
            text_color=COLORS["text_dim"],
            command=lambda: self._set_rating(0)
        ).pack(side="left", padx=8)

    def _set_rating(self, r: int):
        self._rating = r
        for i, btn in enumerate(self._star_btns, 1):
            btn.configure(text_color=(COLORS["warning"] if i <= r
                                       else COLORS["text_dim"]))

    # ── BPM helpers ──────────────────────────────────────────────

    def _set_lock(self, locked: bool):
        """Toggle the lock checkbox AND repaint it (CTk doesn't always
        re-render when only the BooleanVar changes)."""
        self.lock_var.set(bool(locked))
        if locked:
            self.lock_check.select()
        else:
            self.lock_check.deselect()

    def _scale_bpm(self, factor: float):
        try:
            cur = float(self.bpm_var.get())
        except ValueError:
            return
        new = cur * factor
        self.bpm_var.set(f"{new:.1f}")
        self._set_lock(True)

    def _on_tap(self):
        now = time.time()
        # Reset stale taps if more than 2s since last
        if self._taps and now - self._taps[-1] > 2.0:
            self._taps = []
        self._taps.append(now)
        if len(self._taps) < 2:
            self.tap_label.configure(
                text=f"{len(self._taps)} tap — continue…",
                text_color=COLORS["accent"])
            return
        # Average inter-tap interval over the last 8 taps
        recent = self._taps[-8:]
        intervals = [b - a for a, b in zip(recent[:-1], recent[1:])]
        avg = sum(intervals) / len(intervals)
        if avg <= 0:
            return
        bpm = 60.0 / avg
        # Clamp to plausible DJ range
        while bpm < 60:
            bpm *= 2
        while bpm > 200:
            bpm /= 2
        self.bpm_var.set(f"{bpm:.1f}")
        self._set_lock(True)
        self.tap_label.configure(
            text=f"{len(self._taps)} taps · {bpm:.1f} BPM",
            text_color=COLORS["success"])

    def _reset_taps(self):
        self._taps = []
        self.tap_label.configure(
            text="appuie au rythme (4+ taps)",
            text_color=COLORS["text_dim"])

    # ── Cue points (synced from DeckWidget) ───────────────────────

    def _on_cues_changed(self, cues: list[dict]):
        self._cues = list(cues)

    # ── Window lifecycle ─────────────────────────────────────────

    def _on_close(self):
        # Make sure audio stops when the popup closes
        try:
            from app.engine import player
            player.stop(self._deck)
        except Exception:
            pass
        self.destroy()

    # ── Save ─────────────────────────────────────────────────────

    def _save(self):
        path = self._track["path"]
        conn = get_connection()

        # Rating
        set_rating(conn, path, self._rating)

        # Genre + tags
        set_genre(conn, path, self.genre_entry.get())
        tags_text = self.tags_entry.get().strip()
        if tags_text:
            set_tags(conn, path, tags_text.split(","))
        else:
            set_tags(conn, path, [])

        # BPM override (if it changed or lock toggled)
        try:
            new_bpm = float(self.bpm_var.get())
        except ValueError:
            new_bpm = float(self._track.get("bpm") or 0)

        cur_bpm = float(self._track.get("bpm") or 0)
        cur_lock = bool(self._track.get("bpm_locked"))
        if abs(new_bpm - cur_bpm) > 0.05 or self.lock_var.get() != cur_lock:
            override_bpm(conn, path, new_bpm, lock=bool(self.lock_var.get()))
            # Push BPM back to ID3 tags so Rekordbox/Serato sees it
            try:
                write_tags(path, new_bpm, self._track.get("key") or "")
            except Exception:
                pass

        # Cue points — always saved (so deletes also persist)
        # Pull the freshest list from the deck widget if available
        if self._deck_widget is not None:
            self._cues = self._deck_widget.cues
        set_cue_points(conn, path, self._cues)

        if self._on_save:
            try:
                self._on_save()
            except Exception:
                pass

        # Show a non-blocking toast on the parent (main window) BEFORE
        # closing the popup, so the user gets confirmation that the
        # save persisted. Avoids the previous silent-close UX where
        # the popup just disappeared with no feedback.
        try:
            from app.ui.toast import show_toast
            show_toast(
                self.master,
                f"« {(self._track.get('title') or '?')[:40]} » enregistré",
                kind="success", duration_ms=2500)
        except Exception:
            pass
        self._on_close()
