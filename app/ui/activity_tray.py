"""
ActivityTray — floating widget in the top-left corner that lists
every currently-running background task plus recently-finished ones.

Subscribes to ``engine.tasks`` and rebuilds itself whenever the task
list changes. Auto-hides when the tray is empty; auto-shows when a
new task starts. Designed to be the single, always-on-screen feedback
channel for long-running work — replacing the per-job status labels
that the user could miss while on another page.

Layout: small dark card, max ~360 px wide, anchored top-left of the
main app window. Each task row shows:
    ▣ name                      [progress bar]   42 %  · ETA 2 min
    1-line message under the name (truncated)

The widget is mounted with ``place(x=12, y=58)`` — sits below the
sidebar header without overlapping the page content area.
"""
from __future__ import annotations

import threading
from typing import Optional

import customtkinter as ctk

from app.config import COLORS
from app.engine import tasks as task_registry


_TRAY_W = 360
_ROW_H = 56
_MAX_VISIBLE = 5


def _fmt_eta(seconds: Optional[float]) -> str:
    if not seconds or seconds < 1:
        return ""
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        m, s = divmod(s, 60)
        return f"{m}min{s:02d}"
    h, rem = divmod(s, 3600)
    m = rem // 60
    return f"{h}h{m:02d}"


def _color_for(status: str) -> str:
    return {
        "running":   COLORS["accent"],
        "done":      COLORS["success"],
        "error":     COLORS["error"],
        "cancelled": COLORS["warning"],
    }.get(status, COLORS["accent"])


class ActivityTray(ctk.CTkFrame):
    """Floating activity panel. Mount once on the App window."""

    def __init__(self, parent):
        super().__init__(parent, fg_color=COLORS["bg_card"],
                          corner_radius=10,
                          border_width=1,
                          border_color=COLORS["bg_input"])
        self._parent = parent
        self._row_widgets: dict[int, ctk.CTkFrame] = {}
        self._build()
        # Subscribe — task_registry calls back from worker threads, so
        # we marshal back to the UI thread via after(0, _refresh).
        task_registry.subscribe(self._on_tasks_changed)
        # First paint
        self._refresh()

    def _build(self):
        # Header strip (always visible when tray is shown)
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x", padx=10, pady=(8, 4))
        self._header_label = ctk.CTkLabel(
            head, text="Activité", font=ctk.CTkFont(size=11, weight="bold"),
            text_color=COLORS["accent"])
        self._header_label.pack(side="left")
        ctk.CTkButton(
            head, text="✕", width=20, height=20,
            font=ctk.CTkFont(size=10, weight="bold"),
            fg_color="transparent", hover_color=COLORS["bg_input"],
            text_color=COLORS["text_dim"],
            command=self._hide).pack(side="right")

        # Container that holds one row per task
        self._rows_box = ctk.CTkFrame(self, fg_color="transparent")
        self._rows_box.pack(fill="x", padx=8, pady=(0, 8))

    # ── Task-list subscription ─────────────────────────────────

    def _on_tasks_changed(self):
        # Worker thread → marshal back to UI
        try:
            self._parent.after(0, self._refresh)
        except Exception:
            pass

    def _refresh(self):
        active = task_registry.list_active()
        if not active:
            self._hide()
            return
        self._show()
        self._render(active)

    def _render(self, tasks):
        # Wipe all current rows then redraw — small list (<= 5), simpler
        # than diff-patching at this scale
        for w in self._rows_box.winfo_children():
            try:
                w.destroy()
            except Exception:
                pass
        self._row_widgets.clear()

        n_running = sum(1 for t in tasks if t.status == "running")
        if n_running:
            self._header_label.configure(
                text=f"Activité  ·  {n_running} en cours",
                text_color=COLORS["accent"])
        else:
            self._header_label.configure(
                text="Activité  ·  terminé",
                text_color=COLORS["success"])

        for t in tasks[:_MAX_VISIBLE]:
            self._draw_row(t)
        if len(tasks) > _MAX_VISIBLE:
            ctk.CTkLabel(
                self._rows_box,
                text=f"+ {len(tasks) - _MAX_VISIBLE} autre(s)…",
                font=ctk.CTkFont(size=10),
                text_color=COLORS["text_dim"]
            ).pack(anchor="w", padx=4, pady=(0, 2))

    def _draw_row(self, t):
        row = ctk.CTkFrame(self._rows_box,
                            fg_color=COLORS["bg_input"],
                            corner_radius=6)
        row.pack(fill="x", pady=2)

        # Top line: status icon + name + eta/percent on the right
        top = ctk.CTkFrame(row, fg_color="transparent")
        top.pack(fill="x", padx=8, pady=(4, 0))

        icon = {"running": "●", "done": "✓",
                "error": "✗", "cancelled": "■"}.get(t.status, "●")
        ctk.CTkLabel(
            top, text=icon, width=14,
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=_color_for(t.status)
        ).pack(side="left", padx=(0, 4))
        ctk.CTkLabel(
            top, text=t.name[:48],
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=COLORS["text"], anchor="w"
        ).pack(side="left", fill="x", expand=True)

        right = ""
        if t.status == "running" and 0 <= t.progress <= 1:
            right = f"{int(t.progress * 100)}%"
            if t.eta_s:
                right += f"  ·  {_fmt_eta(t.eta_s)}"
        elif t.status == "done":
            right = "fini"
        elif t.status == "error":
            right = "erreur"
        elif t.status == "cancelled":
            right = "annulé"
        if right:
            ctk.CTkLabel(
                top, text=right, font=ctk.CTkFont(size=10),
                text_color=COLORS["text_dim"]
            ).pack(side="right")
        # Per-task cancel button while running — clicking sets the
        # task's cancel_event so the worker bails out cleanly on its
        # next iteration check.
        if t.status == "running":
            ctk.CTkButton(
                top, text="✕", width=18, height=18,
                font=ctk.CTkFont(size=10, weight="bold"),
                fg_color="transparent",
                hover_color=COLORS["error"],
                text_color=COLORS["text_dim"],
                command=lambda tid=t.id: task_registry.cancel(tid)
            ).pack(side="right", padx=(2, 0))

        # Progress bar (only while running)
        if t.status == "running" and t.progress >= 0:
            bar = ctk.CTkProgressBar(
                row, height=4,
                fg_color=COLORS["bg_card"],
                progress_color=_color_for(t.status))
            bar.set(t.progress)
            bar.pack(fill="x", padx=8, pady=(2, 0))

        # Bottom message line
        if t.message:
            ctk.CTkLabel(
                row, text=t.message[:72],
                font=ctk.CTkFont(size=10),
                text_color=COLORS["text_dim"],
                anchor="w", justify="left"
            ).pack(fill="x", padx=8, pady=(0, 4))
        else:
            # Spacer to keep row height stable
            ctk.CTkLabel(row, text="", height=2).pack()

        self._row_widgets[t.id] = row

    # ── Show / hide ───────────────────────────────────────────

    def _show(self):
        # Sticky position: top-left of the content area. We're parented
        # to App.content (not App root), so x=12 is 12 px past the
        # sidebar edge regardless of DPI scaling — no more hardcoded
        # 210/224 px offsets that broke on 125 % CTk scaling.
        try:
            self.place(x=12, y=12, width=_TRAY_W)
            # Without lift() the tray is created BELOW the pages that
            # were packed earlier in App.__init__ — Tk's default
            # stacking order is creation-order, not z-index. lift()
            # raises us to the top so the floating panel actually
            # floats over whichever page is currently mounted.
            self.lift()
        except Exception:
            pass

    def _hide(self):
        try:
            self.place_forget()
        except Exception:
            pass

    def destroy(self):
        try:
            task_registry.unsubscribe(self._on_tasks_changed)
        except Exception:
            pass
        super().destroy()
