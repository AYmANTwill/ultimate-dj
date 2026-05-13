"""
Home page — actionable dashboard for DJ workflow.

Shows what actually matters before a set:
- Total tracks in the library
- Tracks not yet rated (need triage)
- Duplicates to clean
- Most recent imports (with quick-jump to Library)

The previous "averages" (avg BPM, avg energy, dominant key) have been
removed — averaging across a mixed-genre library produces meaningless
numbers. Recent imports + un-rated count is what a working DJ actually
checks before prep.
"""
from __future__ import annotations

import threading

import customtkinter as ctk

from app.config import COLORS, load_config
from app.engine.library import (
    get_connection, all_tracks, duplicate_count,
    recent_tracks, unrated_count,
)
from app.ui.fastlist import FastList
from app.ui.helpers import font


def _format_duration(seconds: float | None) -> str:
    s = int(seconds or 0)
    return f"{s // 60}:{s % 60:02d}"


class HomePage(ctk.CTkFrame):

    def __init__(self, parent):
        super().__init__(parent, fg_color=COLORS["bg_dark"])
        self._folder_card = None
        self._build_ui()

    def _build_ui(self):
        # Greeting ──────────────────────────────────────────────────
        ctk.CTkLabel(
            self, text="Ultimate DJ",
            font=font(30, "bold"),
            text_color=COLORS["accent"]
        ).pack(anchor="w", padx=30, pady=(28, 0))
        ctk.CTkLabel(
            self, text="Tableau de bord — prépare tes sets",
            font=font(13), text_color=COLORS["text_dim"]
        ).pack(anchor="w", padx=30, pady=(0, 18))

        # Stats grid (4 actionable cards) ───────────────────────────
        self.stats_grid = ctk.CTkFrame(self, fg_color="transparent")
        self.stats_grid.pack(fill="x", padx=30, pady=(0, 12))

        # Quick actions ─────────────────────────────────────────────
        ctk.CTkLabel(
            self, text="Actions rapides",
            font=font(13, "bold"),
            text_color=COLORS["text_dim"]
        ).pack(anchor="w", padx=30, pady=(8, 6))

        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.pack(fill="x", padx=30)

        for label, target, color in [
            ("Synchroniser ma bibliothèque", "Library",  COLORS["accent"]),
            ("Analyser un dossier",          "Analyze",  COLORS["accent2"]),
            ("Découvrir des morceaux",       "Discover", COLORS["success"]),
            ("Construire un setlist",        "Setlist",  COLORS["warning"]),
        ]:
            ctk.CTkButton(
                actions, text=label, height=60, width=210,
                font=font(13, "bold"),
                fg_color=color, hover_color=color,
                text_color=COLORS["bg_dark"],
                command=lambda t=target: self._goto(t),
            ).pack(side="left", padx=6, pady=4)

        # Recent imports ────────────────────────────────────────────
        ctk.CTkLabel(
            self, text="Derniers imports",
            font=font(13, "bold"),
            text_color=COLORS["text_dim"]
        ).pack(anchor="w", padx=30, pady=(20, 6))

        self.recent_table = FastList(
            self,
            [("title",   "Title",    400),
             ("bpm",     "BPM",       70),
             ("camelot", "Camelot",   80),
             ("rating",  "Rating",    80),
             ("dur",     "Duration",  80)],
            sortable=False,
            height=8,
        )
        self.recent_table.pack(fill="x", padx=30, pady=(0, 16))

    def _goto(self, page_name: str):
        top = self.winfo_toplevel()
        if hasattr(top, "_switch_page"):
            top._switch_page(page_name)

    def on_show(self):
        # Paint placeholders instantly, fetch real numbers off-thread
        self._render_cards([
            ("Morceaux",          "…", COLORS["accent"]),
            ("Non notés",         "…", COLORS["warning"]),
            ("Doublons",          "…", COLORS["text_dim"]),
            ("Dossier musique",   "…", COLORS["accent2"]),
        ])
        threading.Thread(target=self._compute_stats, daemon=True).start()

    def _compute_stats(self):
        try:
            conn = get_connection()
            total = len(all_tracks(conn))
            unrated = unrated_count(conn)
            dups = duplicate_count(conn)
            recent = recent_tracks(conn, limit=10)
        except Exception:
            total = unrated = dups = 0
            recent = []

        music_root = load_config().get("music_root", "")
        root_short = "(non défini)" if not music_root else (
            "…" + music_root[-22:] if len(music_root) > 24 else music_root)

        cards = [
            ("Morceaux", f"{total}", COLORS["accent"]),
            ("Non notés",
                f"{unrated}",
                COLORS["warning"] if unrated else COLORS["text_dim"]),
            ("Doublons",
                f"{dups}",
                COLORS["error"] if dups else COLORS["text_dim"]),
            ("Dossier musique", root_short, COLORS["accent2"]),
        ]
        self.after(0, lambda c=cards, r=recent: (
            self._render_cards(c), self._render_recent(r)))

    def _render_cards(self, cards):
        for w in self.stats_grid.winfo_children():
            w.destroy()
        for label, value, color in cards:
            self._stat_card(label, value, color)

    def _render_recent(self, tracks: list[dict]):
        rows = []
        for t in tracks:
            stars = "★" * int(t.get("rating") or 0) or "—"
            rows.append((
                (t["title"] or "?")[:80],
                f"{(t['bpm'] or 0):.0f}",
                t["camelot"] or "?",
                stars,
                _format_duration(t.get("duration")),
            ))
        self.recent_table.set_rows(rows)

    def _stat_card(self, label: str, value: str, color: str):
        card = ctk.CTkFrame(self.stats_grid, fg_color=COLORS["bg_card"],
                             corner_radius=12, width=210, height=86)
        card.pack(side="left", padx=6, pady=4, fill="y")
        card.pack_propagate(False)
        ctk.CTkLabel(card, text=label,
                     font=font(11),
                     text_color=COLORS["text_dim"]
                     ).pack(anchor="w", padx=14, pady=(10, 0))
        # Use smaller font for long string values (e.g. folder path)
        size = 14 if len(value) > 8 else 26
        ctk.CTkLabel(card, text=value,
                     font=font(size, "bold"),
                     text_color=color,
                     anchor="w"
                     ).pack(anchor="w", padx=14, pady=(0, 10), fill="x")
