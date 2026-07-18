"""
Main application window with grouped sidebar navigation.
Dark DJ-themed UI using CustomTkinter.
"""
from __future__ import annotations

import customtkinter as ctk

from app.config import COLORS, apply_theme

# Only HomePage is imported eagerly — it's the default landing page
# and we want the window to paint as fast as possible. Every other
# page is loaded on first navigation via _import_page() below. Saves
# 3-5 s at boot because the heaviest modules (mixer pulls sounddevice
# + player, download pulls yt_dlp transitively) only pay their cost
# when the user actually clicks the sidebar entry.
from app.ui.home import HomePage


ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


# (sidebar_label, "module_path", "ClassName")
# The class is resolved on first switch. Pages live in their own files
# so the lazy import is a one-liner.
SIDEBAR_GROUPS_LAZY: list[tuple[str, list[tuple[str, str, str]]]] = [
    ("HOME", [
        ("Home",      "app.ui.home",     "HomePage"),
    ]),
    ("LIBRARY", [
        ("Download",  "app.ui.download", "DownloadPage"),
        ("Library",   "app.ui.library",  "LibraryPage"),
        ("Analyze",   "app.ui.analyze",  "AnalyzePage"),
    ]),
    ("MIX", [
        ("Mixer",     "app.ui.mixer",    "MixerPage"),
        ("Live",      "app.ui.live",     "LivePage"),
        ("Setlist",   "app.ui.setlist",  "SetlistPage"),
    ]),
    ("DISCOVER", [
        ("Discover",  "app.ui.discover", "DiscoverPage"),
    ]),
    ("CONFIG", [
        ("Settings",  "app.ui.settings", "SettingsPage"),
    ]),
]


# Backwards-compatible shape for any code that walked SIDEBAR_GROUPS
# expecting the actual class. Resolves Home eagerly, the rest stay
# as lazy markers (importlib.import_module on first switch_page).
class _LazyPage:
    """Placeholder that imports the real page class on first call."""
    def __init__(self, module: str, name: str):
        self._module = module
        self._name = name
        self._cls = None

    def __call__(self, *args, **kwargs):
        if self._cls is None:
            import importlib
            self._cls = getattr(
                importlib.import_module(self._module), self._name)
        return self._cls(*args, **kwargs)


SIDEBAR_GROUPS: list[tuple[str, list[tuple[str, type]]]] = [
    (section, [
        (label,
         HomePage if name == "HomePage" and mod == "app.ui.home"
         else _LazyPage(mod, name))
        for (label, mod, name) in items
    ])
    for section, items in SIDEBAR_GROUPS_LAZY
]


class App(ctk.CTk):
    WIDTH = 1200
    HEIGHT = 750

    def __init__(self):
        super().__init__()
        apply_theme()  # ensure COLORS is loaded from config
        # Move any legacy plaintext Spotify creds from config.json into
        # the Windows Credential Manager. Idempotent — safe every boot.
        try:
            from app.secrets_store import ensure_migrated
            ensure_migrated()
        except Exception:
            pass
        # Daily DB safety net — VACUUM INTO is fast and never blocks
        # readers, so doing this in the foreground is fine.
        try:
            from app.engine.backup import maybe_snapshot
            maybe_snapshot(reason="startup")
        except Exception:
            pass
        # Purge trashed entries that have been there 30+ days. Their
        # files may already be gone; the DB rows are unreachable and
        # were waiting for this deletion.
        try:
            from app.engine.library import get_connection, purge_old_trash
            purge_old_trash(get_connection())
        except Exception:
            pass
        self.title("Ultimate DJ")
        self.geometry(f"{self.WIDTH}x{self.HEIGHT}")
        self.minsize(960, 600)
        self.configure(fg_color=COLORS["bg_dark"])

        self._nav_buttons: dict[str, ctk.CTkButton] = {}
        self._pages: dict[str, type] = {}
        self._page_cache: dict[str, ctk.CTkFrame] = {}
        self._current_page: str = ""

        self._build_sidebar()

        self.content = ctk.CTkFrame(
            self, fg_color=COLORS["bg_dark"], corner_radius=0)
        self.content.pack(side="right", fill="both", expand=True)

        # Default landing: Home (overview + quick actions)
        self._switch_page("Home")

        # Floating activity tray — appears top-left when any background
        # task registers itself, auto-hides when none are active. Single
        # source of truth for "what's the app currently doing".
        # Built eagerly + hidden until first task: the previous lazy
        # subscriber-based bootstrap had thread-marshalling races that
        # sometimes left the tray un-mounted even after register() had
        # fired (the user clicked Segmenter / Reconstruire and saw
        # nothing). Eager + place_forget is one tray construction at
        # boot (~130 ms) but rock-solid afterwards.
        from app.ui.activity_tray import ActivityTray
        # Status-bar pattern: parent the tray to the App ROOT and tell
        # it to pack itself `before=self.content` (→ a strip at the
        # bottom of the content region). Because it's on root, page
        # switches inside `self.content` can never unpack or bury it.
        # This replaces the fragile place()/float approach that kept
        # ending up hidden under CTk's DPI widget scaling.
        self._activity_tray = ActivityTray(self, pack_before=self.content)

        # Global keyboard nav: Ctrl+1..9 → switch to the n-th page in
        # sidebar order, Ctrl+, → Settings (Mac convention).
        self._wire_keyboard_shortcuts()

    def _wire_keyboard_shortcuts(self):
        """Bind Ctrl+1..9 to the page-switch order so power users
        never have to leave the keyboard."""
        # Flatten SIDEBAR_GROUPS to a single ordered list of names
        page_order: list[str] = []
        for _section, items in SIDEBAR_GROUPS:
            for label, _cls in items:
                page_order.append(label)
        for i, name in enumerate(page_order[:9], 1):
            self.bind(f"<Control-Key-{i}>",
                       lambda _e, n=name: self._switch_page(n))
        # Mac-style "preferences"
        self.bind("<Control-Key-comma>",
                   lambda _e: self._switch_page("Settings"))

        # Background auto-scan: on every launch, walk the music folders
        # configured in Settings, add any new audio files to the DB and
        # remove orphans whose path no longer exists. Runs after_idle so
        # the window paints first, then in a worker thread so the user
        # can interact while files get analysed.
        self.after(800, self._kick_off_auto_scan)

    def _kick_off_auto_scan(self):
        """Spawn the background sync worker. Logs to errors.log so the
        user can audit what was added/removed (UI surfaces a status line
        on Library page when they navigate there).

        Heavy imports (analyzer pulls librosa, ~3-5 s on cold cache) are
        deferred to the worker thread so they never block the UI. Only
        the light bookkeeping imports happen on the main thread here.
        """
        import threading
        from app.config import get_music_roots
        from app.engine import library
        from app.logger import log_info, log_error, log_warning

        roots = get_music_roots()
        if not roots:
            log_info("auto_scan: no music roots configured — skipping")
            return

        def work():
            # Import analyzer (and its librosa dependency) inside the
            # worker thread — keeps the main thread responsive while the
            # heavy modules load.
            from app.engine.analyzer import analyze_track, write_tags
            try:
                conn = library.get_connection()
                result = library.sync_library(conn, roots)
                new = result.get("new", [])
                removed = result.get("orphans_removed", 0)
                total = result.get("total", 0)
                log_info(f"auto_scan: {len(new)} new, {removed} orphans, "
                         f"{total} files on disk")
                # Analyse only the brand-new tracks — already-known
                # ones keep their cached BPM/key/etc.
                done = errs = 0
                for path in new:
                    try:
                        info = analyze_track(path)
                        library.upsert_track(conn, info)
                        write_tags(path, info["bpm"], info["key"])
                        done += 1
                    except Exception as e:
                        errs += 1
                        log_warning(f"auto_scan analyse failed for "
                                     f"{path}: {e}")
                if new or removed:
                    log_info(f"auto_scan done — analysed {done}, "
                             f"{errs} errors, removed {removed}")
            except Exception as e:
                log_error("auto_scan crashed", e)

        threading.Thread(target=work, daemon=True,
                          name="auto-scan").start()

    # ── Sidebar ──────────────────────────────────────────────────

    def _build_sidebar(self):
        self.sidebar = ctk.CTkFrame(
            self, width=210, corner_radius=0,
            fg_color=COLORS["bg_sidebar"])
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)

        ctk.CTkLabel(
            self.sidebar, text="ULTIMATE DJ",
            font=ctk.CTkFont(size=20, weight="bold"),
            text_color=COLORS["accent"],
        ).pack(pady=(28, 18))

        for section, items in SIDEBAR_GROUPS:
            ctk.CTkLabel(
                self.sidebar, text=section, anchor="w",
                font=ctk.CTkFont(size=10, weight="bold"),
                text_color=COLORS["text_dim"],
            ).pack(fill="x", padx=18, pady=(10, 2))

            for label, page_cls in items:
                self._pages[label] = page_cls
                btn = ctk.CTkButton(
                    self.sidebar,
                    text=f"   {label}",
                    anchor="w",
                    font=ctk.CTkFont(size=14),
                    height=36,
                    corner_radius=8,
                    fg_color="transparent",
                    text_color=COLORS["text"],
                    hover_color=COLORS["bg_card"],
                    command=lambda l=label: self._switch_page(l),
                )
                btn.pack(fill="x", padx=12, pady=2)
                self._nav_buttons[label] = btn

        # (No footer — version moved to Settings → About)

    def _switch_page(self, name: str):
        if name == self._current_page:
            return

        for lbl, btn in self._nav_buttons.items():
            if lbl == name:
                btn.configure(
                    fg_color=COLORS["accent"],
                    text_color=COLORS["bg_dark"],
                    hover_color=COLORS["accent_hover"])
            else:
                btn.configure(
                    fg_color="transparent",
                    text_color=COLORS["text"],
                    hover_color=COLORS["bg_card"])

        # Notify the outgoing page so it can cancel any pending render /
        # background work that's no longer visible to the user.
        if self._current_page and self._current_page in self._page_cache:
            outgoing = self._page_cache[self._current_page]
            if hasattr(outgoing, "on_hide"):
                try:
                    outgoing.on_hide()
                except Exception:
                    pass

        for child in self.content.winfo_children():
            child.pack_forget()

        if name not in self._page_cache:
            page_cls = self._pages[name]
            page = page_cls(self.content)
            self._page_cache[name] = page

        self._page_cache[name].pack(fill="both", expand=True)
        self._current_page = name

        page = self._page_cache[name]
        if hasattr(page, "on_show"):
            page.on_show()
        # No tray re-lift needed: the tray lives on the App root and is
        # packed before `content`, so switching pages inside `content`
        # never touches it.

    def reload_theme(self):
        """Apply the theme live: rebuild sidebar + active page only.

        Other cached pages are dropped from the cache so they get rebuilt
        lazily the next time the user clicks them — this keeps the theme
        switch snappy regardless of how many pages have been visited.
        """
        apply_theme()  # reload COLORS from disk

        previous = self._current_page

        # Drop all cached pages — they captured old colours at __init__.
        # Destroy only the currently-mounted one synchronously; defer the
        # rest so the UI stays responsive.
        active = self._page_cache.pop(previous, None)
        stale = list(self._page_cache.values())
        self._page_cache.clear()
        if active is not None:
            try:
                active.destroy()
            except Exception:
                pass

        self._nav_buttons.clear()
        self.sidebar.destroy()
        self._build_sidebar()
        self.configure(fg_color=COLORS["bg_dark"])
        self.content.configure(fg_color=COLORS["bg_dark"])

        target = previous if previous in self._pages else "Home"
        self._current_page = ""
        self._switch_page(target)

        # Destroy stale pages off the critical path
        def _cleanup():
            for p in stale:
                try:
                    p.destroy()
                except Exception:
                    pass
        self.after(50, _cleanup)
