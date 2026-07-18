"""Live page — real-time now-playing from Rekordbox + next-track
suggestions. The page only renders LiveSession.snapshot(); all the
Rekordbox polling happens in the engine's daemon thread."""
from __future__ import annotations

import customtkinter as ctk

from app.config import COLORS
from app.engine.live import LiveSession

_REFRESH_MS = 2000


class LivePage(ctk.CTkFrame):
    def __init__(self, parent):
        super().__init__(parent, fg_color="transparent")
        self.session = LiveSession()
        self._last_render = None

        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x", padx=16, pady=(14, 6))
        ctk.CTkLabel(head, text="🔴 MODE LIVE",
                     font=ctk.CTkFont(size=22, weight="bold"),
                     text_color=COLORS["accent"]).pack(side="left")
        self.btn = ctk.CTkButton(head, text="▶ Démarrer la session",
                                 width=190, fg_color=COLORS["accent"],
                                 command=self._toggle)
        self.btn.pack(side="right")
        self.status = ctk.CTkLabel(
            head, text="Prêt — lance Rekordbox et joue.",
            font=ctk.CTkFont(size=11), text_color=COLORS["text_dim"])
        self.status.pack(side="right", padx=12)

        ctk.CTkLabel(self, text="EN LECTURE",
                     font=ctk.CTkFont(size=10, weight="bold"),
                     text_color=COLORS["text_dim"]).pack(
            anchor="w", padx=18)
        self.now = ctk.CTkLabel(self, text="—",
                                font=ctk.CTkFont(size=18, weight="bold"),
                                text_color=COLORS["text"], anchor="w")
        self.now.pack(fill="x", padx=18, pady=(0, 8))

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=16, pady=(0, 12))
        body.grid_columnconfigure(0, weight=3)
        body.grid_columnconfigure(1, weight=2)
        body.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(body, text="À JOUER ENSUITE (IA)",
                     font=ctk.CTkFont(size=10, weight="bold"),
                     text_color=COLORS["text_dim"]).grid(
            row=0, column=0, sticky="w")
        self.sugg_frame = ctk.CTkScrollableFrame(
            body, fg_color=COLORS["bg_card"], corner_radius=8)
        self.sugg_frame.grid(row=1, column=0, sticky="nsew",
                             padx=(0, 8))

        ctk.CTkLabel(body, text="SET EN COURS",
                     font=ctk.CTkFont(size=10, weight="bold"),
                     text_color=COLORS["text_dim"]).grid(
            row=0, column=1, sticky="w")
        self.timeline = ctk.CTkTextbox(
            body, fg_color=COLORS["bg_card"],
            text_color=COLORS["text"],
            font=ctk.CTkFont(size=11), wrap="none")
        self.timeline.grid(row=1, column=1, sticky="nsew")
        self.timeline.configure(state="disabled")

        self.after(_REFRESH_MS, self._tick)

    # ── Actions ─────────────────────────────────────────────────

    def _toggle(self):
        if self.session.is_running():
            self.session.stop()
            self.btn.configure(text="▶ Démarrer la session",
                               fg_color=COLORS["accent"])
            self.status.configure(text="Session arrêtée.")
        else:
            self.session.start()
            self.btn.configure(text="⏹ Arrêter",
                               fg_color=COLORS["error"])
            self.status.configure(
                text="Détection en cours (~1 min de latence Rekordbox)…")

    # ── Rendering loop ──────────────────────────────────────────

    def _tick(self):
        try:
            if self.session.is_running() or self._last_render is None:
                snap = self.session.snapshot()
                if snap != self._last_render:
                    self._render(snap)
                    self._last_render = snap
        except Exception:
            pass
        self.after(_REFRESH_MS, self._tick)

    def _render(self, snap: dict):
        if snap.get("error"):
            self.status.configure(text=f"⚠ {snap['error']}",
                                  text_color=COLORS["warning"])
        elif snap.get("active"):
            n = len(snap.get("played") or [])
            self.status.configure(
                text=f"{snap.get('history') or 'session'} · {n} joués",
                text_color=COLORS["success"])
        self.now.configure(text=snap.get("current") or "—")

        for w in self.sugg_frame.winfo_children():
            w.destroy()
        for i, s in enumerate(snap.get("suggestions") or [], 1):
            row = ctk.CTkFrame(self.sugg_frame, fg_color="transparent")
            row.pack(fill="x", padx=6, pady=2)
            ctk.CTkLabel(row, text=f"{i:2d}", width=24,
                         font=ctk.CTkFont(size=11),
                         text_color=COLORS["text_dim"]).pack(side="left")
            ctk.CTkLabel(row, text=s["title"][:52], anchor="w",
                         font=ctk.CTkFont(size=12),
                         text_color=COLORS["text"]).pack(
                side="left", fill="x", expand=True)
            ctk.CTkLabel(
                row,
                text=f"{s['bpm']:.0f} · {s['camelot']} · {s['score']:.0f}",
                font=ctk.CTkFont(size=11),
                text_color=COLORS["accent"]).pack(side="right")

        self.timeline.configure(state="normal")
        self.timeline.delete("1.0", "end")
        for i, p in enumerate(snap.get("played") or [], 1):
            mark = "" if p.get("matched") else "  (hors bibliothèque)"
            self.timeline.insert("end", f"{i:2d}. {p['title']}{mark}\n")
        self.timeline.configure(state="disabled")
