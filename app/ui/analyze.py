"""
Analysis page — analyze single track or scan entire folder.
Shows BPM, key, energy with progress feedback.
Includes Stop and Pause/Resume controls.
"""
from __future__ import annotations

import os
import time
import threading
from pathlib import Path

import customtkinter as ctk
from tkinter import filedialog

from app.config import COLORS, load_config
from app.engine.analyzer import analyze_track, write_tags
from app.engine.library import get_connection, upsert_track
from app.logger import log_error, log_info
from app.ui.fastlist import FastList
from app.ui.helpers import UiThrottle, font


class AnalyzePage(ctk.CTkFrame):

    def __init__(self, parent):
        super().__init__(parent, fg_color=COLORS["bg_dark"])
        self._stop_event  = threading.Event()
        self._pause_event = threading.Event()
        self._running = False
        # Throttles for high-frequency progress updates (one update per ~80ms
        # is more than enough for the eye and keeps Tk's queue empty).
        self._status_throttle: UiThrottle | None = None
        self._progress_throttle: UiThrottle | None = None
        self._build_ui()
        self._status_throttle = UiThrottle(self, interval_ms=80)
        self._progress_throttle = UiThrottle(self, interval_ms=80)

    def _build_ui(self):
        ctk.CTkLabel(
            self, text="Analyze",
            font=ctk.CTkFont(size=26, weight="bold"),
            text_color=COLORS["text"],
        ).pack(anchor="w", padx=30, pady=(24, 4))
        ctk.CTkLabel(
            self, text="Detect BPM, key, and energy — results saved to library + MP3 tags",
            font=ctk.CTkFont(size=13), text_color=COLORS["text_dim"],
        ).pack(anchor="w", padx=30, pady=(0, 16))

        # ── Action row ─────────────────────────────────────────
        btn_frame = ctk.CTkFrame(self, fg_color=COLORS["bg_card"], corner_radius=12)
        btn_frame.pack(fill="x", padx=30, pady=(0, 12))

        self.file_btn = ctk.CTkButton(
            btn_frame, text="Analyze File", width=150, height=40,
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
            text_color=COLORS["bg_dark"],
            command=self._pick_file)
        self.file_btn.pack(side="left", padx=16, pady=14)

        self.folder_btn = ctk.CTkButton(
            btn_frame, text="Scan Folder", width=150, height=40,
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color=COLORS["accent2"], hover_color="#e0356f",
            text_color=COLORS["on_accent2"],
            command=self._pick_folder)
        self.folder_btn.pack(side="left", padx=(0, 16), pady=14)

        # Stop / Pause (right side of the same bar)
        self.stop_btn = ctk.CTkButton(
            btn_frame, text="Stop", width=70, height=36,
            font=ctk.CTkFont(size=12),
            fg_color=COLORS["error"], hover_color="#cc3333",
            text_color="white", state="disabled",
            command=self._stop_analysis)
        self.stop_btn.pack(side="right", padx=(4, 16), pady=14)

        self.pause_btn = ctk.CTkButton(
            btn_frame, text="Pause", width=80, height=36,
            font=ctk.CTkFont(size=12),
            fg_color=COLORS["warning"], hover_color="#e09900",
            text_color=COLORS["bg_dark"], state="disabled",
            command=self._toggle_pause)
        self.pause_btn.pack(side="right", padx=4, pady=14)

        self.status_label = ctk.CTkLabel(
            btn_frame, text="", font=ctk.CTkFont(size=12),
            text_color=COLORS["text_dim"])
        self.status_label.pack(side="left", padx=8)

        # ── Progress bar ───────────────────────────────────────
        self.progress = ctk.CTkProgressBar(
            self, fg_color=COLORS["bg_card"],
            progress_color=COLORS["accent"], height=6)
        self.progress.pack(fill="x", padx=30, pady=(0, 10))
        self.progress.set(0)

        # ── Results list (FastList — handles 1000s of rows) ────
        self.results_table = FastList(
            self,
            [("title",   "Title",    320),
             ("bpm",     "BPM",       70),
             ("key",     "Key",      120),
             ("camelot", "Camelot",   80),
             ("energy",  "Energy",    70)],
            sortable=False,
            height=14,
        )
        self.results_table.pack(fill="both", expand=True, padx=30, pady=(0, 8))

        # ── Error log row ──────────────────────────────────────
        log_hdr = ctk.CTkFrame(self, fg_color="transparent")
        log_hdr.pack(fill="x", padx=30, pady=(0, 2))
        ctk.CTkLabel(log_hdr, text="Errors",
                     font=ctk.CTkFont(size=10),
                     text_color=COLORS["text_dim"]).pack(side="left")
        ctk.CTkButton(
            log_hdr, text="Open log file", width=90, height=20,
            font=ctk.CTkFont(size=10),
            fg_color="transparent", hover_color=COLORS["bg_card"],
            text_color=COLORS["text_dim"],
            command=self._open_log,
        ).pack(side="right")

        self.err_log = ctk.CTkTextbox(
            self, height=70, fg_color=COLORS["bg_card"],
            text_color=COLORS["error"],
            font=ctk.CTkFont(size=11, family="Consolas"),
            state="disabled", corner_radius=10)
        self.err_log.pack(fill="x", padx=30, pady=(0, 12))

    # ── Helpers ───────────────────────────────────────────────

    def _log_err(self, msg: str):
        self.err_log.configure(state="normal")
        self.err_log.insert("end", msg + "\n")
        self.err_log.see("end")
        self.err_log.configure(state="disabled")

    def _open_log(self):
        from app.logger import get_log_path
        path = get_log_path()
        if os.path.exists(path):
            os.startfile(path)

    # ── Running state ─────────────────────────────────────────

    def _set_running(self, running: bool):
        self._running = running
        state = "disabled" if running else "normal"
        self.file_btn.configure(state=state)
        self.folder_btn.configure(state=state)
        if running:
            self.stop_btn.configure(state="normal")
            self.pause_btn.configure(state="normal", text="Pause",
                                      fg_color=COLORS["warning"])
        else:
            self.stop_btn.configure(state="disabled")
            self.pause_btn.configure(state="disabled", text="Pause",
                                      fg_color=COLORS["warning"])
            self._pause_event.clear()

    def _stop_analysis(self):
        self._stop_event.set()
        self._pause_event.clear()
        self.status_label.configure(
            text="Stopping after current file…",
            text_color=COLORS["warning"])
        self.stop_btn.configure(state="disabled")

    def _toggle_pause(self):
        if self._pause_event.is_set():
            self._pause_event.clear()
            self.pause_btn.configure(text="Pause", fg_color=COLORS["warning"])
            self.status_label.configure(text="Resumed",
                                         text_color=COLORS["accent"])
        else:
            self._pause_event.set()
            self.pause_btn.configure(text="Resume", fg_color=COLORS["success"])
            self.status_label.configure(text="Paused — click Resume",
                                         text_color=COLORS["warning"])

    # ── File picking ──────────────────────────────────────────

    def _pick_file(self):
        path = filedialog.askopenfilename(
            title="Select audio file",
            filetypes=[("Audio", "*.mp3 *.wav *.flac *.ogg *.m4a"),
                       ("All", "*.*")])
        if path:
            self._start_analysis([path])

    def _pick_folder(self):
        folder = filedialog.askdirectory(title="Select folder to scan")
        if folder:
            exts = {".mp3", ".wav", ".flac", ".ogg", ".m4a"}
            files = [str(p) for p in Path(folder).rglob("*")
                     if p.suffix.lower() in exts]
            if files:
                self._start_analysis(files)
            else:
                self.status_label.configure(
                    text="No audio files found",
                    text_color=COLORS["warning"])

    def _start_analysis(self, files: list[str]):
        self._stop_event.clear()
        self._pause_event.clear()
        self._set_running(True)
        self.progress.set(0)
        self.results_table.clear()
        self.status_label.configure(
            text=f"Analyzing {len(files)} file(s)…",
            text_color=COLORS["accent"])
        log_info(f"Analysis started: {len(files)} files")
        threading.Thread(
            target=self._analyze_thread,
            args=(files,), daemon=True).start()

    # ── Analysis thread ───────────────────────────────────────

    def _analyze_thread(self, files: list[str]):
        # Per-thread connection (engine.library uses thread-local storage)
        conn = get_connection()
        results = []
        total = len(files)

        for i, path in enumerate(files, 1):
            # Stop check
            if self._stop_event.is_set():
                self._status_throttle.call_now(
                    lambda i=i, total=total: self.status_label.configure(
                        text=f"Stopped at {i-1}/{total}",
                        text_color=COLORS["warning"]))
                break

            # Pause check — block until resumed
            if self._pause_event.is_set():
                self._status_throttle.call_now(
                    lambda: self.status_label.configure(
                        text="Paused — click Resume",
                        text_color=COLORS["warning"]))
                while self._pause_event.is_set():
                    if self._stop_event.is_set():
                        break
                    time.sleep(0.2)
                if self._stop_event.is_set():
                    break

            # Throttled — only the latest status/progress within 80ms wins
            self._status_throttle.call(
                lambda i=i, total=total: self.status_label.configure(
                    text=f"Analyzing {i}/{total}…",
                    text_color=COLORS["accent"]))
            self._progress_throttle.call(
                lambda i=i, total=total: self.progress.set(i / total))

            try:
                info = analyze_track(path)
                write_tags(path, info["bpm"], info["key"])
                upsert_track(conn, info)
                results.append(info)
                # Result rows aren't throttled — every track gets its row,
                # but they're cheap (one frame + 5 labels) and infrequent
                # (one librosa pass per file = at least a few seconds).
                self.after(0, lambda info=info: self._add_result_row(info))
            except Exception as e:
                log_error(f"analyze_track failed: {path}", e)
                self.after(0, lambda p=path, e=e: self._add_error_row(p, str(e)))

        done = len(results)

        if not self._stop_event.is_set():
            log_info(f"Analysis done: {done}/{total}")
            # Terminal status — bypass throttle so user sees the final state
            self._status_throttle.call_now(
                lambda: self.status_label.configure(
                    text=f"Done — {done}/{total} analyzed",
                    text_color=COLORS["success"]))
            self._progress_throttle.call_now(lambda: self.progress.set(1.0))

        self.after(0, lambda: self._set_running(False))

    # ── Result rows ───────────────────────────────────────────

    def _add_result_row(self, info: dict):
        self.results_table.append((
            (info["title"] or "?")[:60],
            f"{info['bpm']:.0f}",
            info["key"] or "?",
            info["camelot"] or "?",
            f"{info['energy']:.1f}",
        ), tags=("ok",))

    def _add_error_row(self, path: str, error: str):
        self.results_table.append((
            f"✗  {Path(path).name[:60]}",
            "—", "—", "—", "—",
        ), tags=("err",))
        self._log_err(f"{Path(path).name}: {error}")
