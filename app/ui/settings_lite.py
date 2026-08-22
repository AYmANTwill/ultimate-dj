"""Lite Settings — the small surface a non-technical user needs:
music/download folders, Spotify credentials, and one-click updates.
Deliberately NOT the full settings page (no AI/worker/maintenance
sections). Self-contained so the Lite build stays simple."""
from __future__ import annotations

import threading
from tkinter import filedialog

import customtkinter as ctk

from app.config import COLORS, load_config, save_config
from app.version import __version__


class SettingsLitePage(ctk.CTkFrame):
    def __init__(self, parent):
        super().__init__(parent, fg_color="transparent")
        self.cfg = load_config()

        ctk.CTkLabel(self, text="Réglages",
                     font=ctk.CTkFont(size=24, weight="bold"),
                     text_color=COLORS["text"]).pack(
            anchor="w", padx=20, pady=(16, 10))

        scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        scroll.pack(fill="both", expand=True, padx=16, pady=(0, 8))

        # ── Folders ──────────────────────────────────────────
        self._section(scroll, "Dossiers")
        self.music_root = self._folder_row(
            scroll, "Dossier de ta bibliothèque",
            self.cfg.get("music_root", ""))
        self.dl_folder = self._folder_row(
            scroll, "Dossier de téléchargement",
            self.cfg.get("download_folder", ""))

        # ── Spotify ──────────────────────────────────────────
        self._section(scroll, "Spotify (pour les playlists)")
        ctk.CTkLabel(
            scroll,
            text=("Gratuit sur developer.spotify.com. Deezer et YouTube "
                  "ne demandent rien."),
            font=ctk.CTkFont(size=10), text_color=COLORS["text_dim"],
        ).pack(anchor="w", padx=4, pady=(0, 4))
        from app.secrets_store import get_spotify_credentials
        cid, csec = get_spotify_credentials()
        self.sp_id = self._text_row(scroll, "Client ID", cid)
        self.sp_secret = self._text_row(scroll, "Client Secret", csec,
                                        show="*")

        ctk.CTkButton(scroll, text="💾 Enregistrer", width=160,
                      fg_color=COLORS["accent"],
                      command=self._save).pack(anchor="w", padx=4,
                                               pady=(6, 14))

        # ── Updates ──────────────────────────────────────────
        self._section(scroll, "Mises à jour")
        ctk.CTkLabel(scroll, text=f"Version installée : {__version__}",
                     font=ctk.CTkFont(size=11),
                     text_color=COLORS["text_dim"]).pack(
            anchor="w", padx=4)
        self._upd_status = ctk.CTkLabel(
            scroll, text="", font=ctk.CTkFont(size=11),
            text_color=COLORS["text_dim"], wraplength=680, justify="left")
        self._upd_status.pack(anchor="w", padx=4, pady=(2, 4))
        self._upd_bar = ctk.CTkProgressBar(scroll, width=320)
        self._upd_bar.set(0)
        row = ctk.CTkFrame(scroll, fg_color="transparent")
        row.pack(anchor="w", padx=4, pady=(0, 4))
        self._upd_check_btn = ctk.CTkButton(
            row, text="🔄 Vérifier les mises à jour", width=220,
            fg_color=COLORS["bg_input"], hover_color=COLORS["accent"],
            command=self._check_updates)
        self._upd_check_btn.pack(side="left")
        self._upd_install_btn = ctk.CTkButton(
            row, text="⬇ Installer", width=140,
            fg_color=COLORS["accent"], command=self._install_update)
        self._pending = None  # update info dict once found

    # ── Section / row helpers ────────────────────────────────

    def _section(self, parent, title):
        ctk.CTkLabel(parent, text=title.upper(),
                     font=ctk.CTkFont(size=11, weight="bold"),
                     text_color=COLORS["accent"]).pack(
            anchor="w", padx=4, pady=(12, 4))

    def _text_row(self, parent, label, value="", show=""):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=2)
        ctk.CTkLabel(row, text=label, width=150, anchor="w",
                     font=ctk.CTkFont(size=12),
                     text_color=COLORS["text"]).pack(side="left", padx=4)
        entry = ctk.CTkEntry(row, fg_color=COLORS["bg_input"],
                             show=show or "")
        entry.pack(side="left", fill="x", expand=True, padx=4)
        if value:
            entry.insert(0, value)
        return entry

    def _folder_row(self, parent, label, value=""):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=2)
        ctk.CTkLabel(row, text=label, width=210, anchor="w",
                     font=ctk.CTkFont(size=12),
                     text_color=COLORS["text"]).pack(side="left", padx=4)
        entry = ctk.CTkEntry(row, fg_color=COLORS["bg_input"])
        entry.pack(side="left", fill="x", expand=True, padx=4)
        if value:
            entry.insert(0, value)

        def pick():
            d = filedialog.askdirectory()
            if d:
                entry.delete(0, "end")
                entry.insert(0, d)
        ctk.CTkButton(row, text="…", width=36, command=pick,
                      fg_color=COLORS["bg_input"],
                      hover_color=COLORS["accent"]).pack(side="left", padx=4)
        return entry

    # ── Actions ──────────────────────────────────────────────

    def _save(self):
        cfg = load_config()
        cfg["music_root"] = self.music_root.get().strip()
        cfg["download_folder"] = self.dl_folder.get().strip()
        save_config(cfg)
        self.cfg = cfg
        try:
            from app.secrets_store import set_spotify_credentials
            set_spotify_credentials(self.sp_id.get().strip(),
                                    self.sp_secret.get().strip())
        except Exception as e:
            from app.logger import log_error
            log_error("lite settings: spotify save failed", e)

    def _check_updates(self):
        self._upd_status.configure(text="Vérification…",
                                   text_color=COLORS["text_dim"])
        self._upd_check_btn.configure(state="disabled")

        def work():
            from app.engine import updater
            info = updater.check_for_update()
            self.after(0, lambda: self._on_check_done(info))
        threading.Thread(target=work, daemon=True,
                         name="update-check").start()

    def _on_check_done(self, info):
        self._upd_check_btn.configure(state="normal")
        if not info:
            self._upd_status.configure(
                text="À jour ✓ (ou hors ligne).",
                text_color=COLORS["success"])
            self._upd_install_btn.pack_forget()
            return
        self._pending = info
        self._upd_status.configure(
            text=f"Nouvelle version disponible : {info['version']}",
            text_color=COLORS["accent"])
        self._upd_install_btn.configure(
            text=f"⬇ Installer {info['version']}")
        self._upd_install_btn.pack(side="left", padx=(8, 0))

    def _install_update(self):
        if not self._pending:
            return
        self._upd_install_btn.configure(state="disabled")
        self._upd_check_btn.configure(state="disabled")
        self._upd_bar.pack(anchor="w", padx=4, pady=(2, 4))
        self._upd_status.configure(text="Téléchargement…",
                                   text_color=COLORS["text_dim"])

        def work():
            from app.engine import updater
            path = updater.download_update(
                self._pending,
                progress_cb=lambda f: self.after(
                    0, lambda f=f: self._upd_bar.set(f)))
            if not path:
                self.after(0, lambda: self._upd_status.configure(
                    text="Échec du téléchargement.",
                    text_color=COLORS["error"]))
                self.after(0, lambda: self._upd_install_btn.configure(
                    state="normal"))
                return
            self.after(0, lambda: self._upd_status.configure(
                text="Installation… l'app va redémarrer.",
                text_color=COLORS["accent"]))
            ok = updater.apply_update(path)
            if ok:
                self.after(600, self._quit_for_update)
            else:
                self.after(0, lambda: self._upd_status.configure(
                    text=("Téléchargé, mais l'installation auto n'est "
                          "possible que sur l'app packagée."),
                    text_color=COLORS["warning"]))
                self.after(0, lambda: self._upd_install_btn.configure(
                    state="normal"))
        threading.Thread(target=work, daemon=True,
                         name="update-apply").start()

    def _quit_for_update(self):
        try:
            self.winfo_toplevel().destroy()
        except Exception:
            import os
            os._exit(0)
