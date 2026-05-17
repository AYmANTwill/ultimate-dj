"""
Discover page — AI-powered Playlist Akinator.

1. User describes their style + gives seed songs
2. Engine generates a playlist from Spotify recs + 1001tracklists
3. User keeps tracks they like, deletes others
4. Click "Regenerate" to fill gaps with new suggestions
5. Repeat until the perfect playlist emerges
"""
from __future__ import annotations

import threading
import webbrowser
import customtkinter as ctk

from app.config import COLORS
from app.engine.discovery import (
    generate_playlist, record_like, record_dislike,
    get_taste, set_description, search_spotify,
)
from app.engine.spotify import format_duration
from app.ui import helpers


class DiscoverPage(ctk.CTkFrame):
    def __init__(self, parent):
        super().__init__(parent, fg_color=COLORS["bg_dark"])
        self._playlist: list[dict] = []   # current generated tracks
        self._kept: list[dict] = []       # tracks the user kept
        self._build_ui()

    def _build_ui(self):
        # ── Header ───────────────────────────────────────────
        ctk.CTkLabel(
            self, text="Discover",
            font=ctk.CTkFont(size=26, weight="bold"),
            text_color=COLORS["text"],
        ).pack(anchor="w", padx=30, pady=(24, 2))
        ctk.CTkLabel(
            self, text="AI Playlist Akinator — describe your vibe, keep what you love, regenerate the rest",
            font=ctk.CTkFont(size=13), text_color=COLORS["text_dim"],
        ).pack(anchor="w", padx=30, pady=(0, 12))

        # ── Input section ────────────────────────────────────
        input_card = ctk.CTkFrame(self, fg_color=COLORS["bg_card"], corner_radius=12)
        input_card.pack(fill="x", padx=30, pady=(0, 8))

        # Style description
        ctk.CTkLabel(input_card, text="Describe your style / vibe:",
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=COLORS["text_dim"]).pack(anchor="w", padx=16, pady=(12, 2))
        self.desc_entry = ctk.CTkEntry(
            input_card, height=36,
            placeholder_text="e.g. hard techno, rave energy, 140-150 BPM, dark melodic drops...",
            fg_color=COLORS["bg_input"], border_color=COLORS["bg_input"],
            text_color=COLORS["text"], font=ctk.CTkFont(size=12))
        self.desc_entry.pack(fill="x", padx=16, pady=(0, 8))

        # Seed songs — autocomplete search
        ctk.CTkLabel(input_card,
                     text="Seed songs — tape pour chercher sur Spotify et choisis :",
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=COLORS["text_dim"]
                     ).pack(anchor="w", padx=16, pady=(4, 2))

        seed_row = ctk.CTkFrame(input_card, fg_color="transparent")
        seed_row.pack(fill="x", padx=16, pady=(0, 4))
        self.seed_search = ctk.CTkEntry(
            seed_row, height=32,
            placeholder_text="Cherche un morceau ou un artiste…",
            fg_color=COLORS["bg_input"], border_color=COLORS["bg_input"],
            text_color=COLORS["text"])
        self.seed_search.pack(side="left", fill="x", expand=True)
        self.seed_search.bind("<KeyRelease>", self._on_seed_typing)

        self.seed_suggest_box = ctk.CTkScrollableFrame(
            input_card, fg_color=COLORS["bg_input"], height=110)
        self.seed_suggest_box.pack(fill="x", padx=16, pady=(0, 4))
        ctk.CTkLabel(self.seed_suggest_box,
                     text="Les suggestions Spotify apparaîtront ici.",
                     text_color=COLORS["text_dim"],
                     font=ctk.CTkFont(size=11)).pack(pady=10)

        # Selected seeds — chips
        self._seeds: list[dict] = []
        self.seed_chips = ctk.CTkFrame(input_card, fg_color="transparent")
        self.seed_chips.pack(fill="x", padx=16, pady=(0, 8))
        self._render_chips()

        self._seed_search_after: str | None = None

        # Controls row
        ctrl = ctk.CTkFrame(input_card, fg_color="transparent")
        ctrl.pack(fill="x", padx=16, pady=(0, 12))

        ctk.CTkLabel(ctrl, text="Tracks:", text_color=COLORS["text_dim"],
                     font=ctk.CTkFont(size=12)).pack(side="left")
        self.count_var = ctk.StringVar(value="20")
        ctk.CTkEntry(ctrl, textvariable=self.count_var, width=50, height=32,
                      fg_color=COLORS["bg_input"], border_color=COLORS["bg_input"],
                      text_color=COLORS["text"]).pack(side="left", padx=(4, 16))

        self.gen_btn = ctk.CTkButton(
            ctrl, text="Generate Playlist", height=36, width=180,
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
            text_color=COLORS["bg_dark"],
            command=self._generate)
        self.gen_btn.pack(side="left", padx=(0, 8))

        # Replaces the old "Regenerate Gaps" button — same code path,
        # just expressed as a modifier on the main Generate action.
        self.keep_likes_var = ctk.BooleanVar(value=False)
        self.keep_likes_chk = ctk.CTkCheckBox(
            ctrl, text="Garder mes likes", variable=self.keep_likes_var,
            font=ctk.CTkFont(size=11),
            text_color=COLORS["text_dim"],
            checkbox_height=18, checkbox_width=18,
            fg_color=COLORS["accent"])
        self.keep_likes_chk.pack(side="left", padx=(0, 8))
        # Keep the symbol attribute around for code that already references
        # self.regen_btn — point it at gen_btn so the existing thread code
        # (which configures both buttons) stays valid.
        self.regen_btn = self.gen_btn

        self.status_label = ctk.CTkLabel(
            ctrl, text="", font=ctk.CTkFont(size=11),
            text_color=COLORS["text_dim"])
        self.status_label.pack(side="right", padx=8)

        # ── Taste profile summary ────────────────────────────
        self.taste_frame = ctk.CTkFrame(self, fg_color=COLORS["bg_card"], corner_radius=10)
        self.taste_frame.pack(fill="x", padx=30, pady=(0, 8))
        self.taste_label = ctk.CTkLabel(
            self.taste_frame, text="Your taste profile will appear here after you like/dislike tracks",
            font=ctk.CTkFont(size=11), text_color=COLORS["text_dim"])
        self.taste_label.pack(padx=12, pady=8)

        # ── 1001tracklists import (Phase 1) ───────────────────
        # Paste a tracklist URL → parse → match against the local lib.
        # Phase 2 will batch this; Phase 3 will use the matches as
        # co-occurrence signals for recommendations.
        tl_card = ctk.CTkFrame(self, fg_color=COLORS["bg_card"], corner_radius=10)
        tl_card.pack(fill="x", padx=30, pady=(0, 8))
        tl_row = ctk.CTkFrame(tl_card, fg_color="transparent")
        tl_row.pack(fill="x", padx=12, pady=8)
        ctk.CTkLabel(tl_row, text="1001tracklists URL :",
                     text_color=COLORS["text_dim"],
                     font=ctk.CTkFont(size=11)
                     ).pack(side="left", padx=(0, 6))
        self.tl_url_entry = ctk.CTkEntry(
            tl_row, height=30,
            placeholder_text="https://www.1001tracklists.com/tracklist/...",
            fg_color=COLORS["bg_input"], border_color=COLORS["bg_input"],
            text_color=COLORS["text"])
        self.tl_url_entry.pack(side="left", fill="x", expand=True, padx=4)
        ctk.CTkButton(
            tl_row, text="Importer", width=100, height=30,
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
            text_color=COLORS["bg_dark"],
            command=self._import_tracklist,
        ).pack(side="left", padx=4)
        # Bulk row — paste a DJ slug (e.g. "carl_cox") and grab the
        # last N sets in one shot. Big speed-up for building a
        # cooccurrence corpus.
        bulk_row = ctk.CTkFrame(tl_card, fg_color="transparent")
        bulk_row.pack(fill="x", padx=12, pady=(0, 8))
        ctk.CTkLabel(bulk_row, text="DJ slug :",
                     text_color=COLORS["text_dim"],
                     font=ctk.CTkFont(size=11)
                     ).pack(side="left", padx=(0, 6))
        self.dj_slug_entry = ctk.CTkEntry(
            bulk_row, height=28, placeholder_text="ex: carl_cox",
            fg_color=COLORS["bg_input"], border_color=COLORS["bg_input"],
            text_color=COLORS["text"], width=160)
        self.dj_slug_entry.pack(side="left")
        ctk.CTkLabel(bulk_row, text="× ",
                     text_color=COLORS["text_dim"],
                     font=ctk.CTkFont(size=11)
                     ).pack(side="left", padx=(8, 0))
        self.dj_count_var = ctk.StringVar(value="20")
        ctk.CTkEntry(
            bulk_row, textvariable=self.dj_count_var, width=50, height=28,
            fg_color=COLORS["bg_input"], border_color=COLORS["bg_input"],
            text_color=COLORS["text"]).pack(side="left", padx=(0, 6))
        ctk.CTkButton(
            bulk_row, text="Scraper batch", width=130, height=28,
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color=COLORS["accent2"], hover_color="#e0356f",
            text_color="white",
            command=self._batch_scrape_dj,
        ).pack(side="left", padx=4)
        self.tl_status = ctk.CTkLabel(
            tl_card, text="", font=ctk.CTkFont(size=10),
            text_color=COLORS["text_dim"])
        self.tl_status.pack(anchor="w", padx=12, pady=(0, 6))

        # ── Progress ─────────────────────────────────────────
        self.progress = ctk.CTkProgressBar(
            self, fg_color=COLORS["bg_card"], progress_color=COLORS["accent"], height=4)
        self.progress.pack(fill="x", padx=30, pady=(0, 4))
        self.progress.set(0)

        # ── Playlist display ─────────────────────────────────
        self.playlist_scroll = ctk.CTkScrollableFrame(
            self, fg_color="transparent")
        self.playlist_scroll.pack(fill="both", expand=True, padx=30, pady=(0, 12))

        self._show_placeholder()

    def _show_placeholder(self):
        for w in self.playlist_scroll.winfo_children():
            w.destroy()
        # Replaces the old 6-step blocking tutorial with a one-line tip.
        ctk.CTkLabel(
            self.playlist_scroll,
            text="Décris ton style et/ou choisis des seeds, puis Generate.",
            font=ctk.CTkFont(size=12), text_color=COLORS["text_dim"],
        ).pack(expand=True, pady=20)

    def _update_taste_display(self):
        taste = get_taste()
        top_artists = sorted(taste.get("liked_artists", {}).items(),
                             key=lambda x: -x[1])[:6]
        artists_str = ", ".join(a for a, _ in top_artists) if top_artists else "none yet"
        bpm = taste.get("liked_bpm_range", [120, 150])
        keys = sorted(taste.get("liked_keys", {}).items(), key=lambda x: -x[1])[:4]
        keys_str = ", ".join(k for k, _ in keys) if keys else "any"
        iters = taste.get("iterations", 0)

        self.taste_label.configure(
            text=f"Taste: {artists_str}  |  BPM {bpm[0]:.0f}-{bpm[1]:.0f}  "
                 f"|  Keys: {keys_str}  |  Rounds: {iters}",
            text_color=COLORS["text"])

    def on_show(self):
        self._update_taste_display()

    # ── 1001tracklists import ────────────────────────────────

    def _batch_scrape_dj(self):
        """Bulk-scrape the latest N sets from one DJ's profile.
        Politeness sleep is enforced inside engine.tracklists; this
        thread iterates and reports progress to BOTH the inline
        progress bar (top of page) AND the activity tray."""
        slug = self.dj_slug_entry.get().strip()
        if not slug:
            self.tl_status.configure(
                text="Donne un DJ slug (ex: carl_cox).",
                text_color=COLORS["warning"])
            return
        try:
            n = int(self.dj_count_var.get())
        except ValueError:
            n = 20
        n = max(1, min(n, 100))

        # Immediate visible feedback
        self.tl_status.configure(
            text=f"Découverte des sets de {slug}… "
                 f"(chargement playwright, peut prendre 30s)",
            text_color=COLORS["accent"])
        self.progress.set(0)

        def _ui(msg: str, color: str = "accent", frac: float | None = None):
            """Thread-safe UI update — schedule on main loop."""
            def _apply():
                try:
                    self.tl_status.configure(
                        text=msg, text_color=COLORS[color])
                    if frac is not None:
                        self.progress.set(max(0.0, min(1.0, frac)))
                except Exception:
                    pass
            try:
                self.after(0, _apply)
            except Exception:
                pass

        def work():
            from app.engine import tracklists, tasks
            task = tasks.register(f"Scrape {slug}",
                                    message="discovery…")
            try:
                try:
                    urls = tracklists.discover_dj_sets(slug, limit=n)
                except tracklists.IPLimitedError as e:
                    _ui(f"1001tracklists a bloqué cette IP — "
                        f"réessaie dans quelques heures ou via VPN",
                        color="error", frac=0)
                    tasks.complete(task.id, success=False,
                                    message=str(e)[:80])
                    return
                if not urls:
                    _ui(f"Aucun set trouvé pour {slug} — "
                        f"vérifie le slug sur 1001tracklists.com/dj/",
                        color="warning", frac=0)
                    tasks.complete(task.id, success=False,
                                    message=f"Aucun set trouvé pour {slug}")
                    return
                _ui(f"0/{len(urls)} sets scrapés…", color="accent",
                    frac=0.0)
                tasks.update(task.id, progress=0.0,
                              message=f"0/{len(urls)} sets")

                fetched = cached = failed = 0

                def progress(i, total, status, info):
                    if task.cancel_requested():
                        raise RuntimeError("cancel_requested")
                    nonlocal fetched, cached, failed
                    if status == "ok":
                        fetched += 1
                    elif status == "cache":
                        cached += 1
                    else:
                        failed += 1
                    frac = i / total
                    msg = (f"[{status}] {i}/{total}  ·  "
                            f"{info[:40]}")
                    tasks.update(task.id, progress=frac, message=msg)
                    _ui(msg, color="accent", frac=frac)

                try:
                    summary = tracklists.batch_scrape(
                        urls, on_progress=progress,
                        stop_event=task.cancel_event)
                except RuntimeError:
                    summary = {"fetched": fetched, "cached": cached,
                                "failed": failed}

                tasks.complete(
                    task.id, success=(summary["failed"] == 0),
                    message=f"+{summary['fetched']} nouveaux, "
                            f"{summary['cached']} en cache, "
                            f"{summary['failed']} échecs")
                _ui(f"Scrape OK : +{summary.get('fetched',0)} nouveaux, "
                    f"{summary.get('cached',0)} en cache, "
                    f"{summary.get('failed',0)} échecs",
                    color="success", frac=1.0)
            except Exception as e:
                tasks.complete(task.id, success=False,
                                message=f"Erreur : {str(e)[:60]}")
                _ui(f"Erreur : {str(e)[:80]}", color="error", frac=0)

        threading.Thread(target=work, daemon=True,
                          name="batch-dj-scrape").start()

    def _import_tracklist(self):
        url = self.tl_url_entry.get().strip()
        if not url or "1001tracklists.com" not in url:
            self.tl_status.configure(
                text="Colle une URL 1001tracklists valide.",
                text_color=COLORS["warning"])
            return
        self.tl_status.configure(
            text="Téléchargement en cours… (5-10s, contournement Cloudflare)",
            text_color=COLORS["accent"])
        threading.Thread(
            target=self._tl_thread, args=(url,), daemon=True).start()

    def _tl_thread(self, url: str):
        from app.engine import tracklists
        from app.engine.library import get_connection
        try:
            tl = tracklists.fetch_tracklist(url)
        except Exception as e:
            self.after(0, lambda err=str(e): self.tl_status.configure(
                text=f"Erreur : {err}", text_color=COLORS["error"]))
            return
        # Match against local library
        try:
            matches = tracklists.match_with_library(tl, get_connection())
        except Exception:
            matches = []
        n_total = len(tl.get("tracks", []))
        n_matched = sum(1 for m in matches if m.get("match"))
        cached = " (cache)" if tl.get("cached") else ""
        title = tl.get("title", "(sans titre)")[:60]
        self.after(0, lambda: self.tl_status.configure(
            text=f"« {title} »{cached} — {n_total} tracks, "
                 f"{n_matched} déjà dans ta lib.",
            text_color=COLORS["success"] if n_matched else COLORS["warning"]))

    # ── Generation ───────────────────────────────────────────

    def _generate(self):
        self.gen_btn.configure(state="disabled", text="Generating...")
        self.status_label.configure(text="Searching...", text_color=COLORS["accent"])
        self.progress.set(0.1)
        # Behaviour driven by the "Garder mes likes" checkbox now —
        # replaces the old "Regenerate Gaps" second button.
        keep = bool(self.keep_likes_var.get())
        if not keep:
            self._kept = []
        threading.Thread(
            target=self._gen_thread, args=(keep,), daemon=True).start()

    def _regenerate(self):
        """Backwards-compat alias — same as Generate with keep=True."""
        self.keep_likes_var.set(True)
        self._generate()

    # ── Seed autocomplete ────────────────────────────────────

    def _render_chips(self):
        for w in self.seed_chips.winfo_children():
            w.destroy()
        if not self._seeds:
            ctk.CTkLabel(
                self.seed_chips,
                text="(aucun seed sélectionné — tape au-dessus pour chercher)",
                text_color=COLORS["text_dim"],
                font=ctk.CTkFont(size=10)).pack(anchor="w")
            return
        for s in self._seeds:
            chip = ctk.CTkFrame(self.seed_chips, fg_color=COLORS["accent"],
                                 corner_radius=12)
            chip.pack(side="left", padx=4, pady=4)
            label = f"{s.get('artist','')[:18]} — {s.get('title','')[:24]}"
            ctk.CTkLabel(chip, text=label,
                         font=ctk.CTkFont(size=10, weight="bold"),
                         text_color=COLORS["bg_dark"]
                         ).pack(side="left", padx=(8, 4), pady=2)
            ctk.CTkButton(
                chip, text="x", width=18, height=18,
                fg_color="transparent", hover_color=COLORS["accent_hover"],
                text_color=COLORS["bg_dark"],
                font=ctk.CTkFont(size=10, weight="bold"),
                command=lambda t=s: self._remove_seed(t)
            ).pack(side="left", padx=(0, 6))

    def _remove_seed(self, track: dict):
        self._seeds = [t for t in self._seeds
                       if t.get("spotify_id") != track.get("spotify_id")]
        self._render_chips()

    def _add_seed(self, track: dict):
        if any(t.get("spotify_id") == track.get("spotify_id")
               for t in self._seeds):
            return
        if len(self._seeds) >= 5:
            helpers.warn("Limite de seeds",
                         "Spotify accepte 5 seeds maximum. Retire-en un avant.")
            return
        self._seeds.append(track)
        self._render_chips()
        self.seed_search.delete(0, "end")
        self._clear_seed_suggestions()

    def _clear_seed_suggestions(self):
        for w in self.seed_suggest_box.winfo_children():
            w.destroy()

    def _on_seed_typing(self, _event=None):
        if self._seed_search_after:
            try:
                self.after_cancel(self._seed_search_after)
            except Exception:
                pass
        q = self.seed_search.get().strip()
        if len(q) < 2:
            self._clear_seed_suggestions()
            ctk.CTkLabel(self.seed_suggest_box,
                         text="Tape au moins 2 caractères…",
                         text_color=COLORS["text_dim"],
                         font=ctk.CTkFont(size=11)).pack(pady=8)
            return
        self._seed_search_after = self.after(
            350, lambda: threading.Thread(
                target=self._seed_search_thread, args=(q,), daemon=True
            ).start())

    def _seed_search_thread(self, query: str):
        results = search_spotify(query, limit=8)
        self.after(0, lambda: self._show_seed_suggestions(results))

    def _show_seed_suggestions(self, results: list[dict]):
        self._clear_seed_suggestions()
        if not results:
            ctk.CTkLabel(
                self.seed_suggest_box,
                text="Pas de résultat. Vérifie ta config Spotify dans Settings.",
                text_color=COLORS["warning"],
                font=ctk.CTkFont(size=11)).pack(pady=8)
            return
        for t in results:
            row = ctk.CTkButton(
                self.seed_suggest_box,
                text=f"  {t.get('artist','')[:25]}  —  {t.get('title','')[:40]}",
                anchor="w", height=26,
                fg_color="transparent", hover_color=COLORS["bg_card"],
                text_color=COLORS["text"], font=ctk.CTkFont(size=11),
                command=lambda track=t: self._add_seed(track))
            row.pack(fill="x", padx=4, pady=1)

    # ── Generation ───────────────────────────────────────────

    def _gen_thread(self, is_regen=False):
        desc = self.desc_entry.get().strip()
        seeds = [f"{s.get('artist','')} {s.get('title','')}".strip()
                 for s in self._seeds]

        try:
            count = int(self.count_var.get())
        except ValueError:
            count = 20
        count = max(5, min(count, 50))

        if desc:
            set_description(desc)

        self.after(0, lambda: self.progress.set(0.3))

        if is_regen:
            # Only generate enough to fill gaps
            need = count - len(self._kept)
            need = max(5, need)
            playlist = generate_playlist(
                seed_songs=seeds or None,
                style_desc=desc,
                count=need,
                kept_tracks=self._kept,
            )
            self._playlist = self._kept + playlist
        else:
            self._playlist = generate_playlist(
                seed_songs=seeds or None,
                style_desc=desc,
                count=count,
            )

        self.after(0, lambda: self.progress.set(1.0))
        self.after(0, self._display_playlist)
        self.after(0, lambda: self.gen_btn.configure(state="normal", text="Generate Playlist"))
        self.after(0, lambda: self.regen_btn.configure(state="normal", text="Regenerate Gaps"))
        n = len(self._playlist)
        self.after(0, lambda: self.status_label.configure(
            text=f"{n} tracks found  |  {len(self._kept)} kept",
            text_color=COLORS["success"]))
        self.after(0, self._update_taste_display)

    # ── Display ──────────────────────────────────────────────

    def _display_playlist(self):
        for w in self.playlist_scroll.winfo_children():
            w.destroy()

        if not self._playlist:
            ctk.CTkLabel(self.playlist_scroll,
                         text="No tracks found. Try different seeds or description.",
                         text_color=COLORS["warning"],
                         font=ctk.CTkFont(size=13)).pack(pady=30)
            return

        for i, track in enumerate(self._playlist):
            is_kept = track in self._kept
            self._add_track_row(i, track, is_kept)

    def _add_track_row(self, idx: int, track: dict, is_kept: bool):
        row = ctk.CTkFrame(
            self.playlist_scroll,
            fg_color=COLORS["bg_card"] if not is_kept else "#1a2e1a",
            corner_radius=8, height=50)
        row.pack(fill="x", pady=2)
        row.pack_propagate(False)

        # Track number
        ctk.CTkLabel(row, text=f"{idx + 1:>2}", width=30,
                     font=ctk.CTkFont(size=12),
                     text_color=COLORS["text_dim"]).pack(side="left", padx=(8, 6))

        # Track info
        info_frame = ctk.CTkFrame(row, fg_color="transparent")
        info_frame.pack(side="left", fill="x", expand=True)

        title = track.get("title", "Unknown")[:45]
        artist = track.get("artist", "")[:30]
        source = track.get("source", "")

        ctk.CTkLabel(info_frame, text=title,
                     text_color=COLORS["text"],
                     font=ctk.CTkFont(size=12), anchor="w").pack(anchor="w", pady=(4, 0))

        meta_parts = []
        if artist:
            meta_parts.append(artist)
        dur = track.get("duration")
        if dur:
            meta_parts.append(format_duration(dur))
        if source:
            meta_parts.append(f"via {source}")
        ctk.CTkLabel(info_frame, text="  |  ".join(meta_parts),
                     text_color=COLORS["text_dim"],
                     font=ctk.CTkFont(size=10), anchor="w").pack(anchor="w")

        # Preview button — opens 30s Spotify clip or Spotify track page
        preview = track.get("preview_url") or ""
        sid = track.get("spotify_id") or ""
        if preview or sid:
            target = preview or f"https://open.spotify.com/track/{sid}"
            ctk.CTkButton(
                row, text="▶", width=32, height=28,
                font=ctk.CTkFont(size=12, weight="bold"),
                fg_color="#1DB954", hover_color="#168f3f", text_color="white",
                command=lambda u=target: webbrowser.open(u),
            ).pack(side="right", padx=2, pady=8)

        # Search on YouTube button
        ctk.CTkButton(
            row, text="YT", width=32, height=28,
            font=ctk.CTkFont(size=10),
            fg_color="#FF0000", hover_color="#CC0000", text_color="white",
            command=lambda t=track: webbrowser.open(
                f"https://www.youtube.com/results?search_query="
                f"{t.get('artist','')}+{t.get('title','')}".replace(" ", "+")),
        ).pack(side="right", padx=2, pady=8)

        # Add-to-library: jump to Download page with prefilled YouTube search
        ctk.CTkButton(
            row, text="↓", width=32, height=28,
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
            text_color=COLORS["bg_dark"],
            command=lambda t=track: self._send_to_download(t),
        ).pack(side="right", padx=2, pady=8)

        # Keep button
        if not is_kept:
            ctk.CTkButton(
                row, text="Keep", width=56, height=28,
                font=ctk.CTkFont(size=11, weight="bold"),
                fg_color=COLORS["success"], hover_color="#00c060",
                text_color=COLORS["bg_dark"],
                command=lambda t=track, r=row: self._keep_track(t, r),
            ).pack(side="right", padx=2, pady=8)
        else:
            ctk.CTkLabel(row, text="KEPT", width=56,
                         font=ctk.CTkFont(size=11, weight="bold"),
                         text_color=COLORS["success"]).pack(side="right", padx=6, pady=8)

        # Delete button
        ctk.CTkButton(
            row, text="X", width=32, height=28,
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color=COLORS["error"], hover_color="#cc3333",
            text_color="white",
            command=lambda t=track, r=row: self._delete_track(t, r),
        ).pack(side="right", padx=2, pady=8)

    def _keep_track(self, track: dict, row: ctk.CTkFrame):
        if track not in self._kept:
            self._kept.append(track)
            record_like(track)
        row.configure(fg_color="#1a2e1a")
        # Replace buttons
        for w in row.winfo_children():
            if isinstance(w, ctk.CTkButton) and w.cget("text") == "Keep":
                w.destroy()
                ctk.CTkLabel(row, text="KEPT", width=56,
                             font=ctk.CTkFont(size=11, weight="bold"),
                             text_color=COLORS["success"]).pack(side="right", padx=6, pady=8)
                break
        self.status_label.configure(
            text=f"{len(self._kept)} kept  |  {len(self._playlist)} total")
        self._update_taste_display()

    def _send_to_download(self, track: dict):
        """Switch to the Download page and prefill it with a YouTube search URL."""
        artist = track.get("artist", "")
        title = track.get("title", "")
        query = f"{artist} {title}".strip().replace(" ", "+")
        url = f"https://www.youtube.com/results?search_query={query}"
        top = self.winfo_toplevel()
        if hasattr(top, "_switch_page"):
            top._switch_page("Download")
            page = top._page_cache.get("Download")
            if page and hasattr(page, "url_entry"):
                page.url_entry.delete(0, "end")
                page.url_entry.insert(0, url)
                helpers.info(
                    "Lien chargé",
                    "Ouvre la première vidéo du résultat dans la barre d'URL "
                    "puis clique sur Télécharger.")

    def _delete_track(self, track: dict, row: ctk.CTkFrame):
        record_dislike(track)
        if track in self._playlist:
            self._playlist.remove(track)
        if track in self._kept:
            self._kept.remove(track)
        row.destroy()
        self.status_label.configure(
            text=f"{len(self._kept)} kept  |  {len(self._playlist)} remaining")
