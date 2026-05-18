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

        # ── 1001tracklists account (for the scraping pipeline) ─
        # Guest users get rate-limited HARD on their IP after a few
        # requests. A logged-in account lifts that quota dramatically
        # (and on premium accounts removes it entirely). Creds go to
        # Windows Credential Manager via secrets_store; the Playwright
        # session cookies are persisted in data/tracklists_auth_state.json
        # so we don't have to re-login on every app launch.
        self._section(scroll, "1001tracklists account")
        from app.secrets_store import get_1001tracklists_credentials
        _tl_email, _tl_pwd = get_1001tracklists_credentials()
        ctk.CTkLabel(
            scroll,
            text=("Clique Login → une fenêtre Chromium s'ouvre sur la "
                  "homepage 1001tracklists. Dans cette fenêtre : clique "
                  "l'icône login (la porte avec flèche), remplis email + "
                  "mot de passe, solve le captcha Cloudflare, clique "
                  "Sign in. UNE FOIS LOGGÉ — FERME LA FENÊTRE TOI-MÊME. "
                  "Les cookies sont sauvegardés en continu pendant que "
                  "tu y es ; à la fermeture on valide en faisant un "
                  "vrai scrape de la home, et on te dit si ça marche. "
                  "Email + mot de passe ci-dessous = juste stockés en "
                  "sécurité (Win Credential Manager) pour ton info, PAS "
                  "auto-fillés dans la popup (la modale login est "
                  "dynamique et incompatible avec le pré-remplissage)."),
            font=ctk.CTkFont(size=10),
            text_color=COLORS["text_dim"],
            justify="left", wraplength=720,
        ).pack(anchor="w", padx=4, pady=(0, 4))
        self.tl_email = self._text_row(scroll, "Email", _tl_email)
        self.tl_password = self._text_row(scroll, "Mot de passe",
                                            _tl_pwd, show="*")

        # Status row + Login / Logout buttons
        tl_card = ctk.CTkFrame(scroll, fg_color=COLORS["bg_card"],
                                corner_radius=8)
        tl_card.pack(fill="x", pady=3)
        self._tl_status = ctk.CTkLabel(
            tl_card, text="(non connecté)",
            font=ctk.CTkFont(size=12), text_color=COLORS["text_dim"])
        self._tl_status.pack(anchor="w", padx=12, pady=(10, 4))

        tl_row = ctk.CTkFrame(tl_card, fg_color="transparent")
        tl_row.pack(fill="x", padx=12, pady=(0, 10))
        self._tl_login_btn = ctk.CTkButton(
            tl_row, text="Login 1001tracklists",
            width=180, height=32,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
            text_color=COLORS["bg_dark"],
            command=self._run_tl_login,
        )
        self._tl_login_btn.pack(side="left", padx=(0, 6))
        self._tl_logout_btn = ctk.CTkButton(
            tl_row, text="Logout / clear session",
            width=180, height=32,
            font=ctk.CTkFont(size=11),
            fg_color=COLORS["bg_input"], hover_color=COLORS["error"],
            text_color=COLORS["text"],
            command=self._run_tl_logout,
        )
        self._tl_logout_btn.pack(side="left", padx=(0, 6))
        # Inline progress for the login worker
        (self._tl_prog_frame,
         self._tl_prog_bar,
         self._tl_prog_step) = self._make_progress_row(tl_card)
        # Background refresh of the status row
        self.after_idle(self._refresh_tl_status)

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
        # Status label is built with a placeholder; the real value is
        # filled in by _refresh_ai_status() after the page paints, so
        # opening Settings doesn't block on a DB count.
        self._ai_status = ctk.CTkLabel(
            ai_card, text="Chargement…",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["text_dim"])
        self._ai_status.pack(anchor="w", padx=12, pady=(10, 4))
        self.after_idle(self._refresh_ai_status)

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

        # ── AI · Structure (intro / outro) ───────────────────
        # Detects intro_end + outro_start per track. Used by the Mixer
        # to suggest mix points and to score outro_A vs intro_B
        # rather than the whole-track audio.
        self._section(scroll, "AI · Structure (intro / outro)")
        struct_card = ctk.CTkFrame(scroll, fg_color=COLORS["bg_card"],
                                    corner_radius=8)
        struct_card.pack(fill="x", pady=3)

        self._struct_status = ctk.CTkLabel(
            struct_card, text="Chargement…",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["text_dim"])
        self._struct_status.pack(anchor="w", padx=12, pady=(10, 4))
        self.after_idle(self._refresh_struct_status)

        ctk.CTkLabel(
            struct_card,
            text=("Détecte intros + outros à partir de l'enveloppe "
                   "RMS. Signal clé pour suggérer le bon point de "
                   "mix dans le Mixer."),
            font=ctk.CTkFont(size=10), text_color=COLORS["text_dim"],
            justify="left", wraplength=720
        ).pack(anchor="w", padx=12, pady=(0, 6))

        struct_row = ctk.CTkFrame(struct_card, fg_color="transparent")
        struct_row.pack(fill="x", padx=12, pady=(0, 10))
        self._struct_btn = ctk.CTkButton(
            struct_row, text="Segmenter les nouveaux",
            width=200, height=32,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
            text_color=COLORS["bg_dark"],
            command=self._run_segmentation,
        )
        self._struct_btn.pack(side="left", padx=(0, 6))

        # Inline progress UI for segmentation
        (self._struct_prog_frame,
         self._struct_prog_bar,
         self._struct_prog_step) = self._make_progress_row(struct_card)

        # ── AI · Co-occurrence (1001tracklists) ───────────────
        # Mines real DJ sets to find which local tracks pros mix
        # together. Adds up to +15 raw points to the transition score
        # for pairs that show up often in scraped sets.
        self._section(scroll, "AI · Co-occurrence (1001tracklists)")
        cooc_card = ctk.CTkFrame(scroll, fg_color=COLORS["bg_card"],
                                  corner_radius=8)
        cooc_card.pack(fill="x", pady=3)

        # Same trick as AI section — placeholder, real values filled
        # in after the page paints.
        self._cooc_status = ctk.CTkLabel(
            cooc_card, text="Chargement…",
            font=ctk.CTkFont(size=12), text_color=COLORS["text_dim"])
        self._cooc_status.pack(anchor="w", padx=12, pady=(10, 4))
        self.after_idle(self._refresh_cooc_status)

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
        self._cooc_btn = ctk.CTkButton(
            cooc_row, text="Reconstruire la matrice",
            width=200, height=32, font=ctk.CTkFont(size=12, weight="bold"),
            fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
            text_color=COLORS["bg_dark"],
            command=self._run_cooccurrence,
        )
        self._cooc_btn.pack(side="left", padx=(0, 6))

        # Inline progress UI for cooccurrence rebuild
        (self._cooc_prog_frame,
         self._cooc_prog_bar,
         self._cooc_prog_step) = self._make_progress_row(cooc_card)

        # ── AI · Modèle de transition (L4 Siamese) ────────────
        # Trains a tiny Siamese network on (outro, intro) pairs from
        # 1001tracklists cooccurrence + the user's own 👍/👎 feedback
        # (oversampled). Once trained, transition_score adds ±10 raw
        # points based on the model's learned similarity. Opt-in: needs
        # `torch` installed, which is a heavy install (~700 MB) so we
        # don't pull it by default.
        self._section(scroll, "AI · Modèle de transition (L4 Siamese)")
        model_card = ctk.CTkFrame(scroll, fg_color=COLORS["bg_card"],
                                    corner_radius=8)
        model_card.pack(fill="x", pady=3)

        self._model_status = ctk.CTkLabel(
            model_card, text="Chargement…",
            font=ctk.CTkFont(size=12), text_color=COLORS["text_dim"])
        self._model_status.pack(anchor="w", padx=12, pady=(10, 4))
        self.after_idle(self._refresh_model_status)

        ctk.CTkLabel(
            model_card,
            text=("Apprend de tes 👍/👎 sur le Mixer + des co-jeux "
                  "1001tracklists. Pré-requis : pip install torch, "
                  "ainsi qu'au moins quelques tracks encodées (embeddings) "
                  "et une matrice de co-occurrence reconstruite. "
                  "L'entraînement tourne en arrière-plan dans l'activity "
                  "tray ; quelques minutes CPU sur une biblio moyenne."),
            font=ctk.CTkFont(size=10), text_color=COLORS["text_dim"],
            justify="left", wraplength=720
        ).pack(anchor="w", padx=12, pady=(0, 6))

        model_row = ctk.CTkFrame(model_card, fg_color="transparent")
        model_row.pack(fill="x", padx=12, pady=(0, 10))
        self._model_train_btn = ctk.CTkButton(
            model_row, text="Entraîner le modèle",
            width=200, height=32, font=ctk.CTkFont(size=12, weight="bold"),
            fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
            text_color=COLORS["bg_dark"],
            command=self._run_train_model,
        )
        self._model_train_btn.pack(side="left", padx=(0, 6))
        self._model_reset_btn = ctk.CTkButton(
            model_row, text="Réinitialiser",
            width=120, height=32, font=ctk.CTkFont(size=11),
            fg_color=COLORS["bg_input"], hover_color=COLORS["error"],
            text_color=COLORS["text"],
            command=self._reset_model,
        )
        self._model_reset_btn.pack(side="left", padx=(0, 6))

        # Inline progress UI for training (epochs progression)
        (self._model_prog_frame,
         self._model_prog_bar,
         self._model_prog_step) = self._make_progress_row(model_card)

        # Auto-retrain toggle — fires a background retrain every time
        # the user has accumulated transition_model.AUTO_RETRAIN_THRESHOLD
        # new 👍/👎 since the last train. Off by default (training is
        # CPU-heavy and not everyone wants that running in the background).
        from app.config import load_config as _load_cfg
        _cfg = _load_cfg()
        self._auto_retrain_var = ctk.BooleanVar(
            value=bool(_cfg.get("ai_auto_retrain", False)))
        ctk.CTkCheckBox(
            model_card,
            text=("Auto-réentraînement quand "
                  f"≥ {self._auto_retrain_threshold_label()} "
                  "nouveaux 👍/👎 s'accumulent depuis le dernier train"),
            variable=self._auto_retrain_var,
            font=ctk.CTkFont(size=10),
            text_color=COLORS["text_dim"],
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            command=self._toggle_auto_retrain,
        ).pack(anchor="w", padx=12, pady=(0, 10))

        # ── AI · Pipeline d'entraînement ──────────────────────
        # End-to-end corpus enrichment: scrapes top artists from your
        # lib, downloads missing tracks via yt-dlp (or keeps audio off
        # via embeddings-only mode), rebuilds cooccurrence, retrains L4.
        self._section(scroll, "AI · Pipeline d'entraînement")
        pipe_card = ctk.CTkFrame(scroll, fg_color=COLORS["bg_card"],
                                   corner_radius=8)
        pipe_card.pack(fill="x", pady=3)

        self._pipe_status = ctk.CTkLabel(
            pipe_card, text="Chargement…",
            font=ctk.CTkFont(size=12), text_color=COLORS["text_dim"])
        self._pipe_status.pack(anchor="w", padx=12, pady=(10, 4))
        self.after_idle(self._refresh_pipe_status)

        ctk.CTkLabel(
            pipe_card,
            text=("Enrichit automatiquement le corpus L4 en scrapant "
                  "les sets des artistes les plus présents dans ta lib "
                  "(1001tracklists), téléchargeant les tracks manquantes "
                  "via yt-dlp, calculant leurs embeddings puis "
                  "supprimant les MP3 si mode 'embeddings only'. "
                  "Suivi en temps réel dans l'activity tray."),
            font=ctk.CTkFont(size=10), text_color=COLORS["text_dim"],
            justify="left", wraplength=720
        ).pack(anchor="w", padx=12, pady=(0, 6))

        pipe_row = ctk.CTkFrame(pipe_card, fg_color="transparent")
        pipe_row.pack(fill="x", padx=12, pady=(0, 10))
        self._pipe_run_btn = ctk.CTkButton(
            pipe_row, text="Enrichir le corpus",
            width=200, height=32, font=ctk.CTkFont(size=12, weight="bold"),
            fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
            text_color=COLORS["bg_dark"],
            command=self._run_enrich_corpus,
        )
        self._pipe_run_btn.pack(side="left", padx=(0, 12))

        # Inline progress UI (hidden by default, shown when worker is
        # active). Not relying on the floating activity tray alone —
        # this row is visible right under the button, so the user
        # always sees what's happening even if the tray is off-screen.
        (self._pipe_prog_frame,
         self._pipe_prog_bar,
         self._pipe_prog_step) = self._make_progress_row(pipe_card)

        # Mode toggle — persists in config.json as ai_corpus_mode
        from app.config import load_config as _load_cfg2
        _cfg2 = _load_cfg2()
        self._pipe_mode_var = ctk.StringVar(
            value=_cfg2.get("ai_corpus_mode", "embeddings_only"))
        ctk.CTkLabel(pipe_row, text="Mode:",
                     font=ctk.CTkFont(size=11),
                     text_color=COLORS["text_dim"]
                     ).pack(side="left", padx=(0, 4))
        ctk.CTkOptionMenu(
            pipe_row,
            values=["embeddings_only", "keep_audio"],
            variable=self._pipe_mode_var,
            width=170, height=30,
            fg_color=COLORS["bg_input"], button_color=COLORS["accent"],
            dropdown_fg_color=COLORS["bg_card"],
            text_color=COLORS["text"],
            command=self._toggle_pipe_mode,
        ).pack(side="left")

        # FMA Small (8k tracks across 8 genres) — separator + button
        ctk.CTkFrame(pipe_card, fg_color=COLORS["bg_input"], height=1
                      ).pack(fill="x", padx=12, pady=(2, 6))
        ctk.CTkLabel(
            pipe_card,
            text=("Free Music Archive Small — 8 000 tracks (30s) "
                  "à travers 8 genres, ~7 GB de téléchargement temporaire. "
                  "Anchors l'espace d'embedding du L4 avec de la diversité "
                  "que ta lib seule ne couvre pas. Audio supprimé après "
                  "extraction si mode embeddings_only. Run ≈ 10-15 h CPU "
                  "(interruptible, reprise depuis là où on s'est arrêté)."),
            font=ctk.CTkFont(size=10), text_color=COLORS["text_dim"],
            justify="left", wraplength=720
        ).pack(anchor="w", padx=12, pady=(0, 6))

        fma_row = ctk.CTkFrame(pipe_card, fg_color="transparent")
        fma_row.pack(fill="x", padx=12, pady=(0, 10))
        self._fma_run_btn = ctk.CTkButton(
            fma_row, text="Télécharger + importer FMA Small",
            width=270, height=32, font=ctk.CTkFont(size=12, weight="bold"),
            fg_color=COLORS["accent2"], hover_color="#e0356f",
            text_color=COLORS["on_accent2"],
            command=self._run_fma_import,
        )
        self._fma_run_btn.pack(side="left", padx=(0, 6))
        self._fma_limit_var = ctk.StringVar(value="100")
        ctk.CTkLabel(fma_row, text="Max tracks (vide = tous) :",
                     font=ctk.CTkFont(size=10),
                     text_color=COLORS["text_dim"]
                     ).pack(side="left", padx=(8, 4))
        ctk.CTkEntry(
            fma_row, width=80, height=28,
            textvariable=self._fma_limit_var,
            fg_color=COLORS["bg_input"], border_color=COLORS["bg_input"],
            text_color=COLORS["text"],
        ).pack(side="left")

        # Inline progress UI for FMA (download + analyze phases)
        (self._fma_prog_frame,
         self._fma_prog_bar,
         self._fma_prog_step) = self._make_progress_row(pipe_card)

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
        Off-thread; progress + ETA mirror into the activity tray.

        Gives immediate visual feedback (button + status) so the user
        sees the click was registered while the worker spins up."""
        import threading
        from app.engine import cooccurrence, tasks
        from app.engine.library import get_connection

        self._cooc_btn.configure(state="disabled",
                                   text="En cours…")
        self._cooc_status.configure(
            text="démarrage rebuild…",
            text_color=COLORS["accent"])
        self._show_progress(
            self._cooc_prog_frame, self._cooc_prog_bar,
            self._cooc_prog_step, 0.0,
            "démarrage… (lecture des tracklists cachées)")

        def work():
            task = tasks.register("Cooccurrence — rebuild",
                                    message="lecture des sets…")
            try:
                conn = get_connection()

                def progress(i, total, slug):
                    frac = i / max(1, total)
                    msg = f"{i}/{total}  ·  {slug[:40]}"
                    tasks.update(task.id, progress=frac, message=msg)
                    self._show_progress(
                        self._cooc_prog_frame, self._cooc_prog_bar,
                        self._cooc_prog_step, frac, msg)

                summary = cooccurrence.rebuild(conn, on_progress=progress)
                cooccurrence.invalidate_cache()
            except Exception as e:
                tasks.complete(task.id, success=False,
                                message=f"Erreur : {str(e)[:60]}")
                self.after(0, lambda: self._cooc_btn.configure(
                    state="normal", text="Reconstruire la matrice"))
                self._hide_progress(self._cooc_prog_frame)
                return

            self.after(0, lambda s=summary: self._cooc_status.configure(
                text=(f"{s['sets']} sets · {s['pairs']} paires · "
                      f"{s['matched_tracks']} tracks reconnues "
                      f"({s['unmatched_tracks']} non matchées)"),
                text_color=COLORS["success"]))
            tasks.complete(
                task.id, success=True,
                message=f"{summary['pairs']} paires actives, "
                        f"{summary['matched_tracks']} tracks reconnues")
            self.after(0, lambda: self._cooc_btn.configure(
                state="normal", text="Reconstruire la matrice"))
            self._hide_progress(self._cooc_prog_frame)

        threading.Thread(target=work, daemon=True,
                          name="cooccurrence-rebuild").start()

    def _run_embed(self, *, force: bool):
        """Background bulk-encoder. Walks the library, computes audio
        embeddings, persists them. Force=True re-encodes already-done
        tracks (use after a backend swap).

        Progress + ETA are pushed to the global activity tray (top-left
        of the window) so the user sees what's happening even after
        navigating away from Settings."""
        import threading
        from app.engine import embeddings, library, tasks

        backend = embeddings.best_backend()

        def work():
            label = (f"Réencode TOUT ({backend})" if force
                     else f"Encode embeddings ({backend})")
            task = tasks.register(label, message="initialisation…")

            try:
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
                    tasks.complete(task.id, success=True,
                                    message="Tout est déjà encodé")
                    self._refresh_ai_status()
                    return

                n = len(todo)
                tasks.update(task.id, progress=0.0,
                              message=f"0/{n} tracks")

                done = errs = 0
                import time
                t0 = time.time()
                for i, t in enumerate(todo, 1):
                    if task.cancel_requested():
                        tasks.complete(
                            task.id, success=False,
                            message=f"Annulé à {i}/{n} ({done} encodés)")
                        self._refresh_ai_status()
                        return
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
                    # Push progress every 5 tracks (cheap) so the tray bar
                    # actually moves
                    if i % 5 == 0 or i == n:
                        elapsed = time.time() - t0
                        rate = i / elapsed if elapsed > 0 else 0
                        eta_s = (n - i) / rate if rate > 0 else 0
                        from pathlib import Path
                        tasks.update(
                            task.id,
                            progress=i / n,
                            eta_s=eta_s,
                            message=f"{i}/{n}  ·  "
                                    f"{Path(t['path']).name[:32]}")

                tasks.complete(
                    task.id,
                    success=(errs == 0),
                    message=f"{done} OK, {errs} erreurs")
                self._refresh_ai_status()
            except Exception as e:
                tasks.complete(task.id, success=False,
                                message=f"Erreur : {str(e)[:60]}")

        threading.Thread(target=work, daemon=True,
                          name="bulk-embed").start()

    def _refresh_ai_status(self):
        """Re-read the encoded-count + backend, redraw the AI label.
        Run in a thread so a slow DB doesn't stall the UI build."""
        import threading

        def work():
            try:
                from app.engine import embeddings
                from app.engine.library import (get_connection,
                                                  embedding_count)
                done, total = embedding_count(get_connection())
                backend = embeddings.best_backend()
                self.after(0, lambda d=done, t=total, b=backend:
                           self._ai_status.configure(
                               text=f"Backend : {b}  ·  "
                                    f"{d}/{t} tracks encodés",
                               text_color=(COLORS["success"]
                                            if d == t and t > 0
                                            else COLORS["text"])))
            except Exception:
                pass
        threading.Thread(target=work, daemon=True,
                          name="ai-status-refresh").start()

    def _refresh_struct_status(self):
        """Off-thread count of segmented vs total tracks."""
        import threading

        def work():
            try:
                from app.engine.library import (get_connection,
                                                  structure_count)
                done, total = structure_count(get_connection())
                self.after(0, lambda d=done, t=total:
                           self._struct_status.configure(
                               text=f"{d}/{t} tracks segmentées",
                               text_color=(COLORS["success"]
                                            if d == t and t > 0
                                            else COLORS["text"])))
            except Exception:
                pass
        threading.Thread(target=work, daemon=True,
                          name="struct-status-refresh").start()

    def _run_segmentation(self):
        """Detect intro/outro on every un-segmented track. Threaded;
        progress reported through the activity tray.

        Gives immediate UI feedback (button text + status label) so the
        user sees the click was registered — the actual analyse work
        runs in a daemon thread and surfaces in the top-left activity
        tray, which can be off-screen on the Settings page.
        """
        import threading
        from app.engine import library, tasks
        from app.engine.segmentation import detect_structure

        # Immediate feedback so the user knows the click took
        self._struct_btn.configure(state="disabled",
                                     text="En cours…")
        self._struct_status.configure(
            text="démarrage segmentation…",
            text_color=COLORS["accent"])
        self._show_progress(
            self._struct_prog_frame, self._struct_prog_bar,
            self._struct_prog_step, 0.0,
            "démarrage… (chargement librosa)")

        def work():
            task = tasks.register("Segmentation intro/outro",
                                    message="recherche…")
            try:
                conn = library.get_connection()
                todo = library.tracks_without_structure(conn)
                if not todo:
                    tasks.complete(task.id, success=True,
                                    message="Tout est déjà segmenté")
                    self._refresh_struct_status()
                    return
                n = len(todo)
                done = errs = 0
                import time
                t0 = time.time()
                from pathlib import Path
                for i, t in enumerate(todo, 1):
                    if task.cancel_requested():
                        tasks.complete(
                            task.id, success=False,
                            message=f"Annulé à {i}/{n} ({done} segmentés)")
                        self._refresh_struct_status()
                        return
                    try:
                        s = detect_structure(t["path"])
                        library.set_structure(
                            conn, t["path"],
                            intro_end=s.get("intro_end") or 0.0,
                            outro_start=s.get("outro_start") or 0.0,
                            drops=s.get("drops") or [])
                        done += 1
                    except Exception:
                        errs += 1
                    if i % 5 == 0 or i == n:
                        elapsed = time.time() - t0
                        rate = i / elapsed if elapsed > 0 else 0
                        eta_s = (n - i) / rate if rate > 0 else 0
                        msg = (f"{i}/{n}  ·  "
                                f"{Path(t['path']).name[:32]}")
                        tasks.update(
                            task.id, progress=i / n, eta_s=eta_s,
                            message=msg)
                        self._show_progress(
                            self._struct_prog_frame,
                            self._struct_prog_bar,
                            self._struct_prog_step,
                            i / n,
                            f"{msg}  ·  ETA {eta_s/60:.0f}min")
                tasks.complete(
                    task.id, success=(errs == 0),
                    message=f"{done} OK, {errs} erreurs")
                self._refresh_struct_status()
            except Exception as e:
                tasks.complete(task.id, success=False,
                                message=f"Erreur : {str(e)[:60]}")
            finally:
                # Restore the button regardless of success / cancellation
                self.after(0, lambda: self._struct_btn.configure(
                    state="normal", text="Segmenter les nouveaux"))
                self._hide_progress(self._struct_prog_frame)

        threading.Thread(target=work, daemon=True,
                          name="bulk-segment").start()

    def _refresh_model_status(self):
        """Re-read the L4 transition model status — exists? when trained?
        what dataset size? Threaded because pair_count + feedback.count
        hit SQLite."""
        import threading

        def work():
            try:
                import json as _json
                from app.engine import transition_model
                from app.engine import cooccurrence, feedback
                from app.engine.library import get_connection
                ready = transition_model.is_ready()
                meta = {}
                if transition_model._META_PATH.exists():
                    try:
                        meta = _json.loads(
                            transition_model._META_PATH.read_text(
                                encoding="utf-8"))
                    except Exception:
                        meta = {}
                n_pairs = cooccurrence.pair_count(get_connection())
                fb_n = feedback.count().get("total", 0)
                try:
                    import torch  # noqa
                    has_torch = True
                except ImportError:
                    has_torch = False

                if ready:
                    txt = (f"Modèle entraîné  ·  "
                           f"{meta.get('n_pairs', '?')} exemples, "
                           f"{meta.get('epochs', '?')} epochs  ·  "
                           f"corpus actuel: {n_pairs} co-paires + "
                           f"{fb_n} feedback")
                    color = COLORS["success"]
                elif not has_torch:
                    txt = (f"Modèle non entraîné  ·  torch absent  ·  "
                           f"corpus prêt: {n_pairs} co-paires + "
                           f"{fb_n} feedback")
                    color = COLORS["warning"]
                else:
                    txt = (f"Modèle non entraîné  ·  "
                           f"corpus prêt: {n_pairs} co-paires + "
                           f"{fb_n} feedback")
                    color = COLORS["text_dim"]

                self.after(0, lambda t=txt, c=color, h=has_torch, r=ready:
                            self._apply_model_status(t, c, h, r))
            except Exception:
                pass

        threading.Thread(target=work, daemon=True,
                          name="model-status-refresh").start()

    def _apply_model_status(self, text: str, color: str,
                             has_torch: bool, is_ready: bool):
        """UI-thread setter for the model status row — also flips the
        train button's enabled state based on whether torch is around."""
        try:
            self._model_status.configure(text=text, text_color=color)
            self._model_train_btn.configure(
                state="normal" if has_torch else "disabled")
            self._model_reset_btn.configure(
                state="normal" if is_ready else "disabled")
        except Exception:
            pass

    def _run_train_model(self):
        """Kick off transition_model.train() in a daemon thread, with
        progress mirrored to the activity tray. Refuses to run if torch
        is missing (the button would already be disabled in that case)."""
        import threading
        from app.engine import transition_model, tasks
        from app.engine.library import get_connection

        try:
            import torch  # noqa
        except ImportError:
            self._model_status.configure(
                text="torch n'est pas installé  ·  pip install torch",
                text_color=COLORS["error"])
            return

        # Lock out double-clicks while training is in progress
        self._model_train_btn.configure(state="disabled",
                                         text="Entraînement…")
        self._show_progress(
            self._model_prog_frame, self._model_prog_bar,
            self._model_prog_step, 0.0,
            "démarrage… (extraction des paires)")

        def work():
            task = tasks.register(
                "L4 — entraînement Siamese",
                message="extraction des paires…")
            try:
                conn = get_connection()
                pairs = transition_model.extract_pairs(conn)
                source = "1001tracklists + feedback"
                if not pairs:
                    # No real DJ data yet — fall back to the heuristic
                    # distillation bootstrap so day-1 users still get a
                    # trained model. Next retrain (once cooc / feedback
                    # land) supersedes this with real signal.
                    tasks.update(task.id, progress=0.02,
                                  message="bootstrap (distillation "
                                          "heuristique)…")
                    self._show_progress(
                        self._model_prog_frame, self._model_prog_bar,
                        self._model_prog_step, 0.02,
                        "bootstrap (distillation heuristique)…")
                    pairs = transition_model.bootstrap_pairs(conn)
                    source = "bootstrap (distillation)"
                if not pairs:
                    tasks.complete(
                        task.id, success=False,
                        message="aucun exemple — encode quelques "
                                "tracks (Settings → Embeddings) "
                                "d'abord")
                    return
                tasks.update(task.id, progress=0.05,
                              message=f"{len(pairs)} exemples "
                                      f"({source}) → train")
                self._show_progress(
                    self._model_prog_frame, self._model_prog_bar,
                    self._model_prog_step, 0.05,
                    f"{len(pairs)} exemples ({source}) → train")
                n_pairs_total = len(pairs)

                def _on_epoch(frac: float, msg: str):
                    # Reserve 5% for prep, 5% for the final save —
                    # epochs span the middle 90% of the progress bar
                    progress = 0.05 + frac * 0.90
                    tasks.update(task.id, progress=progress, message=msg)
                    self._show_progress(
                        self._model_prog_frame, self._model_prog_bar,
                        self._model_prog_step, progress, msg)

                ok = transition_model.train(pairs, on_progress=_on_epoch)
                if not ok:
                    tasks.complete(
                        task.id, success=False,
                        message="échec entraînement — voir errors.log")
                    return
                tasks.complete(
                    task.id, success=True,
                    message=f"OK · modèle sauvegardé "
                            f"({n_pairs_total} ex, {source})")
                self.after(0, self._refresh_model_status)
            except Exception as e:
                tasks.complete(task.id, success=False,
                                message=f"Erreur : {str(e)[:60]}")
                from app.logger import log_error
                log_error("L4 train failed", e)
            finally:
                self.after(0, lambda: self._model_train_btn.configure(
                    state="normal", text="Entraîner le modèle"))
                self._hide_progress(self._model_prog_frame)

        threading.Thread(target=work, daemon=True,
                          name="l4-train").start()

    def _auto_retrain_threshold_label(self) -> str:
        try:
            from app.engine.transition_model import AUTO_RETRAIN_THRESHOLD
            return str(AUTO_RETRAIN_THRESHOLD)
        except Exception:
            return "10"

    def _toggle_auto_retrain(self):
        """Persist the on/off state to config.json. transition_model
        re-reads it on every feedback.record() so the change takes
        effect immediately — no app restart needed."""
        try:
            from app.config import load_config, save_config
            cfg = load_config()
            cfg["ai_auto_retrain"] = bool(self._auto_retrain_var.get())
            save_config(cfg)
        except Exception as e:
            from app.logger import log_error
            log_error("toggle auto-retrain failed", e)

    def _reset_model(self):
        """Delete the saved transition.pt + meta. transition_score will
        immediately fall back to the heuristic + cooc + feedback stack."""
        from tkinter import messagebox
        if not messagebox.askyesno(
                "Réinitialiser le modèle L4 ?",
                "Cela supprime data/models/transition.pt. Les scores "
                "perdront le bonus ±10 du modèle, mais cooc + feedback "
                "restent actifs. Tu peux ré-entraîner ensuite. Continuer ?"):
            return
        try:
            from app.engine import transition_model
            for p in (transition_model._MODEL_PATH,
                       transition_model._META_PATH):
                try:
                    p.unlink()
                except FileNotFoundError:
                    pass
            # Clear the in-memory cache so the next score() call
            # actually reads None instead of the stale model object
            transition_model._model_cache = None
            self._refresh_model_status()
        except Exception as e:
            from app.logger import log_error
            log_error("L4 reset failed", e)

    # ── Inline progress widgets (shared helper) ──────────────────

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

    def _refresh_tl_status(self):
        """Check whether a saved 1001tracklists session is still valid,
        update the status row accordingly. Threaded — is_logged_in()
        hits the network."""
        import threading

        def work():
            from app.engine import tracklists
            from app.engine.tracklists import _AUTH_STATE_PATH
            try:
                # Cheap path: if no auth state file, definitely not logged in
                if not _AUTH_STATE_PATH.exists():
                    self.after(0, lambda: self._tl_status.configure(
                        text="(non connecté — clique Login)",
                        text_color=COLORS["text_dim"]))
                    return
                ok = tracklists.is_logged_in()
                if ok:
                    msg = "✓ session 1001tracklists active"
                    color = COLORS["success"]
                else:
                    msg = ("session expirée ou IP bannie — "
                           "re-login ou change d'IP")
                    color = COLORS["warning"]
                self.after(0, lambda m=msg, c=color:
                            self._tl_status.configure(
                                text=m, text_color=c))
            except Exception as e:
                self.after(0, lambda e=e: self._tl_status.configure(
                    text=f"erreur check: {str(e)[:60]}",
                    text_color=COLORS["error"]))

        threading.Thread(target=work, daemon=True,
                          name="tl-status-refresh").start()

    def _run_tl_login(self):
        """Persist email/password to the keyring, then run the Playwright
        login flow. Updates the inline progress + status."""
        import threading
        from app.secrets_store import set_1001tracklists_credentials

        email = (self.tl_email.get() or "").strip()
        password = (self.tl_password.get() or "")
        if not email or not password:
            self._tl_status.configure(
                text="email + mot de passe requis",
                text_color=COLORS["warning"])
            return

        # Save first so the user doesn't have to retype on a retry
        set_1001tracklists_credentials(email, password)

        self._tl_login_btn.configure(
            state="disabled", text="Login en cours…")
        self._show_progress(
            self._tl_prog_frame, self._tl_prog_bar,
            self._tl_prog_step, 0.1,
            "ouverture fenêtre Chromium — login + captcha dans la "
            "fenêtre, puis FERME-LA toi-même quand t'es loggé")

        def work():
            try:
                self._show_progress(
                    self._tl_prog_frame, self._tl_prog_bar,
                    self._tl_prog_step, 0.4,
                    "en attente que tu fermes la fenêtre une fois "
                    "loggé (jusqu'à 10 min)…")
                from app.engine import tracklists
                ok, msg = tracklists.login_with_credentials(
                    email, password, force=True)
                self._show_progress(
                    self._tl_prog_frame, self._tl_prog_bar,
                    self._tl_prog_step, 1.0, msg)
                color = (COLORS["success"] if ok
                         else COLORS["error"])
                self.after(0, lambda m=msg, c=color:
                            self._tl_status.configure(
                                text=m, text_color=c))
            except Exception as e:
                from app.logger import log_error
                log_error("tl login failed", e)
                self.after(0, lambda e=e: self._tl_status.configure(
                    text=f"erreur : {str(e)[:80]}",
                    text_color=COLORS["error"]))
            finally:
                self.after(0, lambda: self._tl_login_btn.configure(
                    state="normal", text="Login 1001tracklists"))
                self._hide_progress(self._tl_prog_frame)

        threading.Thread(target=work, daemon=True,
                          name="tl-login").start()

    def _run_tl_logout(self):
        """Wipe the saved session cookies + reset the Playwright
        context. Doesn't touch the saved email/password."""
        from app.engine import tracklists
        try:
            tracklists.logout_and_clear_session()
            self._tl_status.configure(
                text="déconnecté — session effacée",
                text_color=COLORS["text_dim"])
        except Exception as e:
            from app.logger import log_error
            log_error("tl logout failed", e)
            self._tl_status.configure(
                text=f"erreur logout : {str(e)[:60]}",
                text_color=COLORS["error"])

    def _refresh_pipe_status(self):
        """Show corpus composition (user vs training/fma) + pair count."""
        import threading

        def work():
            try:
                from app.engine.library import get_connection
                from app.engine import cooccurrence
                conn = get_connection()
                rows = conn.execute(
                    "SELECT COALESCE(source, 'user') AS s, "
                    "COUNT(*) AS n FROM tracks GROUP BY s").fetchall()
                breakdown = {r[0]: r[1] for r in rows}
                user_n = breakdown.get("user", 0)
                train_n = breakdown.get("training", 0)
                fma_n = breakdown.get("fma", 0)
                n_pairs = cooccurrence.pair_count(conn)
                txt = (f"Corpus : {user_n} user · {train_n} training · "
                       f"{fma_n} FMA  ·  {n_pairs} paires cooccurrence")
                self.after(0, lambda t=txt: self._pipe_status.configure(
                    text=t, text_color=COLORS["text"]))
            except Exception:
                pass

        threading.Thread(target=work, daemon=True,
                          name="pipe-status").start()

    def _toggle_pipe_mode(self, _value: str = ""):
        try:
            from app.config import load_config, save_config
            cfg = load_config()
            cfg["ai_corpus_mode"] = self._pipe_mode_var.get()
            save_config(cfg)
        except Exception as e:
            from app.logger import log_error
            log_error("toggle pipe mode failed", e)

    def _run_enrich_corpus(self):
        """Fire the L4 training pipeline in a background thread,
        progress mirrored to BOTH the inline progress row AND the
        activity tray (so even if the tray is off-screen the user
        always sees what's happening)."""
        import threading
        from app.engine import training_pipeline, tasks

        self._pipe_run_btn.configure(state="disabled",
                                       text="En cours…")
        # Pop the inline progress row immediately so the user knows the
        # click was registered, before the worker has even spawned.
        self._show_progress(
            self._pipe_prog_frame, self._pipe_prog_bar,
            self._pipe_prog_step, 0.0,
            "démarrage… (chargement playwright + sondage 1001tracklists)")
        mode = self._pipe_mode_var.get() or "embeddings_only"

        def work():
            task = tasks.register(
                "L4 — enrichissement corpus",
                message="démarrage…")
            try:
                def _on_progress(phase, i, total, msg):
                    # Map phases to overall progress fractions so the
                    # progress bar is meaningful across the multi-stage run
                    phase_ranges = {
                        "discover":      (0.00, 0.02),
                        "discover_sets": (0.02, 0.10),
                        "scrape":        (0.10, 0.40),
                        "resolve":       (0.40, 0.42),
                        "download":      (0.42, 0.70),
                        "analyze":       (0.70, 0.85),
                        "cooc":          (0.85, 0.90),
                        "train":         (0.90, 1.00),
                    }
                    lo, hi = phase_ranges.get(phase, (0.0, 1.0))
                    if total > 0:
                        frac = lo + (hi - lo) * (i / total)
                    else:
                        frac = lo
                    tasks.update(
                        task.id,
                        progress=frac,
                        message=f"[{phase}] {msg}"[:120])
                    # Mirror to the inline row — even if the tray
                    # subscription doesn't fire, this WILL update.
                    self._show_progress(
                        self._pipe_prog_frame, self._pipe_prog_bar,
                        self._pipe_prog_step, frac,
                        f"[{phase} {i}/{total}] {msg}"[:160])

                summary = training_pipeline.enrich_corpus(
                    target_pairs=2000,
                    mode=mode,
                    on_progress=_on_progress,
                    retrain=True,
                )
                # If pipeline aborted with a specific reason (e.g. IP
                # rate-limit), surface that distinctly so the user
                # knows it's not a real "no data" outcome.
                if summary.get("abort_reason"):
                    tasks.complete(
                        task.id, success=False,
                        message=summary["abort_reason"][:120])
                    self.after(0, lambda: self._pipe_status.configure(
                        text=summary["abort_reason"],
                        text_color=COLORS["error"]))
                    return
                msg_parts = []
                phases = summary.get("phases", {})
                if "scrape" in phases:
                    s = phases["scrape"]
                    msg_parts.append(
                        f"scrape {s.get('fetched',0)}/{s.get('failed',0)}")
                if "downloaded" in phases:
                    msg_parts.append(
                        f"DL {phases['downloaded']}")
                if "analyzed" in phases:
                    msg_parts.append(
                        f"ana {phases['analyzed']}")
                msg_parts.append(
                    f"paires={summary.get('total_pairs_after', 0)}")
                if summary.get("model_retrained"):
                    msg_parts.append("L4 retrained")
                tasks.complete(
                    task.id,
                    success=not summary.get("aborted"),
                    message=" · ".join(msg_parts))
                self.after(0, self._refresh_pipe_status)
                self.after(0, self._refresh_cooc_status)
                self.after(0, self._refresh_model_status)
            except Exception as e:
                tasks.complete(task.id, success=False,
                                message=f"Erreur : {str(e)[:60]}")
                from app.logger import log_error
                log_error("enrich_corpus failed", e)
            finally:
                self.after(0, lambda: self._pipe_run_btn.configure(
                    state="normal", text="Enrichir le corpus"))
                self._hide_progress(self._pipe_prog_frame)

        threading.Thread(target=work, daemon=True,
                          name="enrich-corpus").start()

    def _run_fma_import(self):
        """Download + extract FMA Small, then run analyse + embed for
        each track with source='fma'. Heavy job (10-15 h on CPU for the
        full 8000); interruptible via the activity tray's per-task
        cancel button (cancels the underlying stop_event)."""
        import threading
        from app.engine import fma, tasks

        try:
            max_n = int(self._fma_limit_var.get()) \
                if self._fma_limit_var.get().strip() else None
        except ValueError:
            max_n = 100

        mode = self._pipe_mode_var.get() or "embeddings_only"
        self._fma_run_btn.configure(
            state="disabled", text="FMA en cours…")
        self._show_progress(
            self._fma_prog_frame, self._fma_prog_bar,
            self._fma_prog_step, 0.0,
            "démarrage… (vérification téléchargement FMA Small)")

        def work():
            task = tasks.register(
                "L4 — FMA Small import",
                message="démarrage…")
            try:
                # Phase 1: download zip if needed (~7 GB, resumable)
                def _dl(done, total, msg):
                    if total > 0:
                        frac = 0.0 + 0.50 * (done / total)
                    else:
                        frac = 0.0
                    tasks.update(task.id, progress=frac,
                                  message=f"[dl] {msg}"[:120])
                    self._show_progress(
                        self._fma_prog_frame, self._fma_prog_bar,
                        self._fma_prog_step, frac,
                        f"[download] {msg}"[:160])

                ok = fma.download_fma_small(
                    on_progress=_dl,
                    stop_event=task.cancel_event)
                if not ok:
                    tasks.complete(
                        task.id, success=False,
                        message="échec téléchargement FMA")
                    return

                # Phase 2: analyze + embed + (optional) purge
                def _an(i, total, status, msg):
                    if total > 0:
                        frac = 0.50 + 0.50 * (i / total)
                    else:
                        frac = 0.50
                    tasks.update(
                        task.id, progress=frac,
                        message=f"[analyse {i}/{total}] {msg}"[:120])
                    self._show_progress(
                        self._fma_prog_frame, self._fma_prog_bar,
                        self._fma_prog_step, frac,
                        f"[analyse {i}/{total}] {msg}"[:160])

                summary = fma.import_into_db(
                    mode=mode, max_tracks=max_n,
                    on_progress=_an,
                    stop_event=task.cancel_event)
                tasks.complete(
                    task.id, success=True,
                    message=(f"OK · {summary['analyzed']} analysés "
                             f"({summary['skipped']} déjà en DB, "
                             f"{summary['failed']} échecs)"))
                self.after(0, self._refresh_pipe_status)
                self.after(0, self._refresh_model_status)
            except Exception as e:
                tasks.complete(task.id, success=False,
                                message=f"Erreur : {str(e)[:60]}")
                from app.logger import log_error
                log_error("fma import failed", e)
            finally:
                self.after(0, lambda: self._fma_run_btn.configure(
                    state="normal",
                    text="Télécharger + importer FMA Small"))
                self._hide_progress(self._fma_prog_frame)

        threading.Thread(target=work, daemon=True,
                          name="fma-import").start()

    def _refresh_cooc_status(self):
        """Re-read scraped-set + pair counts. Threaded — pair_count is
        a COUNT(*) on track_pairs, list_cached_tracklists globs
        data/tracklists/, both can be slow on a busy disk."""
        import threading

        def work():
            try:
                from app.engine import cooccurrence
                from app.engine.tracklists import list_cached_tracklists
                from app.engine.library import get_connection
                n_sets = len(list_cached_tracklists())
                n_pairs = cooccurrence.pair_count(get_connection())
                self.after(0, lambda s=n_sets, p=n_pairs:
                           self._cooc_status.configure(
                               text=f"{s} sets en cache  ·  "
                                    f"{p} paires co-jouées",
                               text_color=COLORS["text"]))
            except Exception:
                pass
        threading.Thread(target=work, daemon=True,
                          name="cooc-status-refresh").start()

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
