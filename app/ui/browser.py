"""
Browser page — embeds Edge WebView2 (via pywebview) directly inside the
Tk window using Win32 SetParent reparenting. This gives a true in-app
browser with full JS support (Spotify, YouTube, SoundCloud, etc.).

How it works:
1. The page creates a container Frame whose HWND we fetch via winfo_id().
2. Clicking a service spawns a pywebview subprocess with a unique title.
3. Once the WebView2 window appears, FindWindowW returns its HWND.
4. SetWindowLong strips the caption/border, SetParent reparents it under
   our container, MoveWindow sizes it to fill.
5. <Configure> events on the container resize the embedded view.
"""
from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
import time
import uuid
import webbrowser
from ctypes import wintypes
from pathlib import Path
from tkinter import messagebox

import customtkinter as ctk

from app.config import COLORS
from app.ui import helpers


SERVICES = [
    ("SoundCloud", "https://soundcloud.com/discover", "#ff5500"),
    ("Spotify",    "https://open.spotify.com/",       "#1DB954"),
    ("YouTube",    "https://www.youtube.com/",        "#ff0000"),
    ("1001Tracklists", "https://www.1001tracklists.com/", "#ffa500"),
]


# ── Win32 helpers (no-op on non-Windows) ────────────────────────────

_IS_WIN = sys.platform.startswith("win")

if _IS_WIN:
    _user32 = ctypes.windll.user32
    _user32.FindWindowW.restype = wintypes.HWND
    _user32.SetParent.restype = wintypes.HWND
    _user32.GetWindowLongW.restype = ctypes.c_long
    _user32.SetWindowLongW.restype = ctypes.c_long
    _user32.GetClientRect.argtypes = [wintypes.HWND,
                                       ctypes.POINTER(wintypes.RECT)]
    _user32.GetClientRect.restype = ctypes.c_int

    GWL_STYLE = -16
    WS_CHILD = 0x40000000
    WS_VISIBLE = 0x10000000
    WS_OVERLAPPEDWINDOW = 0x00CF0000


def _find_hwnd_by_title(title: str) -> int:
    if not _IS_WIN:
        return 0
    return _user32.FindWindowW(None, title) or 0


def _reparent(child_hwnd: int, parent_hwnd: int):
    """Strip frame chrome from `child_hwnd` and reparent it under `parent_hwnd`."""
    if not _IS_WIN or not child_hwnd or not parent_hwnd:
        return
    style = _user32.GetWindowLongW(child_hwnd, GWL_STYLE)
    new_style = (style & ~WS_OVERLAPPEDWINDOW) | WS_CHILD | WS_VISIBLE
    _user32.SetWindowLongW(child_hwnd, GWL_STYLE, new_style)
    _user32.SetParent(child_hwnd, parent_hwnd)


def _client_rect(hwnd: int) -> tuple[int, int]:
    """Ask Windows directly for the HWND's client-area size.

    Tk's winfo_width/height returns *logical* pixels. On a high-DPI
    monitor (125% / 150%) those don't match the parent HWND's real
    pixel coordinate system, so MoveWindow with Tk values produces an
    embed that overflows the host frame. GetClientRect returns the
    actual pixel dimensions in the parent's coord system — exactly
    what MoveWindow needs.
    """
    if not _IS_WIN or not hwnd:
        return (0, 0)
    rect = wintypes.RECT()
    if not _user32.GetClientRect(hwnd, ctypes.byref(rect)):
        return (0, 0)
    return (rect.right - rect.left, rect.bottom - rect.top)


def _fit(child_hwnd: int, w: int, h: int):
    if not _IS_WIN or not child_hwnd or w <= 0 or h <= 0:
        return
    _user32.MoveWindow(child_hwnd, 0, 0, w, h, True)


# ── Subprocess launcher ────────────────────────────────────────────

def _profile_dir() -> Path:
    """Persistent WebView2 user data folder — keeps logins between launches."""
    project_root = Path(__file__).resolve().parent.parent.parent
    p = project_root / "data" / "browser_profile"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _launch_webview(url: str, title: str) -> subprocess.Popen | None:
    """Spawn a pywebview window in a separate process. Returns the Popen or None."""
    project_root = Path(__file__).resolve().parent.parent.parent
    log_path = project_root / "data" / "browser.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    creationflags = 0
    if _IS_WIN:
        creationflags = subprocess.CREATE_NO_WINDOW
    frozen = bool(getattr(sys, "frozen", False))
    try:
        if not frozen:
            # First check pywebview is importable (probe in a subprocess
            # so a broken install can't take down the main app). In the
            # packaged exe webview is bundled — no probe possible or
            # needed, and `-c` wouldn't work anyway.
            check = subprocess.run(
                [sys.executable, "-c", "import webview"],
                capture_output=True, timeout=8,
                creationflags=creationflags,
            )
            if check.returncode != 0:
                return None
        storage = str(_profile_dir())
        if frozen:
            # The packaged exe can't run `-m app.ui._browser_launcher`
            # (sys.executable IS the app, not python) — it re-launches
            # ITSELF with a sentinel argv that run.py routes to the
            # launcher before booting the GUI.
            cmd = [sys.executable, "--browser-launcher", url, title, storage]
        else:
            cmd = [sys.executable, "-m", "app.ui._browser_launcher",
                   url, title, storage]
        return subprocess.Popen(
            cmd,
            cwd=str(project_root),
            stdout=open(log_path, "ab"), stderr=subprocess.STDOUT,
            creationflags=creationflags,
        )
    except Exception:
        return None


# ── BrowserPanel ───────────────────────────────────────────────────
# Re-usable embeddable browser. Used to be a top-level page; now lives
# inside the Download page. The `on_url_pick` callback is invoked when
# the user clicks "Coller URL" — Download wires it to fill its URL field.


class BrowserPanel(ctk.CTkFrame):
    def __init__(self, parent, *, on_url_pick=None, compact: bool = False):
        super().__init__(parent, fg_color=COLORS["bg_dark"])
        self._proc: subprocess.Popen | None = None
        self._embedded_hwnd: int = 0
        self._embed_title: str = ""
        self._current_url: str = ""
        self._on_url_pick = on_url_pick
        self._compact = compact
        self._build_ui()
        self.bind("<Destroy>", self._on_destroy)

    def _build_ui(self):
        # ── Toolbar ────────────────────────────────────────────
        toolbar = ctk.CTkFrame(self, fg_color=COLORS["bg_sidebar"], corner_radius=0)
        toolbar.pack(fill="x")

        svc_row = ctk.CTkFrame(toolbar, fg_color="transparent")
        svc_row.pack(fill="x", padx=8, pady=(6, 2) if self._compact else (8, 4))

        if not self._compact:
            ctk.CTkLabel(svc_row, text="Browser",
                         font=ctk.CTkFont(size=18, weight="bold"),
                         text_color=COLORS["accent"]).pack(side="left",
                                                            padx=(4, 16))

        # Compact mode shrinks the service buttons to fit the Download page
        btn_w = 92 if self._compact else 110
        btn_h = 26 if self._compact else 30
        for name, url, color in SERVICES:
            ctk.CTkButton(
                svc_row, text=name, height=btn_h, width=btn_w,
                font=ctk.CTkFont(size=10 if self._compact else 11,
                                  weight="bold"),
                fg_color=color, hover_color=color, text_color="white",
                command=lambda u=url, n=name: self._navigate(u, n),
            ).pack(side="left", padx=2)

        url_row = ctk.CTkFrame(toolbar, fg_color="transparent")
        url_row.pack(fill="x", padx=8, pady=(2, 6) if self._compact else (0, 8))

        self.url_entry = ctk.CTkEntry(
            url_row, placeholder_text="https://… ou recherche libre",
            fg_color=COLORS["bg_input"], border_color=COLORS["bg_input"],
            text_color=COLORS["text"], height=26 if self._compact else 28,
        )
        self.url_entry.pack(side="left", fill="x", expand=True, padx=(0, 4))
        self.url_entry.bind("<Return>", lambda _e: self._go_from_entry())

        ctk.CTkButton(
            url_row, text="Aller", width=60, height=26 if self._compact else 28,
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
            text_color=COLORS["bg_dark"],
            command=self._go_from_entry,
        ).pack(side="left", padx=2)

        # New: send the embed's current URL back to whoever opened us
        # (in the Download page that's the main URL field).
        if self._on_url_pick is not None:
            ctk.CTkButton(
                url_row, text="↑ Coller dans URL",
                width=130, height=26 if self._compact else 28,
                font=ctk.CTkFont(size=11, weight="bold"),
                fg_color=COLORS["accent2"], hover_color="#e0356f",
                text_color=COLORS["on_accent2"],
                command=self._send_url_to_caller,
            ).pack(side="left", padx=2)

        ctk.CTkButton(
            url_row, text="Fermer", width=60,
            height=26 if self._compact else 28,
            font=ctk.CTkFont(size=11),
            fg_color=COLORS["bg_input"], hover_color=COLORS["error"],
            text_color=COLORS["text"],
            command=self._close_embedded,
        ).pack(side="right", padx=2)

        # Manual safety valve — if the embed somehow ends up the wrong
        # size (DPI weirdness, a Configure event we missed), the user
        # can click this to force a re-fit. Beats restarting the app.
        ctk.CTkButton(
            url_row, text="⇲ Ajuster", width=80,
            height=26 if self._compact else 28,
            font=ctk.CTkFont(size=10),
            fg_color=COLORS["bg_input"], hover_color=COLORS["accent"],
            text_color=COLORS["text"],
            command=self._fit_now,
        ).pack(side="right", padx=2)

        if not self._compact:
            ctk.CTkButton(
                url_row, text="Effacer session", width=110, height=28,
                font=ctk.CTkFont(size=11),
                fg_color=COLORS["bg_input"], hover_color=COLORS["warning"],
                text_color=COLORS["text"],
                command=self._wipe_profile,
            ).pack(side="right", padx=2)

        # ── Embed container ────────────────────────────────────
        self.embed_host = ctk.CTkFrame(
            self, fg_color=COLORS["bg_card"], corner_radius=0)
        self.embed_host.pack(fill="both", expand=True)
        self._show_placeholder()

        self.embed_host.bind("<Configure>", self._on_resize)
        # Also refit when the whole app window resizes — without this
        # the embed only repositions on its own immediate-parent's
        # <Configure>, which the user can miss when dragging the main
        # window between monitors with different DPI.
        try:
            self.winfo_toplevel().bind(
                "<Configure>", self._on_toplevel_resize, add="+")
        except Exception:
            pass

    def _show_placeholder(self):
        for w in self.embed_host.winfo_children():
            w.destroy()
        wrap = ctk.CTkFrame(self.embed_host, fg_color="transparent")
        wrap.pack(expand=True)
        ctk.CTkLabel(
            wrap, text="Browser intégré",
            font=ctk.CTkFont(size=18 if self._compact else 22, weight="bold"),
            text_color=COLORS["accent"]).pack(pady=(0, 6))
        if self._on_url_pick is not None:
            tip = ("Cherche un morceau ou une playlist sur les services "
                   "ci-dessus, puis « ↑ Coller dans URL » envoie le lien "
                   "dans le champ URL pour téléchargement.")
        else:
            tip = ("Choisis un service ci-dessus ou tape une URL.\n"
                   "La fenêtre Edge WebView2 s'embarquera dans cette zone.")
        ctk.CTkLabel(
            wrap, text=tip,
            font=ctk.CTkFont(size=11 if self._compact else 12),
            text_color=COLORS["text_dim"],
            justify="center", wraplength=520).pack()
        ctk.CTkLabel(
            wrap,
            text="✓ Sessions persistantes — tes logins Spotify / SoundCloud / "
                 "YouTube sont gardés d'une fois à l'autre.",
            font=ctk.CTkFont(size=10 if self._compact else 11),
            text_color=COLORS["success"],
            justify="center", wraplength=520).pack(pady=(10, 0))

    # ── Navigation ─────────────────────────────────────────────

    def _navigate(self, url: str, label: str = "Web"):
        if not _IS_WIN:
            webbrowser.open(url)
            return

        self._current_url = url
        self.url_entry.delete(0, "end")
        self.url_entry.insert(0, url)

        # Tear down any existing embed
        self._close_embedded()

        # Clear placeholder, ensure host updates so winfo_id() is final
        for w in self.embed_host.winfo_children():
            w.destroy()
        self.update_idletasks()

        host_hwnd = self.embed_host.winfo_id()
        self._embed_title = f"UltimateDJ_Embed_{uuid.uuid4().hex[:8]}"
        self._proc = _launch_webview(url, self._embed_title)
        if self._proc is None:
            self._show_install_prompt()
            return

        # Poll for the WebView2 window to appear, then reparent
        self._poll_for_window(host_hwnd, attempt=0)

    def _poll_for_window(self, host_hwnd: int, attempt: int):
        """Poll up to ~15s for the pywebview window, then reparent it."""
        if self._proc is None:
            return
        if self._proc.poll() is not None:
            self._show_error("La fenêtre intégrée s'est fermée. "
                              "Voir data/browser.log pour les détails.")
            return

        hwnd = _find_hwnd_by_title(self._embed_title)
        if hwnd:
            _reparent(hwnd, host_hwnd)
            self._embedded_hwnd = hwnd
            # Initial fit, then a small burst of re-fits so we catch any
            # layout that wasn't settled at reparent time. Without these,
            # if the host frame's <Configure> doesn't fire after the first
            # fit (common on the very first page switch), the embed stays
            # stuck at whatever the host's transitional size was — which
            # is what hid the Spotify playback bar.
            self._fit_now()
            # Heavy SPAs like SoundCloud / Spotify keep loading
            # post-DOMContentLoaded — their bottom playback bar can
            # materialize 3-5s in. Spread re-fits across the first
            # 8 s so we catch them no matter when they render.
            for delay in (50, 200, 600, 1500, 3000, 5000, 8000):
                self.after(delay, self._fit_now)
            return

        if attempt >= 60:  # 60 * 250ms = 15s
            self._show_error("La fenêtre Edge WebView2 a mis trop de temps à apparaître.")
            return
        self.after(250, lambda: self._poll_for_window(host_hwnd, attempt + 1))

    def _fit_now(self):
        if not self._embedded_hwnd:
            return
        # Force pending geometry to settle before reading the host size.
        # Without this the very first fit can read a partially-built
        # layout — particularly on the initial page switch.
        try:
            self.embed_host.update_idletasks()
        except Exception:
            pass
        # Query the parent HWND directly (now that the process is DPI-aware
        # via app/__init__.py, GetClientRect matches the WebView2 child's
        # coordinate system exactly — Spotify's `position: fixed; bottom: 0`
        # playback bar lands at the bottom of the visible viewport).
        # Fall back to Tk dims only if Win32 returns zero.
        host_hwnd = self.embed_host.winfo_id()
        w, h = _client_rect(host_hwnd)
        if w <= 0 or h <= 0:
            w = max(self.embed_host.winfo_width(), 1)
            h = max(self.embed_host.winfo_height(), 1)
        _fit(self._embedded_hwnd, w, h)

    def _on_resize(self, _event=None):
        self._fit_now()

    def _on_toplevel_resize(self, _event=None):
        # Throttle: only refit if there is actually an embed and we
        # haven't refit in the last 80 ms. Without this, dragging the
        # window edge would call MoveWindow on every WM_SIZE message
        # and the embed would flicker.
        if not self._embedded_hwnd:
            return
        import time as _t
        now = _t.monotonic()
        last = getattr(self, "_last_refit_ts", 0.0)
        if now - last < 0.08:
            return
        self._last_refit_ts = now
        self._fit_now()

    def _go_from_entry(self):
        url = self.url_entry.get().strip()
        if not url:
            return
        if not url.startswith(("http://", "https://")):
            url = f"https://www.google.com/search?q={url.replace(' ', '+')}"
        self._navigate(url, "URL")

    def _send_url_to_caller(self):
        """Push the last URL we navigated to back to whoever opened us.

        Note: this is the URL we *sent* to the embed, not the page the
        user may have clicked through to inside the embed. For Spotify
        playlist links (the killer use-case for Download) that's exactly
        what you want — the user navigates to a playlist, hits this
        button, the playlist URL lands in the Download field.
        """
        if not self._on_url_pick:
            return
        # Prefer the URL bar (covers user-typed URLs) over the last
        # programmatic navigation
        typed = self.url_entry.get().strip()
        url = typed or self._current_url
        if url:
            try:
                self._on_url_pick(url)
            except Exception:
                pass

    def _close_embedded(self):
        if self._proc is not None:
            try:
                self._proc.terminate()
            except Exception:
                pass
            self._proc = None
        self._embedded_hwnd = 0
        self._embed_title = ""
        # Repaint placeholder
        for w in self.embed_host.winfo_children():
            try:
                w.destroy()
            except Exception:
                pass
        self._show_placeholder()

    def _show_error(self, message: str):
        self._close_embedded()
        for w in self.embed_host.winfo_children():
            w.destroy()
        wrap = ctk.CTkFrame(self.embed_host, fg_color="transparent")
        wrap.pack(expand=True)
        ctk.CTkLabel(
            wrap, text="Affichage intégré indisponible",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color=COLORS["warning"]).pack(pady=(0, 10))
        ctk.CTkLabel(
            wrap, text=message,
            font=ctk.CTkFont(size=12),
            text_color=COLORS["text_dim"],
            justify="center", wraplength=560).pack(pady=(0, 8))
        ctk.CTkButton(
            wrap, text="Ouvrir dans le navigateur système",
            command=lambda: webbrowser.open(self._current_url or "https://www.google.com"),
            fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
            text_color=COLORS["bg_dark"],
        ).pack(pady=8)

    def _show_install_prompt(self):
        self._show_error(
            "pywebview n'est pas installé pour ce Python.\n\n"
            "Ferme l'app et relance — le setup auto installera "
            "pywebview au prochain démarrage."
        )

    def _wipe_profile(self):
        """Delete the WebView2 user data folder — logs out of every service."""
        if not messagebox.askyesno(
                "Effacer la session ?",
                "Cela va te déconnecter de Spotify, SoundCloud, YouTube "
                "et 1001Tracklists. Continuer ?"):
            return

        # Must close the embed first — Edge holds locks on its profile files
        self._close_embedded()
        profile = _profile_dir()

        # Give WebView2 a moment to release its file handles
        def _do_wipe(attempt: int = 0):
            try:
                if profile.exists():
                    shutil.rmtree(profile, ignore_errors=False)
                profile.mkdir(parents=True, exist_ok=True)
                messagebox.showinfo(
                    "Session effacée",
                    "Tu es déconnecté de tous les services.")
            except Exception as e:
                if attempt < 4:
                    self.after(400, lambda: _do_wipe(attempt + 1))
                else:
                    messagebox.showwarning(
                        "Effacement partiel",
                        f"Impossible de tout effacer (fichier verrouillé) :\n{e}\n\n"
                        f"Ferme l'app et supprime à la main :\n{profile}")

        self.after(300, _do_wipe)

    def _on_destroy(self, _event=None):
        self._close_embedded()


# Backwards-compat alias for code that still references BrowserPage
# (none in the current tree, but keeps the import surface stable).
class BrowserPage(BrowserPanel):
    pass
