# ruff: noqa: F401
from __future__ import annotations

import customtkinter as ctk
from tkinter import filedialog

from app.config import COLORS, THEMES, load_config, save_config, get_ffmpeg, get_node, apply_theme


class MaintenanceMixin:
    """File-repair section, log journal, repair history, .bak purge,
    About + save button."""

    def _build_repair_section(self, scroll):
        # ── Réparation fichiers ─────────────────────────────
        # An older bug wrote MP3-style ID3 tags into WAV/FLAC/M4A files
        # via the wrong mutagen wrapper, prepending ID3 bytes BEFORE the
        # container's magic header. Rekordbox 7 / Engine DJ refuse to
        # open those files. This tool scans the music folder, locates
        # the real magic, and rewrites the file from there. No physical
        # backups (saves disk space) — instead each repair is logged in
        # data/repair_history.json so the user has an audit trail.
        self._section(scroll, "Réparation des fichiers audio")
        repair_card = ctk.CTkFrame(scroll, fg_color=COLORS["bg_card"],
                                    corner_radius=8)
        repair_card.pack(fill="x", pady=3)
        ctk.CTkLabel(
            repair_card,
            text="Si Rekordbox / Engine refuse d'ouvrir tes WAV / FLAC / M4A "
                 "après un scan, ils ont été corrompus par une ancienne "
                 "version de l'analyse. Cet outil répare les deux dégâts "
                 "connus : octets ID3 AVANT l'en-tête (v1) et chunk id3 "
                 "APRÈS le chunk data des WAV (v2 — le tail retiré est "
                 "gardé dans data/repair_tails, undo possible). "
                 "Historique : data/repair_history.json.",
            font=ctk.CTkFont(size=11),
            text_color=COLORS["text_dim"],
            justify="left", wraplength=720
        ).pack(anchor="w", padx=12, pady=(10, 4))

        repair_row = ctk.CTkFrame(repair_card, fg_color="transparent")
        repair_row.pack(fill="x", padx=12, pady=(0, 4))
        ctk.CTkButton(
            repair_row, text="Diagnostiquer", width=140, height=32,
            fg_color=COLORS["bg_input"], hover_color=COLORS["accent"],
            text_color=COLORS["text"],
            command=lambda: self._run_repair(dry_run=True),
        ).pack(side="left", padx=(0, 6))
        ctk.CTkButton(
            repair_row, text="Réparer", width=140, height=32,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color=COLORS["error"], hover_color="#cc3333",
            text_color="white",
            command=lambda: self._run_repair(dry_run=False),
        ).pack(side="left", padx=6)

        # Right side: history + cleanup of legacy .bak files
        ctk.CTkButton(
            repair_row, text="Voir l'historique", width=140, height=32,
            fg_color=COLORS["bg_input"], hover_color=COLORS["accent"],
            text_color=COLORS["text"],
            command=self._show_repair_history,
        ).pack(side="right", padx=6)
        ctk.CTkButton(
            repair_row, text="Nettoyer .bak", width=140, height=32,
            fg_color=COLORS["bg_input"], hover_color=COLORS["warning"],
            text_color=COLORS["text"],
            command=self._purge_bak_files,
        ).pack(side="right", padx=(6, 0))

        self.repair_status = ctk.CTkLabel(
            repair_card, text="", font=ctk.CTkFont(size=11),
            text_color=COLORS["text_dim"], wraplength=720, justify="left")
        self.repair_status.pack(anchor="w", padx=12, pady=(4, 10))


    def _build_journal_about(self, scroll):
        # ── Journal (messages & erreurs) ─────────────────────
        # Le backend existe depuis toujours (logger.py → data/errors.log,
        # rotation 5×2 Mo) ; cette section le rend visible dans l'app.
        self._section(scroll, "Journal (messages & erreurs)")
        log_card = ctk.CTkFrame(scroll, fg_color=COLORS["bg_card"],
                                 corner_radius=8)
        log_card.pack(fill="x", pady=3)
        from pathlib import Path as _P
        from app.logger import get_log_path
        _lp = _P(get_log_path())
        _lsz = _lp.stat().st_size / 1024 if _lp.exists() else 0
        ctk.CTkLabel(
            log_card,
            text=f"{_lp}  ·  {_lsz:.0f} Ko  ·  rotation 5 × 2 Mo",
            font=ctk.CTkFont(size=11), text_color=COLORS["text_dim"],
        ).pack(anchor="w", padx=12, pady=(10, 4))
        log_row = ctk.CTkFrame(log_card, fg_color="transparent")
        log_row.pack(fill="x", padx=12, pady=(0, 10))
        ctk.CTkButton(
            log_row, text="Voir le journal", width=160, height=32,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
            text_color=COLORS["bg_dark"],
            command=lambda: self._show_log_journal(None),
        ).pack(side="left", padx=(0, 6))
        ctk.CTkButton(
            log_row, text="Erreurs seulement", width=150, height=32,
            fg_color=COLORS["bg_input"], hover_color=COLORS["error"],
            text_color=COLORS["text"],
            command=lambda: self._show_log_journal("ERROR"),
        ).pack(side="left", padx=6)
        ctk.CTkButton(
            log_row, text="Ouvrir le dossier", width=140, height=32,
            fg_color=COLORS["bg_input"], hover_color=COLORS["accent"],
            text_color=COLORS["text"],
            command=lambda: __import__("os").startfile(str(_lp.parent)),
        ).pack(side="left", padx=6)

        # ── About ────────────────────────────────────────────
        self._section(scroll, "About")
        about = ctk.CTkFrame(scroll, fg_color=COLORS["bg_card"], corner_radius=8)
        about.pack(fill="x", pady=3)
        ctk.CTkLabel(about,
                     text="Ultimate DJ  ·  v1.3",
                     font=ctk.CTkFont(size=14, weight="bold"),
                     text_color=COLORS["accent"]
                     ).pack(anchor="w", padx=12, pady=(10, 0))
        ctk.CTkLabel(about,
                     text="DJ library manager · BPM/Key/Energy detection · "
                          "Spotify→YT download · Setlist auto-build",
                     font=ctk.CTkFont(size=11),
                     text_color=COLORS["text_dim"]
                     ).pack(anchor="w", padx=12, pady=(0, 10))

        # ── Save button ──────────────────────────────────────
        ctk.CTkButton(
            scroll, text="Save Settings", height=40, width=180,
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
            text_color=COLORS["bg_dark"],
            command=self._save).pack(pady=20)

        self.save_label = ctk.CTkLabel(scroll, text="",
                                        font=ctk.CTkFont(size=12))
        self.save_label.pack()

    def _run_repair(self, *, dry_run: bool):
        """Walk every configured music folder, fix any pre-magic garbage.
        Threaded so the UI stays alive on a 5000-track library."""
        import threading
        from app.config import get_music_roots
        from app.engine import repair as repair_engine

        roots = get_music_roots()
        if not roots:
            self.repair_status.configure(
                text="Aucun dossier musique configuré.",
                text_color=COLORS["warning"])
            return

        verb = "Diagnostic" if dry_run else "Réparation"
        self.repair_status.configure(
            text=f"{verb} en cours…", text_color=COLORS["accent"])

        def work():
            totals = {"scanned": 0, "ok": 0, "corrupt": 0,
                      "trailing_corrupt": 0, "review": 0,
                      "repaired": 0, "errors": 0}
            for root in roots:
                def on_progress(name, cur, total, _root=root):
                    self.after(0, lambda n=name, c=cur, t=total:
                                self.repair_status.configure(
                                    text=f"{verb} {c}/{t} · {n[:60]}",
                                    text_color=COLORS["accent"]))
                summary = repair_engine.scan_folder(
                    root, on_progress=on_progress, dry_run=dry_run)
                for k in totals:
                    totals[k] += summary.get(k, 0)
                # Persist the verdicts so the Library ⚠ badges reflect
                # reality — a Diagnostiquer flags, a Réparer clears.
                try:
                    from app.engine.library import (get_connection,
                                                      mark_corrupt)
                    conn = get_connection()
                    for d in summary.get("details", []):
                        p = d.get("path")
                        if not p:
                            continue
                        if d.get("repaired"):
                            mark_corrupt(conn, p, False)
                        elif d.get("status") in ("corrupt",
                                                  "trailing_garbage",
                                                  "riff_size_mismatch",
                                                  "review"):
                            mark_corrupt(conn, p, True)
                except Exception as e:
                    from app.logger import log_warning
                    log_warning(f"repair scan: flag persist failed: {e}")

            if dry_run:
                msg = (f"Diagnostic terminé — {totals['scanned']} fichiers, "
                       f"{totals['corrupt']} corrompus à réparer "
                       f"(dont {totals['trailing_corrupt']} structure v2), "
                       f"{totals['review']} à examiner, "
                       f"{totals['errors']} erreurs.")
                color = (COLORS["warning"] if totals["corrupt"]
                         else COLORS["success"])
            else:
                msg = (f"Terminé — {totals['repaired']} fichiers réparés, "
                       f"{totals['ok']} déjà sains, "
                       f"{totals['review']} à examiner (non touchés), "
                       f"{totals['errors']} erreurs. "
                       f"Historique : data/repair_history.json")
                color = (COLORS["success"] if totals["repaired"] or not totals["errors"]
                         else COLORS["warning"])
            self.after(0, lambda: self.repair_status.configure(
                text=msg, text_color=color))

        threading.Thread(target=work, daemon=True).start()


    def _show_log_journal(self, level: str | None = None):
        """Popup viewer over data/errors.log — colored by level,
        newest at the bottom, refresh in place."""
        import tkinter as tk
        from app.logger import tail_log

        win = ctk.CTkToplevel(self)
        win.title("Journal — messages & erreurs"
                  + (f" ({level})" if level else ""))
        win.geometry("940x560")
        win.configure(fg_color=COLORS["bg_dark"])
        win.transient(self.winfo_toplevel())

        txt = tk.Text(
            win, bg=COLORS["bg_card"], fg=COLORS["text"],
            insertbackground=COLORS["text"], relief="flat",
            font=("Consolas", 9), wrap="none")
        txt.pack(fill="both", expand=True, padx=14, pady=(14, 6))
        txt.tag_configure("ERROR", foreground="#ff5c7a")
        txt.tag_configure("WARNING", foreground="#ffb020")
        txt.tag_configure("INFO", foreground=COLORS["text_dim"])

        def _fill():
            txt.configure(state="normal")
            txt.delete("1.0", "end")
            for line in tail_log(400, level=level):
                tag = ("ERROR" if "[ERROR" in line
                       else "WARNING" if "[WARNING" in line
                       else "INFO")
                txt.insert("end", line + "\n", tag)
            txt.see("end")
            txt.configure(state="disabled")

        _fill()

        bar = ctk.CTkFrame(win, fg_color="transparent")
        bar.pack(fill="x", padx=14, pady=(0, 12))
        ctk.CTkButton(
            bar, text="Rafraîchir", width=110, height=30,
            fg_color=COLORS["bg_input"], hover_color=COLORS["accent"],
            text_color=COLORS["text"], command=_fill,
        ).pack(side="left")
        ctk.CTkButton(
            bar, text="Fermer", width=100, height=30,
            fg_color=COLORS["bg_card"], hover_color=COLORS["bg_input"],
            text_color=COLORS["text"], command=win.destroy,
        ).pack(side="right")


    def _show_repair_history(self):
        """Open a popup listing the most recent repairs."""
        from app.engine import repair as repair_engine
        from datetime import datetime
        import tkinter as tk

        entries = repair_engine.history(limit=200)

        win = ctk.CTkToplevel(self)
        win.title("Historique des réparations")
        win.geometry("780x520")
        win.configure(fg_color=COLORS["bg_dark"])
        win.transient(self.winfo_toplevel())

        ctk.CTkLabel(
            win, text="Historique des réparations",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color=COLORS["text"]
        ).pack(anchor="w", padx=20, pady=(16, 4))
        ctk.CTkLabel(
            win,
            text=f"{len(entries)} entrée(s) — la plus récente en haut. "
                 f"Source : data/repair_history.json",
            font=ctk.CTkFont(size=11),
            text_color=COLORS["text_dim"]
        ).pack(anchor="w", padx=20, pady=(0, 8))

        # Plain Tk Text — fast, scrollable, monospace
        body = tk.Text(
            win, bg=COLORS["bg_card"], fg=COLORS["text"],
            font=("Consolas", 10), bd=0, relief="flat",
            highlightthickness=0, wrap="none")
        body.pack(fill="both", expand=True, padx=20, pady=(0, 8))

        if not entries:
            body.insert("end", "(aucune réparation enregistrée pour le moment)")
        else:
            body.insert(
                "end",
                f"{'Date':<19}  {'Octets':>8}  {'Avant':>9}  {'Après':>9}  Fichier\n"
                f"{'-'*19}  {'-'*8}  {'-'*9}  {'-'*9}  {'-'*40}\n")
            for e in entries:
                ts = datetime.fromtimestamp(e.get("ts", 0)).strftime(
                    "%Y-%m-%d %H:%M:%S")
                body.insert(
                    "end",
                    f"{ts}  {e.get('stripped', 0):>8}  "
                    f"{e.get('size_before', 0):>9}  {e.get('size_after', 0):>9}  "
                    f"{e.get('path', '')}\n")
        body.configure(state="disabled")

        ctk.CTkButton(
            win, text="Fermer", width=100, height=32,
            fg_color=COLORS["bg_card"], hover_color=COLORS["bg_input"],
            text_color=COLORS["text"],
            command=win.destroy
        ).pack(pady=(0, 16))


    def _purge_bak_files(self):
        """Delete leftover *.bak files from older app versions to free disk space."""
        import threading
        from app.config import get_music_roots
        from app.engine import repair as repair_engine
        from app.ui.helpers import confirm

        roots = get_music_roots()
        if not roots:
            self.repair_status.configure(
                text="Aucun dossier musique configuré.",
                text_color=COLORS["warning"])
            return

        if not confirm(
                "Supprimer les .bak ?",
                f"Tous les fichiers *.bak des dossiers configurés vont être "
                f"définitivement supprimés. Cette action est irréversible.\n\n"
                f"Dossiers : {', '.join(roots)}\n\nContinuer ?"):
            return

        self.repair_status.configure(
            text="Suppression des .bak en cours…",
            text_color=COLORS["accent"])

        def work():
            removed = 0
            for root in roots:
                removed += repair_engine.purge_backups(root)
            self.after(0, lambda r=removed: self.repair_status.configure(
                text=f"{r} fichier(s) .bak supprimé(s).",
                text_color=COLORS["success"] if removed
                else COLORS["text_dim"]))

        threading.Thread(target=work, daemon=True).start()

