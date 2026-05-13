"""
Friendly UI helpers — readable error / info / confirm dialogs.

Replaces raw exception strings with messages a non-technical user can
understand. All texts are in French (the app's primary audience).
"""
from __future__ import annotations

import threading
from tkinter import messagebox

import customtkinter as ctk


# ── Font cache ─────────────────────────────────────────────────────
# CTkFont allocation isn't free — each instance creates a Tk font, queries
# metrics, and registers callbacks. A page that creates 20 ctk.CTkFont(size=12)
# objects ends up creating 20 identical Tk fonts. Cache them by signature.

_font_cache: dict[tuple, ctk.CTkFont] = {}


def font(size: int = 12, weight: str = "normal",
         family: str | None = None) -> ctk.CTkFont:
    """Return a cached CTkFont. Safe to call from anywhere — same args
    always return the same instance."""
    key = (size, weight, family or "")
    cached = _font_cache.get(key)
    if cached is not None:
        return cached
    if family:
        cached = ctk.CTkFont(family=family, size=size, weight=weight)
    else:
        cached = ctk.CTkFont(size=size, weight=weight)
    _font_cache[key] = cached
    return cached


# ── Lightweight hover tooltip ──────────────────────────────────────

def attach_tooltip(widget, text: str, *, delay_ms: int = 400) -> None:
    """Bind a tk-native tooltip to `widget`.

    Cheap (no extra widgets created until the user hovers, then a single
    Toplevel). Used for things like Sync Library where a 1-line
    explanation is plenty without cluttering the page.
    """
    import tkinter as tk
    from app.config import COLORS

    state = {"win": None, "after": None}

    def _show():
        if state["win"] is not None:
            return
        try:
            x = widget.winfo_rootx() + 10
            y = widget.winfo_rooty() + widget.winfo_height() + 4
        except Exception:
            return
        win = tk.Toplevel(widget)
        win.wm_overrideredirect(True)
        win.attributes("-topmost", True)
        try:
            win.attributes("-alpha", 0.95)
        except Exception:
            pass
        win.configure(bg=COLORS.get("bg_card", "#1a1a35"))
        lbl = tk.Label(
            win, text=text, justify="left",
            bg=COLORS.get("bg_card", "#1a1a35"),
            fg=COLORS.get("text", "#e0e0e0"),
            font=("Segoe UI", 9),
            padx=8, pady=4, bd=0,
            wraplength=320)
        lbl.pack()
        win.geometry(f"+{x}+{y}")
        state["win"] = win

    def _hide(_e=None):
        if state["after"] is not None:
            try:
                widget.after_cancel(state["after"])
            except Exception:
                pass
            state["after"] = None
        if state["win"] is not None:
            try:
                state["win"].destroy()
            except Exception:
                pass
            state["win"] = None

    def _on_enter(_e):
        _hide()
        state["after"] = widget.after(delay_ms, _show)

    widget.bind("<Enter>", _on_enter, add="+")
    widget.bind("<Leave>", _hide, add="+")
    widget.bind("<ButtonPress>", _hide, add="+")


# ── UI rate-limiter ────────────────────────────────────────────────

class UiThrottle:
    """Coalesce high-frequency callbacks into at most 1 per `interval_ms`.

    Use case: a worker thread fires status/progress updates dozens of times
    per second. Without throttling each `after(0, ...)` queues a Tk event
    and the mainloop can't keep up with input. With throttling only the
    *latest* callback runs per time window, so the UI stays responsive
    and the user only sees the freshest value anyway.

    Thread-safe: `call()` may be invoked from any thread.

    Usage:
        self._status_throttle = UiThrottle(self, interval_ms=100)
        # in worker thread:
        self._status_throttle.call(lambda: self.status_label.configure(text=...))
    """

    def __init__(self, widget, interval_ms: int = 100):
        self._widget = widget
        self._interval_ms = interval_ms
        self._lock = threading.Lock()
        self._pending = None
        self._scheduled = False

    def call(self, fn) -> None:
        """Queue `fn` to run on the UI thread; only latest within window wins."""
        with self._lock:
            self._pending = fn
            if self._scheduled:
                return
            self._scheduled = True
        try:
            self._widget.after(self._interval_ms, self._fire)
        except Exception:
            # Widget is being destroyed — drop the callback
            with self._lock:
                self._pending = None
                self._scheduled = False

    def call_now(self, fn) -> None:
        """Run on the UI thread immediately (still via `after(0)` for safety),
        bypassing the throttle. Use for terminal events (final status)."""
        try:
            self._widget.after(0, fn)
        except Exception:
            pass

    def _fire(self) -> None:
        with self._lock:
            fn = self._pending
            self._pending = None
            self._scheduled = False
        if fn is None:
            return
        try:
            fn()
        except Exception:
            # UI may be torn down between schedule and fire
            pass


# ── Dialog helpers ─────────────────────────────────────────────────


def info(title: str, message: str) -> None:
    messagebox.showinfo(title, message)


def warn(title: str, message: str) -> None:
    messagebox.showwarning(title, message)


def error(title: str, message: str, *, detail: str | None = None) -> None:
    """Show a friendly error. `detail` is appended in a small footer."""
    full = message
    if detail:
        full += f"\n\nDétails techniques :\n{detail[:300]}"
    messagebox.showerror(title, full)


def confirm(title: str, question: str, *, yes_label: str = "Oui",
            no_label: str = "Non") -> bool:
    return messagebox.askyesno(title, question)


# ── Translation of common errors ────────────────────────────────

_FRIENDLY = {
    "spotify credentials": (
        "Spotify non configuré",
        "Va dans Settings et entre tes Client ID et Client Secret Spotify "
        "pour activer cette fonction.\n\n"
        "Tu peux les obtenir gratuitement sur developer.spotify.com."),
    "ffmpeg": (
        "FFmpeg introuvable",
        "FFmpeg est nécessaire pour convertir l'audio. Installe-le ou "
        "indique son chemin dans Settings → FFmpeg path."),
    "node": (
        "Node.js manquant",
        "Node.js est requis pour télécharger depuis YouTube. "
        "Installe-le depuis nodejs.org puis relance Ultimate DJ."),
    "404": (
        "Lien introuvable",
        "Le lien donné n'est plus disponible. Vérifie-le et réessaie."),
    "stopped by user": (
        "Téléchargement arrêté",
        "L'opération a bien été interrompue."),
}


def explain(raw: str) -> tuple[str, str]:
    """Return (title, message) for a friendly version of a raw error."""
    low = raw.lower()
    for key, (title, msg) in _FRIENDLY.items():
        if key in low:
            return title, msg
    return "Erreur", raw[:300] if raw else "Une erreur inconnue est survenue."
