"""Recorder page — record a live set: the track sequence (Rekordbox
history) AND every DDJ-FLX4 gesture (MIDI + wake-up SysEx), together,
while you mix in Rekordbox. Saves a set document to data/recorded_sets/.

UI stays on the Tk thread; the recorders run their own threads and push
updates back via self.after()."""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

import customtkinter as ctk

from app.config import COLORS, DATA_DIR
from app.engine import midi_recorder as mr
from app.engine.set_recorder import SetRecorder

_REFRESH_MS = 1000


class RecorderPage(ctk.CTkFrame):
    def __init__(self, parent):
        super().__init__(parent, fg_color="transparent")
        self._tracks_rec: SetRecorder | None = None
        self._midi: mr.MidiRecorder | None = None
        self._tracks: list[dict] = []
        self._controls: dict[str, int] = {}   # label -> hit count
        self._t0 = 0.0

        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x", padx=16, pady=(14, 6))
        ctk.CTkLabel(head, text="🎥 ENREGISTRER UN SET",
                     font=ctk.CTkFont(size=22, weight="bold"),
                     text_color=COLORS["accent"]).pack(side="left")
        self.btn = ctk.CTkButton(head, text="● Démarrer", width=170,
                                 fg_color=COLORS["accent"],
                                 command=self._toggle)
        self.btn.pack(side="right")
        self.status = ctk.CTkLabel(
            head, text="Prêt — lance Rekordbox, clique Démarrer, puis mixe.",
            font=ctk.CTkFont(size=11), text_color=COLORS["text_dim"])
        self.status.pack(side="right", padx=12)

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=16, pady=(4, 12))
        body.grid_columnconfigure(0, weight=3)
        body.grid_columnconfigure(1, weight=2)
        body.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(body, text="MORCEAUX JOUÉS",
                     font=ctk.CTkFont(size=10, weight="bold"),
                     text_color=COLORS["text_dim"]).grid(
            row=0, column=0, sticky="w")
        self.tracks_box = ctk.CTkTextbox(
            body, fg_color=COLORS["bg_card"], text_color=COLORS["text"],
            font=ctk.CTkFont(size=11), wrap="none")
        self.tracks_box.grid(row=1, column=0, sticky="nsew", padx=(0, 8))
        self.tracks_box.configure(state="disabled")

        ctk.CTkLabel(body, text="GESTES CAPTÉS (contrôleur)",
                     font=ctk.CTkFont(size=10, weight="bold"),
                     text_color=COLORS["text_dim"]).grid(
            row=0, column=1, sticky="w")
        self.gest_frame = ctk.CTkScrollableFrame(
            body, fg_color=COLORS["bg_card"], corner_radius=8)
        self.gest_frame.grid(row=1, column=1, sticky="nsew")

        self.after(_REFRESH_MS, self._tick)

    # ── actions ─────────────────────────────────────────────────

    def _toggle(self):
        if self._tracks_rec and self._tracks_rec.is_running():
            self._stop()
        else:
            self._start()

    def _start(self):
        self._tracks = []
        self._controls = {}
        self._t0 = time.time()
        for w in self.gest_frame.winfo_children():
            w.destroy()
        self._tracks_rec = SetRecorder(poll_interval=5.0)
        started = self._tracks_rec.start(
            on_track=lambda e: self.after(0, self._on_track, e))
        # Gestures (optional — only if a controller is present)
        self._midi = None
        if mr.available() and mr.find_controller():
            self._midi = mr.MidiRecorder()
            if not self._midi.start(
                    on_event=lambda ev: self.after(0, self._on_gesture, ev)):
                self._midi = None

        if not started and self._midi is None:
            self.status.configure(
                text="Impossible de démarrer (Rekordbox ouvert ?).",
                text_color=COLORS["warning"])
            return
        bits = []
        if started:
            bits.append("morceaux")
        if self._midi:
            bits.append(f"gestes ({self._midi.port_name})")
        self.btn.configure(text="■ Arrêter & sauver",
                           fg_color=COLORS["error"])
        self.status.configure(text="● Enregistrement : " + " + ".join(bits),
                              text_color=COLORS["success"])

    def _stop(self):
        tdoc = self._tracks_rec.stop(save=False) if self._tracks_rec else {}
        events = self._midi.stop() if self._midi else []
        doc = {
            "name": tdoc.get("name") or "Set",
            "started_at": tdoc.get("started_at", ""),
            "duration_s": round(time.time() - self._t0, 1),
            "tracks": tdoc.get("tracks", []),
            "gestures": [{**e, "ctrl": mr.label(e)} for e in events],
        }
        path = _save(doc)
        n_ctrl = len({g["ctrl"] for g in doc["gestures"]})
        self.btn.configure(text="● Démarrer", fg_color=COLORS["accent"])
        self.status.configure(
            text=(f"■ Sauvé : {len(doc['tracks'])} morceaux · "
                  f"{len(events)} gestes ({n_ctrl} contrôles) → {path.name}"),
            text_color=COLORS["success"])
        self._tracks_rec = None
        self._midi = None

    # ── worker callbacks (marshalled to Tk thread) ──────────────

    def _on_track(self, e: dict):
        self._tracks.append(e)

    def _on_gesture(self, ev: dict):
        lb = mr.label(ev)
        self._controls[lb] = self._controls.get(lb, 0) + 1

    # ── render loop ─────────────────────────────────────────────

    def _tick(self):
        try:
            self._render()
        except Exception:
            pass
        self.after(_REFRESH_MS, self._tick)

    def _render(self):
        self.tracks_box.configure(state="normal")
        self.tracks_box.delete("1.0", "end")
        for e in self._tracks:
            self.tracks_box.insert(
                "end", f"{e['pos']:>2}. {e['artist']} — {e['title']}\n")
        self.tracks_box.configure(state="disabled")

        existing = {}
        for c in self.gest_frame.winfo_children():
            base = c.cget("text").split("  ×")[0]
            existing[base] = c
        for lb, n in sorted(self._controls.items(), key=lambda kv: -kv[1]):
            txt = f"{lb}  ×{n}"
            if lb in existing:
                existing[lb].configure(text=txt)
            else:
                ctk.CTkLabel(self.gest_frame, text=txt, anchor="w",
                             font=ctk.CTkFont(size=12),
                             text_color=COLORS["text"]).pack(
                    fill="x", padx=6, pady=1)


def _save(doc: dict) -> Path:
    d = DATA_DIR / "recorded_sets"
    d.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^0-9A-Za-z_-]+", "-", doc["name"]).strip("-") or "set"
    p = d / f"{safe}.json"
    p.write_text(json.dumps(doc, ensure_ascii=False, indent=1),
                 encoding="utf-8")
    return p
