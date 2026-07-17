# ruff: noqa: F401
from __future__ import annotations

import customtkinter as ctk
from tkinter import filedialog

from app.config import COLORS, THEMES, load_config, save_config, get_ffmpeg, get_node, apply_theme


class AISectionsMixin:
    """AI section builders + status refreshers (embeddings, structure,
    cooccurrence, L4 model, pipeline, FMA)."""

    def _build_ai_sections(self, scroll):
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
        ).pack(anchor="w", padx=12, pady=(0, 4))
        self._auto_enrich_var = ctk.BooleanVar(
            value=bool(_cfg.get("ai_auto_enrich", False)))
        ctk.CTkCheckBox(
            model_card,
            text=("Apprentissage continu : enrichir le corpus (scrape + "
                  "download arrière-plan) quand ≥ 25 nouveaux morceaux "
                  "arrivent au Sync"),
            variable=self._auto_enrich_var,
            font=ctk.CTkFont(size=10),
            text_color=COLORS["text_dim"],
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            command=self._toggle_auto_enrich,
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

        # Disk usage + cleanup row — the FMA download is ~7 GB zip +
        # ~4 GB extracted. In embeddings_only mode the import auto-
        # cleans after success, but a half-finished or keep_audio run
        # leaves the files on disk. This button nukes them on demand.
        fma_disk_row = ctk.CTkFrame(pipe_card, fg_color="transparent")
        fma_disk_row.pack(fill="x", padx=12, pady=(0, 6))
        self._fma_disk_label = ctk.CTkLabel(
            fma_disk_row, text="FMA disque : —",
            font=ctk.CTkFont(size=10), text_color=COLORS["text_dim"],
            anchor="w")
        self._fma_disk_label.pack(side="left", fill="x", expand=True)
        self._fma_cleanup_btn = ctk.CTkButton(
            fma_disk_row, text="Nettoyer FMA (libère le disque)",
            width=230, height=26, font=ctk.CTkFont(size=10),
            fg_color=COLORS["bg_input"], hover_color=COLORS["warning"],
            text_color=COLORS["text"],
            command=self._run_fma_cleanup,
        )
        self._fma_cleanup_btn.pack(side="left", padx=(8, 0))
        self.after_idle(self._refresh_fma_disk)

        # Inline progress UI for FMA (download + analyze phases)
        (self._fma_prog_frame,
         self._fma_prog_bar,
         self._fma_prog_step) = self._make_progress_row(pipe_card)


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
                    delta = transition_model.feedback_delta_since_train()
                    thresh = getattr(transition_model,
                                     "AUTO_RETRAIN_THRESHOLD", 10)
                    txt = (f"Modèle entraîné  ·  "
                           f"{meta.get('n_pairs', '?')} exemples, "
                           f"{meta.get('epochs', '?')} epochs  ·  "
                           f"corpus actuel: {n_pairs} co-paires + "
                           f"{fb_n} feedback  ·  "
                           f"auto-retrain: {delta}/{thresh} nouveaux votes")
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

    def _toggle_auto_enrich(self):
        """Persist the continuous-learning toggle. maybe_auto_enrich
        re-reads config at every Sync, and its first enabled run only
        sets the baseline count — no instant scrape on toggle."""
        try:
            from app.config import load_config, save_config
            cfg = load_config()
            cfg["ai_auto_enrich"] = bool(self._auto_enrich_var.get())
            save_config(cfg)
        except Exception as e:
            from app.logger import log_error
            log_error("toggle auto-enrich failed", e)


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


    def _refresh_fma_disk(self):
        """Show how much disk the FMA dataset currently occupies."""
        import threading

        def work():
            try:
                from app.engine import fma
                u = fma.disk_usage()
                total = sum(u.values())
                if total == 0:
                    txt = "FMA disque : rien (pas téléchargé)"
                    color = COLORS["text_dim"]
                else:
                    txt = (f"FMA disque : {total/1024**3:.1f} GB  "
                           f"(zip {u['zip']/1024**3:.1f} + extrait "
                           f"{u['extracted']/1024**3:.1f})")
                    color = COLORS["warning"]
                self.after(0, lambda t=txt, c=color:
                            self._fma_disk_label.configure(
                                text=t, text_color=c))
            except Exception:
                pass

        threading.Thread(target=work, daemon=True,
                          name="fma-disk").start()


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

