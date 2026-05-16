"""
Library browser page — view, search, filter all analyzed tracks.

Performance:
- Track table is a ttk.Treeview (FastList) — virtualised, native, painted
  in C. 5 000 tracks render in <100 ms.
- DB queries always run off the UI thread.
- Sort by clicking any column header (free with FastList).
- Duplicate filter is now an inline view toggle (used to be a modal).
"""
from __future__ import annotations

import threading

import customtkinter as ctk

from app.config import COLORS, load_config
from app.engine.library import (
    get_connection, all_tracks, search_tracks, delete_track, track_count,
    sync_library, find_duplicates, set_rating, override_bpm, set_genre,
)
from app.engine.analyzer import analyze_track, write_tags
from app.engine.library import upsert_track
from app.logger import log_error, log_info, log_warning
from app.ui.fastlist import FastList
from app.ui.helpers import UiThrottle, font, confirm, attach_tooltip


def _format_duration(seconds: float | None) -> str:
    s = int(seconds or 0)
    return f"{s // 60}:{s % 60:02d}"


def _format_eta(seconds: float) -> str:
    """Human-friendly ETA string. Empty when < 1s."""
    s = int(seconds)
    if s < 1:
        return "—"
    if s < 60:
        return f"{s}s"
    if s < 3600:
        m, s = divmod(s, 60)
        return f"{m}min{s:02d}"
    h, rem = divmod(s, 3600)
    m = rem // 60
    return f"{h}h{m:02d}"


class LibraryPage(ctk.CTkFrame):
    def __init__(self, parent):
        super().__init__(parent, fg_color=COLORS["bg_dark"])
        self._sync_throttle: UiThrottle | None = None
        self._tracks: list[dict] = []  # cached for double-click handler
        self._dup_only = False         # toggle: show duplicates only
        self._unrated_only = False     # toggle: show un-rated only
        self._build_ui()

    def _build_ui(self):
        # Header ────────────────────────────────────────────────────
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=30, pady=(24, 10))

        ctk.CTkLabel(top, text="Library",
                     font=font(26, "bold"),
                     text_color=COLORS["text"]).pack(side="left")

        self.count_label = ctk.CTkLabel(
            top, text="", font=font(13),
            text_color=COLORS["text_dim"])
        self.count_label.pack(side="left", padx=16)

        ctk.CTkButton(
            top, text="Refresh", width=80, height=32,
            fg_color=COLORS["bg_card"], hover_color=COLORS["accent"],
            command=self._refresh).pack(side="right")

        ctk.CTkButton(
            top, text="Export…", width=90, height=32,
            fg_color=COLORS["accent2"], hover_color="#e0356f",
            text_color=COLORS["on_accent2"],
            command=self._export).pack(side="right", padx=4)

        # Sync Library = scan tous les dossiers musique configurés (le
        # principal + les sources secondaires depuis Settings), retire
        # les fichiers qui ne sont plus sur le disque, et analyse les
        # nouveaux trouvés.
        self.sync_btn = ctk.CTkButton(
            top, text="Sync Library", width=120, height=32,
            fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
            text_color=COLORS["bg_dark"],
            command=self._sync)
        self.sync_btn.pack(side="right", padx=8)
        attach_tooltip(
            self.sync_btn,
            "Scanne tous les dossiers musique configurés (principal + "
            "secondaires depuis Settings), retire les fichiers absents "
            "du disque, et analyse les nouveaux trouvés.")

        # Doublons : un toggle pour filtrer la vue + un bouton pour
        # ouvrir le modal de résolution (choisir lequel garder)
        self.dup_toggle = ctk.CTkSwitch(
            top, text="Doublons", font=font(11),
            text_color=COLORS["text_dim"],
            progress_color=COLORS["error"],
            command=self._toggle_dup)
        self.dup_toggle.pack(side="right", padx=4)
        ctk.CTkButton(
            top, text="Résoudre…", width=90, height=32,
            font=font(11),
            fg_color=COLORS["error"], hover_color="#cc3333",
            text_color="white",
            command=self._open_duplicates,
        ).pack(side="right", padx=4)

        self.unrated_toggle = ctk.CTkSwitch(
            top, text="Non notés", font=font(11),
            text_color=COLORS["text_dim"],
            progress_color=COLORS["warning"],
            command=self._toggle_unrated)
        self.unrated_toggle.pack(side="right", padx=4)

        self.sync_status = ctk.CTkLabel(
            top, text="", font=font(11),
            text_color=COLORS["text_dim"])
        self.sync_status.pack(side="right", padx=8)

        # Search bar ────────────────────────────────────────────────
        search_frame = ctk.CTkFrame(self, fg_color=COLORS["bg_card"], corner_radius=10)
        search_frame.pack(fill="x", padx=30, pady=(0, 10))

        self.search_entry = ctk.CTkEntry(
            search_frame, placeholder_text="Search title...",
            height=36, fg_color=COLORS["bg_input"],
            border_color=COLORS["bg_input"], text_color=COLORS["text"])
        self.search_entry.pack(side="left", fill="x", expand=True, padx=(12, 6), pady=8)
        self.search_entry.bind("<Return>", lambda e: self._search())

        ctk.CTkLabel(search_frame, text="Key:",
                     text_color=COLORS["text_dim"]).pack(side="left", padx=(8, 2))
        self.key_entry = ctk.CTkEntry(
            search_frame, width=60, height=36, placeholder_text="e.g. 8A",
            fg_color=COLORS["bg_input"], border_color=COLORS["bg_input"],
            text_color=COLORS["text"])
        self.key_entry.pack(side="left", padx=(0, 6), pady=8)

        ctk.CTkLabel(search_frame, text="BPM:",
                     text_color=COLORS["text_dim"]).pack(side="left", padx=(8, 2))
        self.bpm_min = ctk.CTkEntry(
            search_frame, width=50, height=36, placeholder_text="min",
            fg_color=COLORS["bg_input"], border_color=COLORS["bg_input"],
            text_color=COLORS["text"])
        self.bpm_min.pack(side="left", pady=8)
        ctk.CTkLabel(search_frame, text="-",
                     text_color=COLORS["text_dim"]).pack(side="left")
        self.bpm_max = ctk.CTkEntry(
            search_frame, width=50, height=36, placeholder_text="max",
            fg_color=COLORS["bg_input"], border_color=COLORS["bg_input"],
            text_color=COLORS["text"])
        self.bpm_max.pack(side="left", padx=(0, 6), pady=8)

        # Genre + rating filters built lazily — CTkOptionMenu adds ~150ms
        # to page-init; deferring it keeps the page-switch under 500ms.
        # The variables exist immediately so _search() never crashes.
        self.rating_var = ctk.StringVar(value="any")
        self.genre_entry = None
        self._search_frame = search_frame
        self.after_idle(self._build_extra_filters)

        ctk.CTkButton(search_frame, text="Search", width=70, height=32,
                       fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
                       text_color=COLORS["bg_dark"],
                       command=self._search).pack(side="right", padx=12, pady=8)

        # The track table ───────────────────────────────────────────
        # Double-click a row → open the track-edit popup (rating, BPM
        # override, genre, etc.)
        cols = [
            ("title",   "Title",    300),
            ("bpm",     "BPM",       60),
            ("key",     "Key",      100),
            ("camelot", "Cam",       60),
            ("energy",  "E",         50),
            ("rating",  "Rating",    80),
            ("genre",   "Genre",    140),
            ("dur",     "Dur",       60),
        ]
        self.table = FastList(
            self, cols, height=20,
            on_double_click=self._open_edit_for_row)
        self.table.pack(fill="both", expand=True, padx=30, pady=(0, 16))

        # Right-click on the underlying Treeview → context menu (bulk actions)
        # We bind on the inner tree widget so the click hits the actual rows;
        # binding on the FastList frame would miss row coordinates.
        self.table.tree.bind("<Button-3>", self._on_right_click)
        # Keyboard rating: with one or more rows selected, pressing
        # 0/1/2/3/4/5 sets the rating directly. Power-DJ workflow —
        # blast through 100 tracks rating in 30s.
        for k in range(0, 6):
            self.table.tree.bind(
                f"<Key-{k}>",
                lambda _e, r=k: self._kbd_set_rating(r))

        self._sync_throttle = UiThrottle(self, interval_ms=100)

    def _build_extra_filters(self):
        """Heavy filters (genre Entry + rating OptionMenu) built lazily so
        the page-switch paints under 500ms even on a slow box."""
        if self.genre_entry is not None:
            return
        ctk.CTkLabel(self._search_frame, text="Genre:",
                     text_color=COLORS["text_dim"]
                     ).pack(side="left", padx=(8, 2))
        self.genre_entry = ctk.CTkEntry(
            self._search_frame, width=110, height=36,
            placeholder_text="e.g. techno",
            fg_color=COLORS["bg_input"], border_color=COLORS["bg_input"],
            text_color=COLORS["text"])
        self.genre_entry.pack(side="left", padx=(0, 6), pady=8)
        self.genre_entry.bind("<Return>", lambda e: self._search())

        ctk.CTkLabel(self._search_frame, text="Min ★:",
                     text_color=COLORS["text_dim"]
                     ).pack(side="left", padx=(8, 2))
        ctk.CTkOptionMenu(
            self._search_frame, width=70, height=36,
            variable=self.rating_var,
            values=["any", "1+", "2+", "3+", "4+", "5"],
            fg_color=COLORS["bg_input"], button_color=COLORS["accent"],
            dropdown_fg_color=COLORS["bg_card"],
            command=lambda _v: self._search(),
        ).pack(side="left", padx=(0, 6), pady=8)

    # ── Refresh / search ─────────────────────────────────────────

    def on_show(self):
        # Defer — let the page paint first, then run the DB query off-thread
        self.after_idle(self._refresh)

    def on_hide(self):
        # Nothing to cancel — Treeview rendering is synchronous and fast
        pass

    def _refresh(self):
        self.count_label.configure(text="Chargement…")
        threading.Thread(target=self._fetch_all_thread, daemon=True).start()

    def _fetch_all_thread(self):
        try:
            tracks = all_tracks(get_connection())
        except Exception as e:
            log_error("library refresh failed", e)
            tracks = []
        self.after(0, lambda t=tracks: self._on_fetched(t))

    def _search(self):
        text = self.search_entry.get().strip()
        key = self.key_entry.get().strip()
        try:
            bmin = float(self.bpm_min.get()) if self.bpm_min.get().strip() else 0
        except ValueError:
            bmin = 0
        try:
            bmax = float(self.bpm_max.get()) if self.bpm_max.get().strip() else 999
        except ValueError:
            bmax = 999
        # genre_entry is built lazily — empty filter if not ready yet
        genre = self.genre_entry.get().strip() if self.genre_entry else ""
        rating_sel = self.rating_var.get()
        try:
            min_rating = 0 if rating_sel == "any" else int(rating_sel.rstrip("+"))
        except ValueError:
            min_rating = 0

        self.count_label.configure(text="Recherche…")
        threading.Thread(target=self._search_thread,
                         args=(text, key, bmin, bmax, genre, min_rating),
                         daemon=True).start()

    def _search_thread(self, text, key, bmin, bmax, genre="", min_rating=0):
        try:
            tracks = search_tracks(get_connection(), text=text, key=key,
                                    bpm_min=bmin, bpm_max=bmax)
            # Genre + rating filters applied client-side — they're cheap on
            # the already-filtered DB result set, no point pushing into SQL
            if genre:
                g = genre.lower()
                tracks = [t for t in tracks
                          if g in (t.get("genre") or "").lower()]
            if min_rating > 0:
                tracks = [t for t in tracks
                          if int(t.get("rating") or 0) >= min_rating]
        except Exception as e:
            log_error("library search failed", e)
            tracks = []
        self.after(0, lambda t=tracks: self._on_fetched(t, search=True))

    def _on_fetched(self, tracks: list[dict], search: bool = False):
        self._tracks = tracks
        self._render_table()
        suffix = "results" if search else "tracks"
        self.count_label.configure(text=f"{len(self._visible_tracks())} {suffix}")

    def _visible_tracks(self) -> list[dict]:
        """Apply inline view toggles (duplicates / unrated) on top of
        whatever the last DB query returned."""
        rows = self._tracks
        if self._unrated_only:
            rows = [t for t in rows if not t.get("rating")]
        if self._dup_only:
            # Reuse engine.find_duplicates rules — flatten its groups
            try:
                groups = find_duplicates(get_connection())
            except Exception:
                groups = []
            dup_paths = {r["path"] for g in groups for r in g}
            rows = [t for t in rows if t["path"] in dup_paths]
        return rows

    def _render_table(self):
        tracks = self._visible_tracks()
        rows = []
        tags = []
        for t in tracks:
            stars = "★" * int(t.get("rating") or 0) or "—"
            corrupt = bool(t.get("corrupt"))
            # Prefix corrupt rows with ⚠ in the Title column so the
            # user sees them at a glance — clickable, then run Repair.
            title = (t["title"] or "?")[:60]
            if corrupt:
                title = f"⚠  {title}"
            rows.append((
                title,
                f"{(t['bpm'] or 0):.0f}{'🔒' if t.get('bpm_locked') else ''}",
                t["key"] or "?",
                t["camelot"] or "?",
                f"{(t['energy'] or 0):.1f}",
                stars,
                (t.get("genre") or "")[:24],
                _format_duration(t.get("duration")),
            ))
            tags.append(("err",) if corrupt else ())
        self.table.set_rows(rows, row_tags=tags)
        n_corrupt = sum(1 for t in tracks if t.get("corrupt"))
        suffix = []
        if self._dup_only:
            suffix.append("doublons")
        if self._unrated_only:
            suffix.append("non notés")
        if n_corrupt:
            suffix.append(f"⚠ {n_corrupt} corrompu(s)")
        self.count_label.configure(
            text=f"{len(tracks)} tracks"
                 f"{' · ' + ' · '.join(suffix) if suffix else ''}")

    def _toggle_dup(self):
        self._dup_only = bool(self.dup_toggle.get())
        self._render_table()

    def _toggle_unrated(self):
        self._unrated_only = bool(self.unrated_toggle.get())
        self._render_table()

    def _open_edit_for_row(self, row: tuple):
        """Find the track dict matching the clicked row and open editor."""
        title_shown = row[0]
        match = next(
            (t for t in self._tracks
             if (t["title"] or "?")[:60] == title_shown),
            None,
        )
        if match:
            self._edit_track(match)

    def _edit_track(self, track: dict):
        """Open the TrackEditor on a track dict (used by double-click and
        right-click → Modifier)."""
        from app.ui.track_editor import TrackEditor
        TrackEditor(self.winfo_toplevel(), track, on_save=self._refresh)

    def _export(self):
        """Open the export dialog. If rows are selected → export only those,
        otherwise export everything currently visible."""
        from app.ui.export_dialog import ExportDialog
        sel = self._selected_tracks()
        tracks = sel if sel else self._visible_tracks()
        ExportDialog(self.winfo_toplevel(), tracks,
                      default_name="Ultimate DJ — Library")

    # ── Right-click context menu (bulk actions) ───────────────

    def _selected_tracks(self) -> list[dict]:
        """Resolve the FastList's currently selected rows back to track dicts."""
        selected = self.table.selected_rows()
        if not selected:
            return []
        # FastList rows[0] is the (truncated) title; we match against the
        # same truncation rule we used to build the rows
        shown_titles = {r[0] for r in selected}
        return [t for t in self._visible_tracks()
                if (t["title"] or "?")[:60] in shown_titles]

    def _on_right_click(self, event):
        import tkinter as tk
        # If the user right-clicked on a row that wasn't selected, select
        # it first — matches Explorer / iTunes / Rekordbox behaviour
        row_id = self.table.tree.identify_row(event.y)
        if row_id and row_id not in self.table.tree.selection():
            self.table.tree.selection_set(row_id)

        sel = self._selected_tracks()
        n = len(sel)
        if n == 0:
            return

        menu = tk.Menu(self, tearoff=0,
                        bg=COLORS["bg_card"], fg=COLORS["text"],
                        activebackground=COLORS["accent"],
                        activeforeground=COLORS["bg_dark"],
                        bd=0)

        # Edit only when one row is selected (the editor is single-track)
        if n == 1:
            menu.add_command(
                label="Modifier (rating, BPM, tags…)",
                command=lambda t=sel[0]: self._edit_track(t))
            menu.add_separator()

        rating_menu = tk.Menu(menu, tearoff=0,
                               bg=COLORS["bg_card"], fg=COLORS["text"],
                               activebackground=COLORS["accent"],
                               activeforeground=COLORS["bg_dark"], bd=0)
        for r in range(0, 6):
            stars = "★" * r + "☆" * (5 - r) if r else "(0) effacer"
            rating_menu.add_command(
                label=stars,
                command=lambda rv=r: self._bulk_set_rating(sel, rv))
        menu.add_cascade(label=f"Rating ({n} track{'s' if n>1 else ''})",
                          menu=rating_menu)

        menu.add_command(
            label="Définir le genre…",
            command=lambda: self._bulk_set_genre(sel))
        menu.add_command(
            label="Exporter la sélection…",
            command=lambda: self._bulk_export(sel))
        menu.add_separator()
        # Re-run librosa analysis on the selected track(s). Useful when
        # the DJ saved a wrong BPM by accident — re-analyse clears the
        # bpm_locked flag first, so the fresh detection wins this time.
        menu.add_command(
            label=f"Réanalyser ({n} track{'s' if n>1 else ''})",
            command=lambda: self._bulk_reanalyse(sel))
        menu.add_separator()
        menu.add_command(
            label="Retirer de la bibliothèque (DB seule)",
            command=lambda: self._bulk_remove(sel, delete_files=False))
        menu.add_command(
            label="Supprimer fichier(s) ET DB",
            command=lambda: self._bulk_remove(sel, delete_files=True))

        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _kbd_set_rating(self, rating: int) -> str:
        """Keyboard 0-5 rating shortcut on the Library FastList."""
        sel = self._selected_tracks()
        if not sel:
            return "break"
        self._bulk_set_rating(sel, rating)
        try:
            from app.ui.toast import show_toast
            label = "★" * rating if rating else "0★"
            show_toast(self.winfo_toplevel(),
                        f"{len(sel)} track(s) → {label}",
                        kind="success", duration_ms=1500)
        except Exception:
            pass
        return "break"

    def _bulk_set_rating(self, tracks: list[dict], rating: int):
        conn = get_connection()
        for t in tracks:
            try:
                set_rating(conn, t["path"], rating)
            except Exception as e:
                log_error(f"set_rating failed for {t['path']}", e)
        self._refresh()

    def _bulk_set_genre(self, tracks: list[dict]):
        from tkinter import simpledialog
        # Pre-fill if all selected tracks share the same genre
        existing = {(t.get("genre") or "").strip() for t in tracks}
        initial = next(iter(existing)) if len(existing) == 1 else ""
        genre = simpledialog.askstring(
            "Définir le genre",
            f"Genre pour {len(tracks)} track(s) :",
            initialvalue=initial,
            parent=self.winfo_toplevel())
        if genre is None:  # user cancelled
            return
        conn = get_connection()
        for t in tracks:
            try:
                set_genre(conn, t["path"], genre)
            except Exception as e:
                log_error(f"set_genre failed for {t['path']}", e)
        self._refresh()

    def _bulk_export(self, tracks: list[dict]):
        from app.ui.export_dialog import ExportDialog
        ExportDialog(self.winfo_toplevel(), tracks,
                      default_name=f"Ultimate DJ — {len(tracks)} tracks")

    def _bulk_reanalyse(self, tracks: list[dict]):
        """Force a fresh librosa analysis on the selected track(s).

        Use case: the DJ tapped/manually overrode the BPM by accident
        and locked it — this re-runs the engine analyser, clears the
        bpm_locked flag so the new value wins, and updates the row.
        Runs in a background thread because librosa.load can take 1-2s
        per track on a 5-min file.
        """
        if not tracks:
            return
        n = len(tracks)
        self.sync_status.configure(
            text=f"Réanalyse 0/{n}…",
            text_color=COLORS["accent"])

        def work():
            conn = get_connection()
            done = errs = 0
            for i, t in enumerate(tracks, 1):
                path = t.get("path")
                if not path:
                    continue
                try:
                    # Drop the lock so the new analysis isn't blocked
                    # by upsert_track's bpm_locked CASE expression.
                    conn.execute(
                        "UPDATE tracks SET bpm_locked = 0 WHERE path = ?",
                        (path,))
                    conn.commit()
                    info = analyze_track(path)
                    upsert_track(conn, info)
                    # write_tags is a no-op unless the user opted in
                    # via Settings → Interop, so this never mutates
                    # files by accident.
                    write_tags(path, info["bpm"], info["key"])
                    done += 1
                except Exception as e:
                    errs += 1
                    log_error(f"reanalyse failed for {path}", e)
                # Live status update — throttle so we don't flood the UI
                self._sync_throttle.call(
                    lambda i=i, n=n: self.sync_status.configure(
                        text=f"Réanalyse {i}/{n}…",
                        text_color=COLORS["accent"]))
            self.after(0, lambda d=done, e=errs:
                       self.sync_status.configure(
                           text=f"Réanalyse terminée — "
                                f"{d} ok, {e} erreurs.",
                           text_color=COLORS["success"] if e == 0
                           else COLORS["warning"]))
            self.after(0, self._refresh)

        threading.Thread(target=work, daemon=True).start()

    def _bulk_remove(self, tracks: list[dict], *, delete_files: bool):
        if delete_files:
            msg = (f"Supprimer {len(tracks)} fichier(s) du disque ET les "
                   f"déplacer vers la corbeille de la bibliothèque ?\n\n"
                   f"Le fichier disque est irrécupérable, mais la fiche DB "
                   f"reste restaurable depuis la corbeille pendant 30 jours.")
        else:
            msg = (f"Déplacer {len(tracks)} track(s) vers la corbeille ?\n\n"
                   f"Les fichiers restent sur le disque. Tu peux annuler via "
                   f"la corbeille de la bibliothèque pendant 30 jours.")
        if not confirm("Confirmer", msg):
            return

        # Snapshot DB before any destructive op — recovery in 1 click
        # if the user changes their mind even after the trash TTL.
        try:
            from app.engine.backup import force_snapshot_before_destructive
            force_snapshot_before_destructive(
                f"bulk_remove({len(tracks)} tracks, "
                f"files={delete_files})")
        except Exception:
            pass

        conn = get_connection()
        from app.engine.library import trash_tracks
        paths = [t["path"] for t in tracks if t.get("path")]
        n_trashed = 0
        n_files = 0
        n_fail = 0

        # Move to trash FIRST (DB-side, atomic), THEN delete files if asked
        try:
            n_trashed = trash_tracks(conn, paths, file_deleted=delete_files)
        except Exception as e:
            log_error(f"trash_tracks failed", e)

        if delete_files:
            import os
            for p in paths:
                try:
                    if os.path.isfile(p):
                        os.remove(p)
                        n_files += 1
                except OSError as e:
                    n_fail += 1
                    log_error(f"file delete failed: {p}", e)

        log_info(f"bulk_remove: trashed={n_trashed}, "
                 f"files_deleted={n_files}, fail={n_fail}")
        # Show toast with Undo action (only meaningful for soft-delete)
        try:
            from app.ui.toast import show_toast
            if delete_files:
                show_toast(
                    self.winfo_toplevel(),
                    f"{n_files} fichier(s) supprimé(s) · {n_trashed} méta dans la corbeille")
            else:
                show_toast(
                    self.winfo_toplevel(),
                    f"{n_trashed} track(s) déplacé(s) vers la corbeille",
                    action_label="Annuler",
                    action=lambda: self._undo_trash(paths))
        except Exception:
            pass
        self._refresh()

    def _undo_trash(self, paths: list[str]):
        """Restore the most recent bulk_remove. Called from the toast."""
        from app.engine.library import restore_from_trash
        try:
            n = restore_from_trash(get_connection(), paths)
        except Exception as e:
            log_error("restore_from_trash failed", e)
            return
        log_info(f"undo_trash: restored {n} tracks")
        try:
            from app.ui.toast import show_toast
            show_toast(self.winfo_toplevel(),
                       f"{n} track(s) restaurée(s)")
        except Exception:
            pass
        self._refresh()

    def _open_duplicates(self):
        """Open the modal duplicate resolver — pick which copy to keep
        per group, delete the rest from disk + DB."""
        from app.ui.duplicates import DuplicatesWindow
        DuplicatesWindow(self.winfo_toplevel())

    # ── Sync ──────────────────────────────────────────────────────

    def _sync(self):
        from app.config import get_music_roots
        folders = get_music_roots()
        self.sync_btn.configure(state="disabled", text="Scanning…")
        self.sync_status.configure(
            text="Scanning folders…", text_color=COLORS["accent"])
        threading.Thread(target=self._sync_thread,
                         args=(folders,), daemon=True).start()

    def _sync_thread(self, folders: list[str]):
        import time
        from app.engine import tasks as task_registry
        task = task_registry.register(
            "Sync Library", message="scan du dossier…")
        try:
            conn = get_connection()
            result = sync_library(conn, folders)
            new_paths = result["new"]
            removed = result["orphans_removed"]
            total = result["total"]
            log_info(f"Sync: {len(new_paths)} new, {removed} orphans, "
                     f"{total} files on disk")
            done = 0
            errs = 0
            n = len(new_paths)
            t_start = time.time()
            if n == 0:
                task_registry.complete(
                    task.id, success=True,
                    message=f"Aucun nouveau fichier · "
                            f"{removed} orphelins retirés")
            else:
                task_registry.update(
                    task.id, progress=0.0,
                    message=f"0/{n} nouveaux à analyser")
            from app.engine import repair as repair_engine
            from app.engine.library import mark_corrupt
            for i, path in enumerate(new_paths, 1):
                # Cheap container sanity check — catches the legacy WAV
                # corruption (RIFF buried under ID3 prefix) BEFORE we
                # try librosa.load on a broken file. Sub-millisecond per
                # track since it only reads the first 256 KB.
                try:
                    diag = repair_engine.inspect(path)
                except Exception:
                    diag = {"status": "ok"}
                if diag.get("status") == "corrupt":
                    # Don't analyse — the file would either fail or
                    # produce garbage. Flag it for the Repair tool.
                    try:
                        # Materialise a placeholder DB row so the user
                        # SEES the corrupt track in Library
                        upsert_track(conn, {
                            "path": path,
                            "title": Path(path).stem,
                            "bpm": 0, "key": "?", "camelot": "?",
                            "energy": 0, "duration": 0,
                            "key_confidence": 0,
                        })
                        mark_corrupt(conn, path, True)
                    except Exception as e:
                        log_error(f"corrupt-flag failed: {path}", e)
                    errs += 1
                    log_warning(f"sync: corrupt container detected — {path}")
                    n = len(new_paths)
                    self._sync_throttle.call(
                        lambda i=i, n=n: self.sync_status.configure(
                            text=f"Analysing {i}/{n} (corrupt skipped)…",
                            text_color=COLORS["warning"]))
                    continue
                try:
                    info = analyze_track(path)
                    write_tags(path, info["bpm"], info["key"])
                    upsert_track(conn, info)
                    # Whatever was previously flagged as corrupt and now
                    # parses cleanly = repaired since last scan
                    mark_corrupt(conn, path, False)
                    done += 1
                except Exception as e:
                    errs += 1
                    log_error(f"sync analyse failed: {path}", e)
                # Status with rolling ETA — based on average per-track
                # time so far × tracks remaining. Updates throttled to
                # 100ms so we never flood the mainloop.
                elapsed = time.time() - t_start
                avg = elapsed / max(1, i)
                eta_s = avg * (n - i)
                eta = _format_eta(eta_s)
                self._sync_throttle.call(
                    lambda i=i, n=n, eta=eta: self.sync_status.configure(
                        text=f"Analysing {i}/{n} · ETA {eta}",
                        text_color=COLORS["accent"]))
                # Mirror to activity tray (every 5 tracks — cheap)
                if i % 5 == 0 or i == n:
                    task_registry.update(
                        task.id, progress=i / n, eta_s=eta_s,
                        message=f"{i}/{n}  ·  {Path(path).name[:32]}")
            if n > 0:
                task_registry.complete(
                    task.id, success=(errs == 0),
                    message=f"+{done} nouveaux, -{removed} orphelins, "
                            f"{errs} erreurs")
            msg = (f"Sync done — +{done} new, "
                   f"-{removed} removed, {errs} errors")
            self.after(0, lambda: self.sync_status.configure(
                text=msg, text_color=COLORS["success"] if errs == 0
                else COLORS["warning"]))
        except Exception as e:
            log_error("sync_library crashed", e)
            self.after(0, lambda: self.sync_status.configure(
                text=f"Sync error: {e}", text_color=COLORS["error"]))
            try:
                task_registry.complete(
                    task.id, success=False,
                    message=f"Erreur : {str(e)[:60]}")
            except Exception:
                pass
        finally:
            self.after(0, lambda: self.sync_btn.configure(
                state="normal", text="Sync Library"))
            self.after(0, self._refresh)
