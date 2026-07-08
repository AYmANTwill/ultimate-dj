# ruff: noqa: F401
from __future__ import annotations

import customtkinter as ctk
from tkinter import filedialog

from app.config import COLORS, THEMES, load_config, save_config, get_ffmpeg, get_node, apply_theme


class AIWorkersMixin:
    """Long-running AI workers (threaded; progress via activity tray)."""

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
                pairs_before = cooccurrence.pair_count(conn)

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

            # C4 — new pairs should retrain L4 without waiting for the
            # feedback-vote threshold. Guarded by the user's auto-retrain
            # opt-in + an actual change in the matrix.
            retrain_note = ""
            try:
                from app.config import load_config
                from app.engine import transition_model
                if (summary["pairs"] != pairs_before
                        and load_config().get("ai_auto_retrain", False)
                        and transition_model.is_ready()
                        and transition_model.maybe_auto_retrain(force=True)):
                    retrain_note = "  ·  auto-retrain L4 lancé"
            except Exception as e:
                from app.logger import log_warning
                log_warning(f"post-rebuild auto-retrain failed: {e}")

            self.after(0, lambda s=summary, rn=retrain_note:
                       self._cooc_status.configure(
                text=(f"{s['sets']} sets · {s['pairs']} paires · "
                      f"{s['matched_tracks']} tracks reconnues "
                      f"({s['unmatched_tracks']} non matchées)" + rn),
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


    def _run_fma_cleanup(self):
        """Delete the FMA zip + extracted MP3s. Embeddings already in
        the DB are NOT affected (they're stored separately). Frees up
        to ~11 GB."""
        from tkinter import messagebox
        if not messagebox.askyesno(
                "Nettoyer FMA ?",
                "Supprime data/fma/ (zip ~7 GB + MP3 extraits ~4 GB). "
                "Les embeddings déjà calculés et stockés en DB sont "
                "conservés — seul l'audio brut est effacé. Tu devras "
                "re-télécharger si tu veux ré-importer plus tard. "
                "Continuer ?"):
            return

        self._fma_cleanup_btn.configure(state="disabled",
                                         text="Nettoyage…")

        import threading

        def work():
            try:
                from app.engine import fma
                freed_z = fma.cleanup_zip()
                freed_e = fma.cleanup_extracted()
                freed = (freed_z + freed_e) / 1024**3
                self.after(0, lambda f=freed:
                            self._fma_disk_label.configure(
                                text=f"FMA nettoyé — {f:.1f} GB libérés",
                                text_color=COLORS["success"]))
            except Exception as e:
                from app.logger import log_error
                log_error("fma cleanup failed", e)
            finally:
                self.after(0, lambda: self._fma_cleanup_btn.configure(
                    state="normal",
                    text="Nettoyer FMA (libère le disque)"))
                self.after(500, self._refresh_fma_disk)

        threading.Thread(target=work, daemon=True,
                          name="fma-cleanup").start()


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

