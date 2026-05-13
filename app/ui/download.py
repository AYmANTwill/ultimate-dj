"""
Download page — YouTube / SoundCloud / Spotify playlist download.
Features: folder browser, format/quality selector, open-in-browser buttons,
live per-track status, Stop and Pause/Resume controls.
"""
from __future__ import annotations

import os
import threading
from pathlib import Path

import customtkinter as ctk
from tkinter import filedialog, simpledialog

from app.config import COLORS, load_config
from app.engine import downloader, spotify
from app.logger import log_error, log_info
from app.ui.fastlist import FastList
from app.ui.helpers import UiThrottle, font


class FolderBrowser(ctk.CTkFrame):
    """Inline folder navigator using a native tk.Listbox.

    Why tk.Listbox vs the previous CTkScrollableFrame:
    - Listbox always paints its rows reliably (the scroll-frame variant
      could collapse to height-0 depending on parent layout, which is
      what hid the user's subfolders)
    - Native scroll, way faster on big directories
    - Standard double-click-to-open / Enter-to-open behaviour
    - Reads as a real folder picker (clearer UX)
    """

    def __init__(self, parent, root_path: str):
        super().__init__(parent, fg_color=COLORS["bg_card"], corner_radius=10)
        # Force a minimum height so the listbox is *always* visible no
        # matter what fill mode our parent uses.
        self.configure(height=200)
        self.pack_propagate(False)
        self._root_dir = Path(root_path)
        self._root_dir.mkdir(parents=True, exist_ok=True)
        self._current = self._root_dir
        self._dirs: list[Path] = []
        self._build()

    def _build(self):
        import tkinter as tk
        # Top toolbar — current path + Parcourir / Nouveau
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=8, pady=(6, 4))

        ctk.CTkLabel(top, text="Dossier :", text_color=COLORS["text_dim"],
                     font=ctk.CTkFont(size=11)).pack(side="left", padx=(4, 6))

        self.path_label = ctk.CTkLabel(
            top, text=str(self._current),
            font=ctk.CTkFont(size=11), text_color=COLORS["accent"],
            anchor="w")
        self.path_label.pack(side="left", fill="x", expand=True)

        for txt, cmd in [("Parcourir", self._browse_external),
                          ("Nouveau",  self._create)]:
            ctk.CTkButton(
                top, text=txt, width=80, height=26,
                font=ctk.CTkFont(size=10),
                fg_color=COLORS["bg_input"], hover_color=COLORS["accent"],
                text_color=COLORS["text"],
                command=cmd,
            ).pack(side="right", padx=2)

        # The list itself — native Listbox so scrolling + height behave
        list_row = ctk.CTkFrame(self, fg_color="transparent")
        list_row.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        self._listbox = tk.Listbox(
            list_row,
            bg=COLORS["bg_input"], fg=COLORS["text"],
            selectbackground=COLORS["accent"],
            selectforeground=COLORS["bg_dark"],
            font=("Segoe UI", 10), bd=0, relief="flat",
            highlightthickness=0, activestyle="none")
        self._listbox.pack(side="left", fill="both", expand=True)
        self._listbox.bind("<Double-Button-1>", lambda _e: self._enter_selected())
        self._listbox.bind("<Return>", lambda _e: self._enter_selected())

        sb = ctk.CTkScrollbar(list_row, command=self._listbox.yview,
                               button_color=COLORS["accent"])
        sb.pack(side="right", fill="y")
        self._listbox.configure(yscrollcommand=sb.set)

        # Defer the actual scan so the layout settles first
        self.after(30, self._refresh_list)

    def _refresh_list(self):
        self._listbox.delete(0, "end")
        self._dirs = []

        # Up-one-level — always at the top so Radios → parent → Music works
        parent = self._current.parent
        if parent != self._current:
            self._listbox.insert("end", "..  (dossier parent)")
            self._dirs.append(parent)

        try:
            dirs = sorted([d for d in self._current.iterdir() if d.is_dir()],
                           key=lambda d: d.name.lower())
        except OSError:
            dirs = []
        # Cap to keep UI snappy on huge directories
        truncated = len(dirs) > 200
        dirs = dirs[:200]
        for d in dirs:
            self._listbox.insert("end", f"  📁  {d.name}")
            self._dirs.append(d)

        if not dirs:
            self._listbox.insert(
                "end",
                "  (aucun sous-dossier — clique « Nouveau » pour en créer un)")
            self._dirs.append(None)
        elif truncated:
            self._listbox.insert(
                "end", "  … (200 premiers sous-dossiers affichés)")
            self._dirs.append(None)

        self.path_label.configure(text=str(self._current))

    def _enter_selected(self):
        sel = self._listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        if idx >= len(self._dirs):
            return
        path = self._dirs[idx]
        if path is not None:
            self._navigate(path)

    def _navigate(self, path: Path):
        self._current = path
        self._refresh_list()

    def _create(self):
        name = simpledialog.askstring("New Folder", "Folder name:",
                                       parent=self.winfo_toplevel())
        if name:
            safe = "".join(c for c in name if c not in '<>:"/\\|?*')
            (self._current / safe).mkdir(parents=True, exist_ok=True)
            self._navigate(self._current / safe)

    def _browse_external(self):
        folder = filedialog.askdirectory(
            title="Choisir un dossier",
            initialdir=str(self._current))
        if folder:
            self._current = Path(folder)
            self._refresh_list()

    @property
    def selected_path(self) -> str:
        return str(self._current)


class DownloadPage(ctk.CTkFrame):

    def __init__(self, parent):
        super().__init__(parent, fg_color=COLORS["bg_dark"])
        # Track index -> (status_text, status_tag) — used to update rows
        self._track_iids: dict[int, str] = {}
        self._track_data: dict[int, tuple] = {}
        self._track_table: FastList | None = None
        self._stop_event  = threading.Event()
        self._pause_event = threading.Event()
        self._running = False
        self._status_throttle: UiThrottle | None = None
        self._progress_throttle: UiThrottle | None = None
        self._summary_throttle: UiThrottle | None = None
        self._build_ui()
        # 80ms ≈ 12 fps for status, way faster than user perception
        self._status_throttle = UiThrottle(self, interval_ms=80)
        self._progress_throttle = UiThrottle(self, interval_ms=80)
        self._summary_throttle = UiThrottle(self, interval_ms=120)

    # ── UI construction ───────────────────────────────────────

    def _build_ui(self):
        # ── Single compact header row: title + service shortcuts ─
        hdr = ctk.CTkFrame(self, fg_color="transparent")
        hdr.pack(fill="x", padx=24, pady=(20, 4))

        ctk.CTkLabel(hdr, text="Download",
                     font=ctk.CTkFont(size=24, weight="bold"),
                     text_color=COLORS["text"]).pack(side="left")
        ctk.CTkLabel(hdr,
                     text="  YouTube · SoundCloud · Spotify · 1001Tracklists",
                     font=ctk.CTkFont(size=12),
                     text_color=COLORS["text_dim"]).pack(side="left", padx=4)
        # The 3 service buttons that opened the system browser have been
        # replaced by the embedded BrowserPanel below — the user navigates
        # the services *inside* the app and pushes the chosen URL into
        # the URL field with one click.

        # ── Big single card with everything: URL + format + actions
        card = ctk.CTkFrame(self, fg_color=COLORS["bg_card"], corner_radius=14)
        card.pack(fill="x", padx=24, pady=(0, 8))

        # URL row
        url_row = ctk.CTkFrame(card, fg_color="transparent")
        url_row.pack(fill="x", padx=14, pady=(12, 6))
        ctk.CTkLabel(url_row, text="URL", width=46,
                     font=ctk.CTkFont(size=11, weight="bold"),
                     text_color=COLORS["text_dim"]).pack(side="left")
        self.url_entry = ctk.CTkEntry(
            url_row, placeholder_text="Colle un lien YouTube / SoundCloud / Spotify…",
            height=36, font=ctk.CTkFont(size=12),
            fg_color=COLORS["bg_input"], border_color=COLORS["accent"],
            text_color=COLORS["text"])
        self.url_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))
        ctk.CTkButton(
            url_row, text="Coller", width=70, height=34,
            fg_color=COLORS["bg_input"], hover_color=COLORS["accent"],
            text_color=COLORS["text"], font=ctk.CTkFont(size=11),
            command=self._paste_clipboard).pack(side="left")

        # Format / quality / actions row
        ctrl = ctk.CTkFrame(card, fg_color="transparent")
        ctrl.pack(fill="x", padx=14, pady=(0, 12))

        ctk.CTkLabel(ctrl, text="Format", width=46,
                     font=ctk.CTkFont(size=11, weight="bold"),
                     text_color=COLORS["text_dim"]).pack(side="left")
        self.format_var = ctk.StringVar(value="MP3")
        ctk.CTkOptionMenu(
            ctrl, values=["MP3", "WAV", "MP3 (fallback WAV)", "WAV (fallback MP3)"],
            variable=self.format_var, width=170, height=32,
            fg_color=COLORS["bg_input"], button_color=COLORS["accent"],
            dropdown_fg_color=COLORS["bg_card"],
            text_color=COLORS["text"],
        ).pack(side="left", padx=(0, 14))

        # 320 kbps locked — DJ standard. Bad audio = bad sets.
        self.quality_var = ctk.StringVar(value="320")
        ctk.CTkLabel(ctrl, text="320 kbps", text_color=COLORS["text_dim"],
                     font=ctk.CTkFont(size=10)).pack(side="left", padx=(0, 8))

        # Right-aligned action buttons
        self.dl_btn = ctk.CTkButton(
            ctrl, text="Télécharger", width=130, height=34,
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
            text_color=COLORS["bg_dark"],
            command=self._start_download)
        self.dl_btn.pack(side="right", padx=(6, 0))
        self.pause_btn = ctk.CTkButton(
            ctrl, text="Pause", width=70, height=34,
            font=ctk.CTkFont(size=11),
            fg_color=COLORS["warning"], hover_color="#e09900",
            text_color=COLORS["bg_dark"], state="disabled",
            command=self._toggle_pause)
        self.pause_btn.pack(side="right", padx=4)
        self.stop_btn = ctk.CTkButton(
            ctrl, text="Stop", width=60, height=34,
            font=ctk.CTkFont(size=11),
            fg_color=COLORS["error"], hover_color="#cc3333",
            text_color="white", state="disabled",
            command=self._stop_download)
        self.stop_btn.pack(side="right", padx=4)

        # Folder browser + preview panel inside a draggable PanedWindow.
        # The user can grab the sash (dark stripe between the two) and
        # resize: shrink the folder list to make the embedded browser
        # taller, or vice-versa. Min sizes prevent panes from being
        # collapsed into nothing.
        import tkinter as tk
        cfg = load_config()
        dl_root = cfg.get("download_folder",
                           str(Path(__file__).resolve().parent.parent.parent / "downloads"))

        self._main_pane = tk.PanedWindow(
            self, orient="vertical",
            bg=COLORS["bg_dark"],            # gutter colour
            sashwidth=6,                     # 6 px grab strip
            sashrelief="flat",
            # opaqueresize=False → only a phantom guideline follows the
            # cursor during drag; the actual layout snaps once on release.
            # Drastically smoother than live-redraw on heavy CTk widgets,
            # which can stutter during continuous resize.
            bd=0, opaqueresize=False)
        self._main_pane.pack(fill="both", expand=True,
                              padx=24, pady=(0, 6))

        self.folder_browser = FolderBrowser(self._main_pane, dl_root)
        # minsize lets users squash the folder list down to "just the
        # path bar"; height seeds the initial allocation.
        self._main_pane.add(self.folder_browser,
                             minsize=44, height=200, stretch="never")

        # Track list / content area (takes remaining vertical space).
        # Holds either:
        #   - the embedded BrowserPanel (idle state — built lazily)
        #   - the Spotify track-list (during/after a Spotify download)
        self.preview_frame = ctk.CTkFrame(self._main_pane,
                                           fg_color=COLORS["bg_card"],
                                           corner_radius=12)
        self._main_pane.add(self.preview_frame,
                             minsize=160, stretch="always")
        self._browser_panel = None    # built on first idle paint
        self._show_placeholder()

        # Compact bottom bar: progress + status + log toggle
        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.pack(fill="x", padx=24, pady=(0, 6))
        self.progress_bar = ctk.CTkProgressBar(
            bottom, fg_color=COLORS["bg_card"],
            progress_color=COLORS["accent"], height=6)
        self.progress_bar.pack(fill="x", side="top")
        self.progress_bar.set(0)
        self.status_label = ctk.CTkLabel(
            bottom, text="Prêt", font=ctk.CTkFont(size=11),
            text_color=COLORS["text_dim"])
        self.status_label.pack(side="left", pady=(4, 0))
        ctk.CTkButton(
            bottom, text="Ouvrir le journal", width=120, height=22,
            font=ctk.CTkFont(size=10),
            fg_color="transparent", hover_color=COLORS["bg_card"],
            text_color=COLORS["text_dim"],
            command=self._open_log_file,
        ).pack(side="right", pady=(4, 0))

        # Inline log — kept hidden by default to give the embedded browser
        # all the vertical space it needs. Auto-shown the moment a
        # download writes its first line, and hidden again on Stop.
        import tkinter as tk
        self.log = tk.Text(
            self, height=4, bg=COLORS["bg_card"], fg=COLORS["text"],
            font=("Consolas", 10), bd=0, relief="flat",
            highlightthickness=0, state="disabled")
        self._log_visible = False
        # NOT packed yet — _show_log_pane() will pack it when needed

    # ── Helpers ───────────────────────────────────────────────

    def _show_placeholder(self):
        """Default idle state = embedded BrowserPanel.

        The panel is built lazily on first idle paint, so opening
        the Download page is instant. Until the panel exists we show
        a tiny placeholder so the area isn't empty.
        """
        for w in self.preview_frame.winfo_children():
            try:
                if self._browser_panel is not None and w is self._browser_panel:
                    w.pack_forget()
                else:
                    w.destroy()
            except Exception:
                pass
        if self._browser_panel is None:
            # Show a one-line placeholder while we wait for after_idle
            self._idle_placeholder = ctk.CTkLabel(
                self.preview_frame,
                text="Browser intégré : SoundCloud · Spotify · YouTube · "
                     "1001Tracklists  (chargement…)",
                text_color=COLORS["text_dim"],
                font=ctk.CTkFont(size=12))
            self._idle_placeholder.pack(expand=True)
            self.after_idle(self._build_browser_panel)
        else:
            self._browser_panel.pack(fill="both", expand=True, padx=4, pady=4)

    def _build_browser_panel(self):
        if self._browser_panel is not None:
            return
        from app.ui.browser import BrowserPanel
        # Drop the temporary placeholder if still up
        ph = getattr(self, "_idle_placeholder", None)
        if ph is not None:
            try:
                ph.destroy()
            except Exception:
                pass
            self._idle_placeholder = None
        self._browser_panel = BrowserPanel(
            self.preview_frame, compact=True,
            on_url_pick=self._on_browser_url_pick)
        self._browser_panel.pack(fill="both", expand=True, padx=4, pady=4)

    def _on_browser_url_pick(self, url: str):
        """User clicked « ↑ Coller dans URL » in the embedded browser."""
        self.url_entry.delete(0, "end")
        self.url_entry.insert(0, url)
        self._set_status(f"URL prête : {url[:60]}", "success")

    def _paste_clipboard(self):
        try:
            txt = self.clipboard_get()
        except Exception:
            return
        if txt:
            self.url_entry.delete(0, "end")
            self.url_entry.insert(0, txt.strip())

    def _log(self, msg: str):
        # Lazily reveal the log pane — first line of any download will
        # pop it open; before that it stays hidden and the browser eats
        # the full vertical space.
        if not self._log_visible:
            self._show_log_pane()
        self.log.configure(state="normal")
        self.log.insert("end", msg + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _log_clear(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def _show_log_pane(self):
        if self._log_visible:
            return
        self.log.pack(fill="x", padx=24, pady=(0, 12))
        self._log_visible = True

    def _hide_log_pane(self):
        if not self._log_visible:
            return
        self.log.pack_forget()
        self._log_visible = False

    def _set_status(self, text: str, color: str = "text_dim"):
        self.status_label.configure(text=text,
                                     text_color=COLORS.get(color, color))

    def _open_log_file(self):
        from app.logger import get_log_path
        path = get_log_path()
        if os.path.exists(path):
            os.startfile(path)

    def _get_format_opts(self) -> tuple[str, str | None]:
        sel = self.format_var.get()
        if sel == "MP3":           return "mp3", None
        if sel == "WAV":           return "wav", None
        if sel.startswith("MP3"): return "mp3", "wav"
        return "wav", "mp3"

    # ── Running state management ──────────────────────────────

    def _set_running(self, running: bool):
        self._running = running
        if running:
            self.dl_btn.configure(state="disabled", text="Downloading…")
            self.stop_btn.configure(state="normal")
            self.pause_btn.configure(state="normal", text="Pause",
                                      fg_color=COLORS["warning"])
        else:
            self.dl_btn.configure(state="normal", text="Download")
            self.stop_btn.configure(state="disabled")
            self.pause_btn.configure(state="disabled", text="Pause",
                                      fg_color=COLORS["warning"])
            self._pause_event.clear()

    def _stop_download(self):
        self._stop_event.set()
        self._pause_event.clear()          # unblock if paused
        self._set_status("Stopping after current track…", "warning")
        self.stop_btn.configure(state="disabled")

    def _toggle_pause(self):
        if self._pause_event.is_set():
            self._pause_event.clear()
            self.pause_btn.configure(text="Pause", fg_color=COLORS["warning"])
            self._set_status("Resumed", "accent")
        else:
            self._pause_event.set()
            self.pause_btn.configure(text="Resume", fg_color=COLORS["success"])
            self._set_status("Paused — click Resume to continue", "warning")

    # ── Spotify track list UI ─────────────────────────────────

    def _build_track_list(self, name: str, tracks: list[dict]):
        # Don't destroy the BrowserPanel — just hide it. Recreating it
        # would kill the embedded WebView2 process and lose the user's
        # logged-in session.
        for w in self.preview_frame.winfo_children():
            try:
                if w is self._browser_panel:
                    w.pack_forget()
                else:
                    w.destroy()
            except Exception:
                pass
        self._track_iids.clear()
        self._track_data.clear()

        hdr = ctk.CTkFrame(self.preview_frame, fg_color="transparent")
        hdr.pack(fill="x", padx=16, pady=(10, 4))
        # Back to browser button — keeps the WebView2 session alive
        ctk.CTkButton(
            hdr, text="← Browser", width=90, height=24,
            font=font(11),
            fg_color=COLORS["bg_input"], hover_color=COLORS["accent"],
            text_color=COLORS["text"],
            command=self._show_placeholder).pack(side="left", padx=(0, 8))
        ctk.CTkLabel(hdr, text=name,
                     font=font(15, "bold"),
                     text_color=COLORS["accent"]).pack(side="left")
        self._summary_label = ctk.CTkLabel(
            hdr, text=f"{len(tracks)} tracks",
            font=font(12), text_color=COLORS["text_dim"])
        self._summary_label.pack(side="right", padx=4)

        self._track_table = FastList(
            self.preview_frame,
            [("st",     "",         32),
             ("n",      "#",        40),
             ("title",  "Title",   320),
             ("artist", "Artist",  200),
             ("dur",    "Time",     60)],
            sortable=False,
            height=14,
        )
        self._track_table.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        # Build rows in a single batch — Treeview eats this in <50ms
        rows = []
        for i, t in enumerate(tracks, 1):
            dur = spotify.format_duration(t.get("duration", 0))
            row = ("·", str(i), t["title"][:80], t["artist"][:50], dur)
            rows.append(row)
            self._track_data[i] = row
        # We need iids back so updates can target rows — set rows then
        # capture them.
        self._track_table.set_rows(rows)
        # Map row index -> iid via the table's internal order
        for i, iid in enumerate(self._track_table.tree.get_children(""), 1):
            self._track_iids[i] = iid

    def _update_track_status(self, i: int, status: str):
        iid = self._track_iids.get(i)
        if not iid or self._track_table is None:
            return
        icons = {
            "downloading": ("⬇", "warn"),
            "ok":          ("✓", "ok"),
            "fail":        ("✗", "err"),
            "stopped":     ("■", "warn"),
            "paused":      ("⏸", "warn"),
        }
        icon, tag = icons.get(status, ("·", ""))
        old = self._track_data.get(i)
        if old is None:
            return
        new_row = (icon, *old[1:])
        self._track_data[i] = new_row
        self._track_table.update_row(iid, new_row, tags=(tag,) if tag else ())

    def _update_summary(self, ok: int, fail: int, total: int):
        lbl = getattr(self, "_summary_label", None)
        if lbl:
            parts = [f"{total} tracks", f"✓ {ok}"]
            if fail:
                parts.append(f"✗ {fail}")
            lbl.configure(text="  ·  ".join(parts))

    # ── Download logic ────────────────────────────────────────

    def _start_download(self):
        url = self.url_entry.get().strip()
        if not url:
            self._set_status("Please enter a URL", "warning")
            return

        out     = self.folder_browser.selected_path
        quality = self.quality_var.get()
        codec, fallback = self._get_format_opts()

        self._stop_event.clear()
        self._pause_event.clear()
        self._set_running(True)
        self.progress_bar.set(0)
        self._log_clear()

        if "spotify.com" in url:
            threading.Thread(
                target=self._download_spotify,
                args=(url, out, quality, codec, fallback),
                daemon=True).start()
        else:
            self._log(f"→ {out}")
            threading.Thread(
                target=self._download_direct,
                args=(url, out, quality, codec, fallback),
                daemon=True).start()

    def _download_direct(self, url, out, quality, codec, fallback):
        def on_progress(status, msg):
            # yt-dlp fires 10+ times per second — throttle hard or the
            # mainloop chokes. Only the latest status/percentage within
            # the throttle window actually paints.
            self._status_throttle.call(
                lambda s=status, m=msg: self._set_status(f"{s}: {m}"))
            if "%" in str(msg):
                try:
                    pct_str = str(msg).split("%")[0].strip().lstrip("\x1b[0-9;m")
                    pct = float(pct_str) / 100
                    self._progress_throttle.call(
                        lambda p=pct: self.progress_bar.set(p))
                except Exception:
                    pass

        try:
            paths = downloader.download_url(
                url, out, quality, codec=codec, fallback_codec=fallback,
                on_progress=on_progress, stop_event=self._stop_event)
            self.after(0, lambda: self._download_finished(paths, codec))
        except Exception as e:
            log_error("Direct download thread crashed", e)
            self.after(0, lambda: self._set_status(f"Error: {e}", "error"))
            self.after(0, lambda: self._set_running(False))

    def _download_spotify(self, url, out, quality, codec, fallback):
        self.after(0, lambda: self._set_status("Fetching Spotify playlist…"))

        if spotify.is_editorial(url):
            self.after(0, lambda: self._set_status(
                "Editorial playlist — save to your library first", "warning"))
            self.after(0, lambda: self._log(
                "Spotify blocks API access to editorial playlists (IDs starting 37i9dQZF1).\n"
                "Open Spotify → playlist menu → 'Save to your library', then paste the new URL."))
            self.after(0, lambda: self._set_running(False))
            return

        name, source_tracks, err = spotify.fetch_playlist(url)
        if not source_tracks:
            msg = err or "Could not fetch playlist"
            self.after(0, lambda: self._set_status(msg, "error"))
            self.after(0, lambda: self._log(msg))
            self.after(0, lambda: self._set_running(False))
            return

        # Smart re-sync: if this playlist was already downloaded into
        # this folder, only fetch the tracks that have been ADDED. Ask
        # the user before deleting tracks that LEFT the source playlist.
        from app.engine import playlist_sync
        playlist_id = spotify.url_id(url)
        cache = playlist_sync.load_cache(playlist_id, out) if playlist_id else None
        diff = playlist_sync.compute_diff(source_tracks, cache)

        decision = {"proceed": True, "delete_removed": False}
        if cache is not None and (diff["kept"] or diff["removed"]):
            # There IS prior history — ask the user via a modal on the
            # Tk thread before doing anything. The worker thread blocks
            # on a threading.Event until they answer.
            import threading
            ev = threading.Event()
            self.after(0, lambda: self._ask_resync(name, diff, decision, ev))
            ev.wait()
            if not decision["proceed"]:
                self.after(0, lambda: self._set_status(
                    "Sync annulé.", "text_dim"))
                self.after(0, lambda: self._set_running(False))
                return

        # The list of tracks the downloader actually pulls = "added"
        # only. "kept" tracks are already on disk and stay.
        tracks = diff["added"]
        if not tracks:
            self.after(0, lambda: self._set_status(
                f"Rien de nouveau — {len(diff['kept'])} déjà sur le disque, "
                f"{len(diff['removed'])} retirés de la playlist.",
                "success"))
            # Still apply the user's removal decision + refresh cache
            self._after_spotify_sync(
                url, name, playlist_id, out, source_tracks,
                cache, diff, [], decision)
            self.after(0, lambda: self._set_running(False))
            return

        self.after(0, lambda: self._build_track_list(name, tracks))
        skip_msg = (f" · {len(diff['kept'])} déjà OK"
                     if diff["kept"] else "")
        self.after(0, lambda: self._set_status(
            f"À télécharger : {len(tracks)} nouveaux{skip_msg}"))

        ok_count = 0
        fail_count = 0

        def on_track(i, total, display, status, err_msg):
            nonlocal ok_count, fail_count
            if status == "ok":
                ok_count += 1
            elif status == "fail":
                fail_count += 1

            pct = i / total
            self._progress_throttle.call(
                lambda p=pct: self.progress_bar.set(p))
            # Per-track icon updates are state changes, not flooding —
            # one per track. Direct dispatch.
            self.after(0, lambda s=status, idx=i: self._update_track_status(idx, s))
            self._summary_throttle.call(
                lambda o=ok_count, f=fail_count, t=total:
                    self._update_summary(o, f, t))

            if status == "downloading":
                self._status_throttle.call(
                    lambda i=i, total=total, display=display:
                        self._set_status(f"[{i}/{total}] {display[:55]}…"))
            elif status == "paused":
                self._status_throttle.call_now(
                    lambda: self._set_status(
                        "Paused — click Resume to continue", "warning"))
            elif status == "fail":
                short_err = f"  ({err_msg[:60]})" if err_msg else ""
                # Log lines are infrequent (one per failure) — direct
                self.after(0, lambda d=display, s=short_err:
                           self._log(f"✗  {d[:60]}{s}"))

        try:
            ok, fail, paths, failed = downloader.download_tracks_by_search(
                tracks, out, quality, codec=codec, fallback_codec=fallback,
                on_track=on_track,
                stop_event=self._stop_event,
                pause_event=self._pause_event)
        except Exception as e:
            log_error("Spotify download thread crashed", e)
            ok, fail, paths, failed = 0, 0, [], []

        def _finish():
            self._update_summary(ok, fail, len(tracks))
            self._set_running(False)
            if self._stop_event.is_set():
                self._set_status(
                    f"Stopped — {ok} downloaded, {fail} failed", "warning")
            else:
                self._download_finished(paths, codec)
            if failed:
                self._log(f"\n── MISSING ({fail}/{len(tracks)}) ───────────────────")
                for t in failed:
                    self._log(f"  ✗  {t['artist']} — {t['title']}")
                self._log("────────────────────────────────────────────────")
            # Apply removal decision + refresh the playlist cache so
            # the next sync knows exactly what's on disk.
            self._after_spotify_sync(
                url, name, playlist_id, out, source_tracks,
                cache, diff, paths, decision)

        self.after(0, _finish)

    def _ask_resync(self, playlist_name: str, diff: dict,
                     decision: dict, ev) -> None:
        """Modal that asks the user how to handle a re-sync. Sets
        ``decision`` in place and signals the worker via Event."""
        win = ctk.CTkToplevel(self)
        win.title("Resynchroniser la playlist")
        win.geometry("560x440")
        win.configure(fg_color=COLORS["bg_dark"])
        win.transient(self.winfo_toplevel())
        try:
            win.grab_set()
        except Exception:
            pass

        ctk.CTkLabel(
            win, text=f"« {playlist_name[:45]} »",
            font=font(16, "bold"),
            text_color=COLORS["accent"]
        ).pack(anchor="w", padx=20, pady=(20, 4))

        ctk.CTkLabel(
            win,
            text=("Cette playlist a déjà été téléchargée dans ce "
                   "dossier. Voilà ce qui a changé depuis :"),
            font=font(11), text_color=COLORS["text_dim"],
            wraplength=520, justify="left",
        ).pack(anchor="w", padx=20, pady=(0, 12))

        # Diff card
        card = ctk.CTkFrame(win, fg_color=COLORS["bg_card"], corner_radius=10)
        card.pack(fill="x", padx=20, pady=(0, 12))
        for sym, label, count, color in [
            ("📥", "à télécharger (nouveaux)",
             len(diff["added"]) - len(diff["missing"]), COLORS["accent"]),
            ("⟳",  "à re-télécharger (fichier disparu localement)",
             len(diff["missing"]), COLORS["warning"]),
            ("⏭", "déjà sur le disque (skip)",
             len(diff["kept"]), COLORS["success"]),
            ("⚠", "retirés de la playlist source",
             len(diff["removed"]), COLORS["error"]),
        ]:
            row = ctk.CTkFrame(card, fg_color="transparent")
            row.pack(fill="x", padx=12, pady=4)
            ctk.CTkLabel(row, text=f"{sym}  {count}",
                         font=font(13, "bold"),
                         text_color=color, width=60
                         ).pack(side="left")
            ctk.CTkLabel(row, text=label,
                         font=font(11),
                         text_color=COLORS["text"], anchor="w"
                         ).pack(side="left", fill="x", expand=True, padx=8)

        # Removal opt-in (only shown if there's something to remove)
        delete_var = ctk.BooleanVar(value=False)
        if diff["removed"]:
            ctk.CTkCheckBox(
                win,
                text=(f"Supprimer aussi les {len(diff['removed'])} "
                       "fichiers retirés de la playlist source"),
                variable=delete_var,
                font=font(12),
                text_color=COLORS["text"],
                checkbox_height=18, checkbox_width=18,
                fg_color=COLORS["error"],
            ).pack(anchor="w", padx=20, pady=(4, 4))
            ctk.CTkLabel(
                win,
                text=("Cocher = strip de ton dossier exactement comme "
                       "Spotify. Décocher = garder ces fichiers en local."),
                font=font(10), text_color=COLORS["text_dim"],
                wraplength=520, justify="left",
            ).pack(anchor="w", padx=20, pady=(0, 12))

        # Buttons
        bar = ctk.CTkFrame(win, fg_color="transparent")
        bar.pack(fill="x", padx=20, pady=(8, 18), side="bottom")

        def _cancel():
            decision["proceed"] = False
            ev.set()
            win.destroy()

        def _go():
            decision["proceed"] = True
            decision["delete_removed"] = bool(delete_var.get())
            ev.set()
            win.destroy()

        ctk.CTkButton(
            bar, text="Annuler", width=110, height=36,
            fg_color=COLORS["bg_card"], hover_color=COLORS["bg_input"],
            text_color=COLORS["text"], command=_cancel,
        ).pack(side="right", padx=4)
        ctk.CTkButton(
            bar, text="Continuer", width=160, height=36,
            font=font(13, "bold"),
            fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
            text_color=COLORS["bg_dark"], command=_go,
        ).pack(side="right", padx=4)
        win.protocol("WM_DELETE_WINDOW", _cancel)

    def _after_spotify_sync(self, url, name, playlist_id, folder,
                              source_tracks, cache, diff, downloaded_paths,
                              decision):
        """Run after the Spotify download finishes (or was skipped).
        - Optionally delete the files the user agreed to remove
        - Refresh the cache so the next sync knows the exact disk state
        """
        from app.engine import playlist_sync
        if not playlist_id:
            return

        if decision.get("delete_removed") and diff["removed"]:
            ok_d, fail_d = playlist_sync.delete_files(diff["removed"])
            self.after(0, lambda o=ok_d, f=fail_d: self._log(
                f"\n── REMOVED FROM DISK ({o} ok, {f} fail) ──"))
            for ct in diff["removed"]:
                self._log(f"  ✗  {ct.get('artist','')} — {ct.get('title','')}")

        # Build the new cache: kept tracks + freshly-downloaded ones
        # (matched fuzzy by filename), MINUS the user-deleted ones
        keep_now = list(diff["kept"])
        if not decision.get("delete_removed"):
            # User said keep them — their files are still there but no
            # longer in the source playlist. Drop from cache anyway so
            # they're not re-checked next sync (they're "orphan local").
            pass
        new_entries = playlist_sync.merge_after_download(
            cache, source_tracks, downloaded_paths)
        # Index by spotify_id, kept entries override since they have a
        # stable filepath we already validated
        merged: dict[str, dict] = {e["spotify_id"]: e for e in new_entries}
        for k in keep_now:
            merged[k["spotify_id"]] = k

        try:
            playlist_sync.save_cache(
                url, playlist_id, name, folder, list(merged.values()))
        except Exception as e:
            log_error("playlist_sync.save_cache failed", e)

    def _download_finished(self, paths, codec="mp3"):
        self.progress_bar.set(1.0)
        self._set_status(f"Done — {len(paths)} {codec.upper()} files in folder",
                          "success")
        self._set_running(False)
