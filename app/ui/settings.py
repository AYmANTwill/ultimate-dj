"""
Settings page — configure paths, Spotify API keys, preferences.
"""
from __future__ import annotations

import customtkinter as ctk
from tkinter import filedialog

from app.config import COLORS, THEMES, load_config, save_config, get_ffmpeg, get_node, apply_theme


class SettingsPage(ctk.CTkFrame):
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

        scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        scroll.pack(fill="both", expand=True, padx=30, pady=(0, 16))

        # ── Theme section ────────────────────────────────────
        self._section(scroll, "Appearance")
        self.theme_var = ctk.StringVar(value=self.cfg.get("theme", "Cyan Night"))
        t_frame = ctk.CTkFrame(scroll, fg_color=COLORS["bg_card"], corner_radius=8)
        t_frame.pack(fill="x", pady=3)
        ctk.CTkLabel(t_frame, text="Color theme", width=200,
                     text_color=COLORS["text"],
                     font=ctk.CTkFont(size=12)).pack(side="left", padx=12, pady=8)
        ctk.CTkOptionMenu(
            t_frame, values=list(THEMES.keys()), variable=self.theme_var,
            width=160, fg_color=COLORS["bg_input"],
            button_color=COLORS["accent"],
            dropdown_fg_color=COLORS["bg_card"],
            command=self._on_theme_pick,
        ).pack(side="right", padx=12, pady=8)

        # ── Paths section ────────────────────────────────────
        self._section(scroll, "Paths")
        self.music_root = self._path_row(scroll, "Music library folder",
                                          self.cfg.get("music_root", ""),
                                          is_dir=True)

        # Secondary music sources — Sync Library will scan these too
        extras_card = ctk.CTkFrame(scroll, fg_color=COLORS["bg_card"],
                                    corner_radius=8)
        extras_card.pack(fill="x", pady=3)
        head = ctk.CTkFrame(extras_card, fg_color="transparent")
        head.pack(fill="x", padx=12, pady=(8, 4))
        ctk.CTkLabel(head, text="Sources musique secondaires",
                     text_color=COLORS["text"],
                     font=ctk.CTkFont(size=12)
                     ).pack(side="left")
        ctk.CTkLabel(head,
                     text="(scannées par Sync Library en plus du dossier principal)",
                     font=ctk.CTkFont(size=10),
                     text_color=COLORS["text_dim"]
                     ).pack(side="left", padx=8)
        ctk.CTkButton(
            head, text="+ Ajouter", width=90, height=26,
            font=ctk.CTkFont(size=11),
            fg_color=COLORS["bg_input"], hover_color=COLORS["accent"],
            text_color=COLORS["text"],
            command=self._add_extra_root,
        ).pack(side="right")

        self._extras_list_frame = ctk.CTkFrame(extras_card, fg_color="transparent")
        self._extras_list_frame.pack(fill="x", padx=12, pady=(0, 8))
        # Initial render — calls into a helper so we can refresh after edits
        self._extra_roots: list[str] = list(
            self.cfg.get("music_roots_extra") or [])
        self._render_extra_roots()

        self.dl_folder = self._path_row(scroll, "Download folder",
                                          self.cfg.get("download_folder", ""),
                                          is_dir=True)
        self.ffmpeg = self._path_row(scroll, "FFmpeg path",
                                      self.cfg.get("ffmpeg_path", ""))

        # ── Spotify section ──────────────────────────────────
        self._section(scroll, "Spotify API")
        ctk.CTkLabel(scroll, text="Get credentials at developer.spotify.com (free)",
                     font=ctk.CTkFont(size=11), text_color=COLORS["text_dim"]
                     ).pack(anchor="w", padx=4, pady=(0, 2))
        # Creds are stored in Windows Credential Manager (DPAPI) via the
        # secrets_store wrapper — NOT in config.json. The placeholders
        # below pre-fill with whatever's currently in the keyring.
        from app.secrets_store import get_spotify_credentials
        _cid_now, _sec_now = get_spotify_credentials()
        ctk.CTkLabel(
            scroll,
            text="✓ Stockés en sécurité dans le Credential Manager Windows",
            font=ctk.CTkFont(size=10),
            text_color=COLORS["success"],
        ).pack(anchor="w", padx=4, pady=(0, 6))
        self.sp_id = self._text_row(scroll, "Client ID", _cid_now)
        self.sp_secret = self._text_row(scroll, "Client Secret", _sec_now,
                                         show="*")

        # ── Audio section (locked to DJ-grade quality) ───────
        # 320 kbps MP3 is the universal DJ standard. Lower bitrates are
        # intentionally not offered — bad audio in a club kills the vibe.
        # Analysis duration is auto-tuned (no user-facing knob).
        self.quality_var = ctk.StringVar(value="320")
        self.dur_var = ctk.StringVar(value="90")

        # ── Interop with other DJ software ────────────────────
        # By default UltimateDJ keeps everything in its own DB and never
        # touches the audio files. This means Rekordbox / Engine DJ /
        # Serato do their own analysis on import — UltimateDJ's BPM/key
        # values can't pollute theirs. Toggle this ON only if you
        # specifically want our analysis to seed the file's TBPM/TKEY
        # tags (which most DJ tools then read on import).
        self._section(scroll, "Interop avec Rekordbox / Serato / Engine")
        interop_card = ctk.CTkFrame(scroll, fg_color=COLORS["bg_card"],
                                     corner_radius=8)
        interop_card.pack(fill="x", pady=3)

        self.write_tags_var = ctk.BooleanVar(
            value=bool(self.cfg.get("write_tags_to_files", False)))
        self.write_tags_check = ctk.CTkCheckBox(
            interop_card,
            text="Écrire les tags BPM/Key dans les fichiers audio",
            variable=self.write_tags_var,
            font=ctk.CTkFont(size=12),
            text_color=COLORS["text"],
            checkbox_height=18, checkbox_width=18,
            fg_color=COLORS["accent"])
        self.write_tags_check.pack(anchor="w", padx=12, pady=(10, 4))
        # Re-paint to current state (BooleanVar+CTkCheckBox quirk)
        if self.write_tags_var.get():
            self.write_tags_check.select()
        else:
            self.write_tags_check.deselect()

        ctk.CTkLabel(
            interop_card,
            text="OFF (par défaut, recommandé) — nos analyses restent "
                 "dans la DB d'Ultimate DJ. Rekordbox fera sa propre "
                 "analyse à l'import, sans interférence.\n"
                 "ON — nos valeurs BPM/Key sont écrites dans les tags "
                 "ID3/RIFF/FLAC du fichier. Les autres DJ tools les "
                 "lisent généralement au lieu de réanalyser.",
            font=ctk.CTkFont(size=10),
            text_color=COLORS["text_dim"],
            justify="left", wraplength=720
        ).pack(anchor="w", padx=12, pady=(0, 10))

        # ── System info ──────────────────────────────────────
        self._section(scroll, "System")
        self._sys_labels: dict[str, ctk.CTkLabel] = {}
        for label in ("FFmpeg", "Node.js"):
            f = ctk.CTkFrame(scroll, fg_color=COLORS["bg_card"], corner_radius=8)
            f.pack(fill="x", pady=2)
            ctk.CTkLabel(f, text=label, width=120, text_color=COLORS["text"],
                         font=ctk.CTkFont(size=12)).pack(side="left", padx=12, pady=6)
            val_lbl = ctk.CTkLabel(f, text="(checking…)", text_color=COLORS["text_dim"],
                                    font=ctk.CTkFont(size=11))
            val_lbl.pack(side="left", padx=4, pady=6)
            self._sys_labels[label] = val_lbl
        # Defer the synchronous shutil.which lookups
        self.after_idle(self._refresh_system_info)

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
                 "version de l'analyse. Cet outil les répare directement, "
                 "sans backup .bak (l'historique est gardé dans "
                 "data/repair_history.json).",
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

        # ── AI Embeddings ────────────────────────────────────
        # Each track gets a 256-d audio fingerprint that the Mixer
        # uses to find sonically-similar tracks (independent of BPM
        # and key). Encoding the whole library is a one-shot job —
        # progress is reported via toast.
        self._section(scroll, "AI · Embeddings audio")
        ai_card = ctk.CTkFrame(scroll, fg_color=COLORS["bg_card"],
                                corner_radius=8)
        ai_card.pack(fill="x", pady=3)

        # Show backend + progress at build time; refreshed on click
        from app.engine import embeddings as _emb
        from app.engine.library import (get_connection as _gc,
                                          embedding_count as _ec)
        try:
            _done, _total = _ec(_gc())
        except Exception:
            _done, _total = 0, 0
        self._ai_status = ctk.CTkLabel(
            ai_card,
            text=f"Backend : {_emb.best_backend()}  ·  "
                 f"{_done}/{_total} tracks encodés",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["text"])
        self._ai_status.pack(anchor="w", padx=12, pady=(10, 4))

        ctk.CTkLabel(
            ai_card,
            text=("Active la similarité sonore dans le score de "
                   "transition (Mixer). Les tracks qui sonnent pareil "
                   "remontent même si le BPM / la key diffèrent."),
            font=ctk.CTkFont(size=10),
            text_color=COLORS["text_dim"],
            justify="left", wraplength=720
        ).pack(anchor="w", padx=12, pady=(0, 6))

        ai_row = ctk.CTkFrame(ai_card, fg_color="transparent")
        ai_row.pack(fill="x", padx=12, pady=(0, 10))
        ctk.CTkButton(
            ai_row, text="Encoder les nouveaux", width=200, height=32,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
            text_color=COLORS["bg_dark"],
            command=lambda: self._run_embed(force=False),
        ).pack(side="left", padx=(0, 6))
        ctk.CTkButton(
            ai_row, text="Réencoder TOUT", width=160, height=32,
            font=ctk.CTkFont(size=12),
            fg_color=COLORS["bg_input"], hover_color=COLORS["warning"],
            text_color=COLORS["text"],
            command=lambda: self._run_embed(force=True),
        ).pack(side="left", padx=6)

        # ── AI · Co-occurrence (1001tracklists) ───────────────
        # Mines real DJ sets to find which local tracks pros mix
        # together. Adds up to +15 raw points to the transition score
        # for pairs that show up often in scraped sets.
        self._section(scroll, "AI · Co-occurrence (1001tracklists)")
        cooc_card = ctk.CTkFrame(scroll, fg_color=COLORS["bg_card"],
                                  corner_radius=8)
        cooc_card.pack(fill="x", pady=3)

        try:
            from app.engine import cooccurrence
            from app.engine.tracklists import list_cached_tracklists
            from app.engine.library import get_connection
            _n_sets = len(list_cached_tracklists())
            _n_pairs = cooccurrence.pair_count(get_connection())
        except Exception:
            _n_sets, _n_pairs = 0, 0

        self._cooc_status = ctk.CTkLabel(
            cooc_card,
            text=f"{_n_sets} sets en cache  ·  {_n_pairs} paires co-jouées",
            font=ctk.CTkFont(size=12), text_color=COLORS["text"])
        self._cooc_status.pack(anchor="w", padx=12, pady=(10, 4))

        ctk.CTkLabel(
            cooc_card,
            text=("Colle une URL 1001tracklists dans Discover pour "
                   "scraper un set, puis reconstruis la matrice ici. "
                   "Aucun fichier audio n'est modifié — c'est juste "
                   "des données de co-jeu."),
            font=ctk.CTkFont(size=10), text_color=COLORS["text_dim"],
            justify="left", wraplength=720
        ).pack(anchor="w", padx=12, pady=(0, 6))

        cooc_row = ctk.CTkFrame(cooc_card, fg_color="transparent")
        cooc_row.pack(fill="x", padx=12, pady=(0, 10))
        ctk.CTkButton(
            cooc_row, text="Reconstruire la matrice",
            width=200, height=32, font=ctk.CTkFont(size=12, weight="bold"),
            fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
            text_color=COLORS["bg_dark"],
            command=self._run_cooccurrence,
        ).pack(side="left", padx=(0, 6))

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

    def _run_cooccurrence(self):
        """Rebuild the track_pairs table from every cached tracklist.
        Off-thread so the rebuild on 5k+ sets doesn't freeze the UI."""
        import threading
        from app.engine import cooccurrence
        from app.engine.library import get_connection
        from app.ui.toast import show_toast

        def work():
            conn = get_connection()

            def progress(i, total, slug):
                if i % 10 == 0 or i == total:
                    self.after(0, lambda i=i, t=total:
                                self._cooc_status.configure(
                                    text=f"Reconstruction {i}/{t} sets…",
                                    text_color=COLORS["accent"]))

            try:
                summary = cooccurrence.rebuild(conn, on_progress=progress)
                cooccurrence.invalidate_cache()
            except Exception as e:
                self.after(0, lambda err=str(e): self._cooc_status.configure(
                    text=f"Erreur : {err}",
                    text_color=COLORS["error"]))
                return

            self.after(0, lambda s=summary: self._cooc_status.configure(
                text=(f"{s['sets']} sets · {s['pairs']} paires · "
                      f"{s['matched_tracks']} tracks reconnues "
                      f"({s['unmatched_tracks']} non matchées)"),
                text_color=COLORS["success"]))
            self.after(0, lambda: show_toast(
                self.winfo_toplevel(),
                f"Matrice co-occurrence reconstruite — "
                f"{summary['pairs']} paires actives",
                kind="success"))

        threading.Thread(target=work, daemon=True,
                          name="cooccurrence-rebuild").start()

    def _run_embed(self, *, force: bool):
        """Background bulk-encoder. Walks the library, computes audio
        embeddings, persists them. Force=True re-encodes already-done
        tracks (use after a backend swap)."""
        import threading
        from app.engine import embeddings, library
        from app.ui.toast import show_toast

        backend = embeddings.best_backend()

        def work():
            conn = library.get_connection()
            if force:
                # Wipe existing embeddings so the regular query
                # surfaces every track
                conn.execute(
                    "UPDATE tracks SET embedding = NULL, "
                    "embedding_backend = NULL "
                    "WHERE COALESCE(corrupt, 0) = 0")
                conn.commit()
            todo = library.tracks_without_embedding(conn)
            if not todo:
                self.after(0, lambda: show_toast(
                    self.winfo_toplevel(),
                    "Toutes les tracks sont déjà encodées",
                    kind="info"))
                self._refresh_ai_status()
                return

            n = len(todo)
            self.after(0, lambda: show_toast(
                self.winfo_toplevel(),
                f"Encodage en cours : 0/{n} tracks (backend {backend})",
                kind="info", duration_ms=4500))

            done = errs = 0
            import time
            t0 = time.time()
            for i, t in enumerate(todo, 1):
                try:
                    vec = embeddings.embed(t["path"], backend=backend)
                    if vec is not None and float(vec.sum()) != 0.0:
                        library.set_embedding(conn, t["path"],
                                                vec, backend=backend)
                        done += 1
                    else:
                        errs += 1
                except Exception:
                    errs += 1
                # Lightweight live status — refresh AI label every 25 tracks
                if i % 25 == 0 or i == n:
                    elapsed = time.time() - t0
                    rate = i / elapsed if elapsed > 0 else 0
                    eta_s = (n - i) / rate if rate > 0 else 0
                    self.after(0, lambda i=i, n=n, e=int(eta_s):
                                self._ai_status.configure(
                                    text=f"Backend : {backend}  ·  "
                                         f"encodage {i}/{n}  "
                                         f"(ETA {e//60}min{e%60:02d})",
                                    text_color=COLORS["accent"]))

            self.after(0, lambda d=done, e=errs: show_toast(
                self.winfo_toplevel(),
                f"Encodage fini : {d} OK, {e} erreurs",
                kind="success" if e == 0 else "warning",
                duration_ms=5000))
            self._refresh_ai_status()

        threading.Thread(target=work, daemon=True,
                          name="bulk-embed").start()

    def _refresh_ai_status(self):
        """Re-read the encoded-count + backend, redraw the AI label."""
        try:
            from app.engine import embeddings
            from app.engine.library import get_connection, embedding_count
            done, total = embedding_count(get_connection())
            self.after(0, lambda d=done, t=total: self._ai_status.configure(
                text=f"Backend : {embeddings.best_backend()}  ·  "
                     f"{d}/{t} tracks encodés",
                text_color=(COLORS["success"] if d == t and t > 0
                             else COLORS["text"])))
        except Exception:
            pass

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

            if dry_run:
                msg = (f"Diagnostic terminé — {totals['scanned']} fichiers, "
                       f"{totals['corrupt']} corrompus à réparer, "
                       f"{totals['errors']} erreurs.")
                color = (COLORS["warning"] if totals["corrupt"]
                         else COLORS["success"])
            else:
                msg = (f"Terminé — {totals['repaired']} fichiers réparés, "
                       f"{totals['ok']} déjà sains, "
                       f"{totals['errors']} erreurs. "
                       f"Historique : data/repair_history.json")
                color = (COLORS["success"] if totals["repaired"] or not totals["errors"]
                         else COLORS["warning"])
            self.after(0, lambda: self.repair_status.configure(
                text=msg, text_color=color))

        threading.Thread(target=work, daemon=True).start()

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
