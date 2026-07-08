# ruff: noqa: F401
from __future__ import annotations

import customtkinter as ctk
from tkinter import filedialog

from app.config import COLORS, THEMES, load_config, save_config, get_ffmpeg, get_node, apply_theme


class GeneralMixin:
    """Appearance, paths, credentials (Spotify / setlist.fm / 1001TL),
    interop and system-info sections + the 1001TL login workers."""

    def _build_general_sections(self, scroll):
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

        # ── setlist.fm (fallback corpus, plan B du scraping) ──
        self._section(scroll, "setlist.fm API")
        ctk.CTkLabel(
            scroll,
            text=("Plan B quand 1001tracklists rate-limite : REST "
                  "officiel, sans scraping. Clé gratuite sur "
                  "setlist.fm/settings/api — colle-la ici et le "
                  "fallback engine.setlist_fm devient actif."),
            font=ctk.CTkFont(size=10), text_color=COLORS["text_dim"],
            justify="left", wraplength=720,
        ).pack(anchor="w", padx=4, pady=(0, 4))
        self.slfm_key = self._text_row(
            scroll, "API key", self.cfg.get("setlistfm_api_key", ""),
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
        ).pack(anchor="w", padx=12, pady=(0, 4))

        # Per-format opt-in (post-regression policy) : les containers
        # non-MP3 restent read-only tant que leur case est décochée,
        # même quand le master est ON. L'écriture WAV est de plus
        # verified-or-reverted côté engine (analyzer.write_tags).
        fmt_row = ctk.CTkFrame(interop_card, fg_color="transparent")
        fmt_row.pack(fill="x", padx=12, pady=(0, 10))
        ctk.CTkLabel(
            fmt_row, text="Formats autorisés :  MP3 (toujours)",
            font=ctk.CTkFont(size=10), text_color=COLORS["text_dim"]
        ).pack(side="left", padx=(0, 12))
        self._fmt_tag_vars: dict[str, ctk.BooleanVar] = {}
        for label, cfg_key in (("WAV (risqué)", "write_tags_wav"),
                                ("FLAC", "write_tags_flac"),
                                ("M4A", "write_tags_m4a")):
            var = ctk.BooleanVar(value=bool(self.cfg.get(cfg_key, False)))
            self._fmt_tag_vars[cfg_key] = var
            ctk.CTkCheckBox(
                fmt_row, text=label, variable=var,
                font=ctk.CTkFont(size=10), text_color=COLORS["text"],
                checkbox_height=16, checkbox_width=16,
                fg_color=COLORS["warning"],
            ).pack(side="left", padx=(0, 10))

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

