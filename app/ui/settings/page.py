# ruff: noqa: F401
from __future__ import annotations

import customtkinter as ctk
from tkinter import filedialog

from app.config import COLORS, THEMES, load_config, save_config, get_ffmpeg, get_node, apply_theme

from app.ui.settings._ai_sections import AISectionsMixin
from app.ui.settings._ai_workers import AIWorkersMixin
from app.ui.settings._general import GeneralMixin
from app.ui.settings._maintenance import MaintenanceMixin


class SettingsPage(GeneralMixin, AISectionsMixin, AIWorkersMixin,
                   MaintenanceMixin, ctk.CTkFrame):
    def __init__(self, parent):
        super().__init__(parent, fg_color=COLORS["bg_dark"])
        self.cfg = load_config()
        self._build_ui()


    def _build_ui(self):
        ctk.CTkLabel(
            self, text="Settings",
            font=ctk.CTkFont(size=26, weight="bold"),
            text_color=COLORS["text"],
        ).pack(anchor="w", padx=30, pady=(24, 16))

        # Sticky save bar at the bottom — pack BEFORE the scroll area
        # (Tk packs in declaration order; side="bottom" pins to bottom)
        # so it never scrolls out of reach. The duplicate Save Settings
        # button further down still works as a fallback.
        save_bar = ctk.CTkFrame(self, fg_color=COLORS["bg_card"],
                                 corner_radius=0, height=52)
        save_bar.pack(side="bottom", fill="x")
        save_bar.pack_propagate(False)
        ctk.CTkButton(
            save_bar, text="💾  Save Settings", height=36, width=180,
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            text_color=COLORS["bg_dark"],
            command=self._save).pack(side="right", padx=20, pady=8)
        self.sticky_save_label = ctk.CTkLabel(
            save_bar, text="", font=ctk.CTkFont(size=11),
            text_color=COLORS["text_dim"])
        self.sticky_save_label.pack(side="right", padx=8)

        scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        scroll.pack(fill="both", expand=True, padx=30, pady=(0, 16))

        self._build_general_sections(scroll)
        self._build_repair_section(scroll)
        self._build_ai_sections(scroll)
        self._build_journal_about(scroll)

    def _make_progress_row(self, parent):
        """Build a (frame, progress_bar, step_label) widget group for
        inline progress reporting under any long-running button.

        The row is ALWAYS packed (never pack/unpack mid-task) — that
        avoided the layout reflow that was glitching the Settings page
        rendering when the Reconstruire button fired multiple progress
        updates in quick succession. When idle the bar is at 0 and the
        step label is empty, so visually the row is a thin invisible
        strip; when a worker pushes updates it fills + populates.
        """
        f = ctk.CTkFrame(parent, fg_color="transparent")
        # Always packed — the worker only mutates `bar.set()` and
        # `step.configure(text=)`, which redraws in place without a
        # parent reflow.
        f.pack(fill="x", pady=(0, 4))
        bar = ctk.CTkProgressBar(
            f, height=6,
            fg_color=COLORS["bg_input"],
            progress_color=COLORS["accent"])
        bar.set(0)
        bar.pack(fill="x", padx=12, pady=(0, 2))
        step = ctk.CTkLabel(
            f, text="", font=ctk.CTkFont(size=10),
            text_color=COLORS["text_dim"], anchor="w",
            justify="left", wraplength=720)
        step.pack(anchor="w", padx=12, pady=(0, 4))
        return f, bar, step


    def _show_progress(self, frame, bar, step, fraction: float, msg: str):
        """Thread-safe UI update — schedule on main loop. Updates bar +
        step label IN PLACE without re-packing (frame is always
        mounted, so no layout reflow)."""
        def _apply():
            try:
                if fraction is not None:
                    bar.set(max(0.0, min(1.0, fraction)))
                if msg is not None:
                    step.configure(text=msg)
            except Exception:
                pass
        try:
            self.after(0, _apply)
        except Exception:
            pass


    def _hide_progress(self, frame):
        """Reset the inline progress row to its idle visual state
        (empty label + 0 % bar) once the worker finishes. Doesn't
        pack_forget — leaving the strip in place avoids a reflow."""
        def _apply():
            try:
                # Find the bar / step children to reset
                for child in frame.winfo_children():
                    if isinstance(child, ctk.CTkProgressBar):
                        child.set(0)
                    elif isinstance(child, ctk.CTkLabel):
                        child.configure(text="")
            except Exception:
                pass
        try:
            self.after(0, _apply)
        except Exception:
            pass

    # ── 1001tracklists login (workers) ──────────────────────────


    def _render_extra_roots(self):
        """Repaint the list of secondary music sources."""
        for w in self._extras_list_frame.winfo_children():
            w.destroy()
        if not self._extra_roots:
            ctk.CTkLabel(
                self._extras_list_frame,
                text="(aucune — clique « + Ajouter » pour scanner d'autres dossiers)",
                font=ctk.CTkFont(size=10),
                text_color=COLORS["text_dim"]
            ).pack(anchor="w", pady=2)
            return
        for idx, p in enumerate(self._extra_roots):
            row = ctk.CTkFrame(self._extras_list_frame,
                                fg_color=COLORS["bg_input"],
                                corner_radius=6)
            row.pack(fill="x", pady=2)
            ctk.CTkLabel(row, text=p, anchor="w",
                         text_color=COLORS["text"],
                         font=ctk.CTkFont(size=11)
                         ).pack(side="left", fill="x", expand=True,
                                 padx=10, pady=4)
            ctk.CTkButton(
                row, text="✗", width=30, height=24,
                font=ctk.CTkFont(size=12, weight="bold"),
                fg_color="transparent", hover_color=COLORS["error"],
                text_color=COLORS["text_dim"],
                command=lambda i=idx: self._remove_extra_root(i),
            ).pack(side="right", padx=4, pady=2)


    def _add_extra_root(self):
        from tkinter import filedialog
        p = filedialog.askdirectory(
            parent=self.winfo_toplevel(),
            title="Choisir un dossier de musique supplémentaire")
        if not p:
            return
        # Avoid dupes (and the main music_root is already scanned)
        primary = (self.music_root.get() or "").strip()
        if p == primary or p in self._extra_roots:
            return
        self._extra_roots.append(p)
        self._render_extra_roots()


    def _remove_extra_root(self, idx: int):
        if 0 <= idx < len(self._extra_roots):
            del self._extra_roots[idx]
            self._render_extra_roots()


    def _refresh_system_info(self):
        ffmpeg = get_ffmpeg() or "NOT FOUND"
        node = get_node() or "NOT FOUND"
        for key, val in (("FFmpeg", ffmpeg), ("Node.js", node)):
            lbl = self._sys_labels.get(key)
            if not lbl:
                continue
            ok = "NOT" not in val
            lbl.configure(text=val,
                           text_color=COLORS["success"] if ok else COLORS["error"])


    def _on_theme_pick(self, name: str):
        """Apply the picked theme immediately (live preview)."""
        cfg = load_config()
        cfg["theme"] = name
        save_config(cfg)
        apply_theme(name)
        top = self.winfo_toplevel()
        if hasattr(top, "reload_theme"):
            self.after(50, top.reload_theme)


    def _section(self, parent, title: str):
        ctk.CTkLabel(parent, text=title,
                     font=ctk.CTkFont(size=16, weight="bold"),
                     text_color=COLORS["accent"]).pack(anchor="w", pady=(16, 6))


    def _text_row(self, parent, label: str, value: str, show: str = "") -> ctk.CTkEntry:
        frame = ctk.CTkFrame(parent, fg_color=COLORS["bg_card"], corner_radius=8)
        frame.pack(fill="x", pady=3)
        ctk.CTkLabel(frame, text=label, width=200, text_color=COLORS["text"],
                     font=ctk.CTkFont(size=12)).pack(side="left", padx=12, pady=8)
        entry = ctk.CTkEntry(frame, fg_color=COLORS["bg_input"],
                              border_color=COLORS["bg_input"],
                              text_color=COLORS["text"], show=show)
        entry.pack(side="right", fill="x", expand=True, padx=12, pady=8)
        if value:
            entry.insert(0, value)
        return entry


    def _path_row(self, parent, label: str, value: str, is_dir=False) -> ctk.CTkEntry:
        frame = ctk.CTkFrame(parent, fg_color=COLORS["bg_card"], corner_radius=8)
        frame.pack(fill="x", pady=3)
        ctk.CTkLabel(frame, text=label, width=200, text_color=COLORS["text"],
                     font=ctk.CTkFont(size=12)).pack(side="left", padx=12, pady=8)

        entry = ctk.CTkEntry(frame, fg_color=COLORS["bg_input"],
                              border_color=COLORS["bg_input"],
                              text_color=COLORS["text"])
        entry.pack(side="left", fill="x", expand=True, padx=(0, 6), pady=8)
        if value:
            entry.insert(0, value)

        def browse():
            if is_dir:
                p = filedialog.askdirectory()
            else:
                p = filedialog.askopenfilename()
            if p:
                entry.delete(0, "end")
                entry.insert(0, p)

        ctk.CTkButton(frame, text="Browse", width=70, height=30,
                       fg_color=COLORS["bg_input"], hover_color=COLORS["accent"],
                       command=browse).pack(side="right", padx=12, pady=8)
        return entry


    def _save(self):
        self.cfg["music_root"] = self.music_root.get().strip()
        self.cfg["music_roots_extra"] = list(self._extra_roots)
        self.cfg["download_folder"] = self.dl_folder.get().strip()
        self.cfg["ffmpeg_path"] = self.ffmpeg.get().strip()
        # Spotify creds → Windows Credential Manager (NOT config.json).
        from app.secrets_store import set_spotify_credentials
        set_spotify_credentials(
            self.sp_id.get().strip(),
            self.sp_secret.get().strip())
        # Make sure any legacy plaintext copy in cfg is wiped out before save
        self.cfg["spotify_client_id"] = ""
        self.cfg["spotify_client_secret"] = ""
        # mp3_quality + analysis_duration are now constants (DJ defaults)
        # Tag-write opt-in — defaults to False so we never pollute
        # Rekordbox/Engine/Serato analysis without explicit consent.
        self.cfg["write_tags_to_files"] = bool(self.write_tags_var.get())
        for cfg_key, var in self._fmt_tag_vars.items():
            self.cfg[cfg_key] = bool(var.get())
        self.cfg["setlistfm_api_key"] = self.slfm_key.get().strip()

        new_theme = self.theme_var.get()
        theme_changed = new_theme != self.cfg.get("theme")
        self.cfg["theme"] = new_theme
        save_config(self.cfg)

        if theme_changed:
            apply_theme(new_theme)
            self.save_label.configure(
                text="Settings saved — applying new theme…",
                text_color=COLORS["accent"])
            # Walk up to the App and reload the theme live
            top = self.winfo_toplevel()
            if hasattr(top, "reload_theme"):
                self.after(200, top.reload_theme)
        else:
            self.save_label.configure(text="Settings saved!",
                                       text_color=COLORS["success"])
        self.after(4000, lambda: self.save_label.configure(text=""))
