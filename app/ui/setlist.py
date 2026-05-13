"""
Setlist builder — pick a starting track, auto-generate an ordered setlist,
edit it manually (move ↑/↓, lock slots, regenerate around fixed positions).

Performance: track loading and setlist generation both run off the UI thread.
The setlist itself is a FastList so even a 50-track setlist renders instantly.

Editor model:
- Each slot has a "locked" flag. When the user clicks Regenerate, only
  unlocked slots are re-computed; locked tracks stay where they are.
- ↑ / ↓ buttons swap a track with its neighbour and recompute scores.
"""
from __future__ import annotations

import threading

import customtkinter as ctk

from app.config import COLORS
from app.engine.library import (
    get_connection, all_tracks, build_setlist_auto, transition_score,
    save_setlist, load_setlist, list_setlists, delete_setlist,
)
from app.logger import log_error
from app.ui.fastlist import FastList
from app.ui.helpers import font, confirm


class SetlistPage(ctk.CTkFrame):
    def __init__(self, parent):
        super().__init__(parent, fg_color=COLORS["bg_dark"])
        self._tracks: list[dict] = []
        # Setlist model: each slot is (track, score, locked)
        self._setlist: list[tuple[dict, float, bool]] = []
        self._build_ui()

    def _build_ui(self):
        ctk.CTkLabel(
            self, text="Setlist Builder",
            font=font(26, "bold"),
            text_color=COLORS["text"],
        ).pack(anchor="w", padx=30, pady=(24, 4))
        ctk.CTkLabel(
            self, text="Auto-generate a harmonically ordered setlist",
            font=font(13),
            text_color=COLORS["text_dim"],
        ).pack(anchor="w", padx=30, pady=(0, 16))

        # Controls ─────────────────────────────────────────────────
        ctrl = ctk.CTkFrame(self, fg_color=COLORS["bg_card"], corner_radius=12)
        ctrl.pack(fill="x", padx=30, pady=(0, 12))

        ctk.CTkLabel(ctrl, text="Start track:",
                     text_color=COLORS["text_dim"],
                     font=font(12)).pack(side="left", padx=(16, 6), pady=12)

        self.start_var = ctk.StringVar(value="")
        self.start_menu = ctk.CTkOptionMenu(
            ctrl, variable=self.start_var, values=["(load library first)"],
            width=350, height=34,
            fg_color=COLORS["bg_input"], button_color=COLORS["accent"],
            dropdown_fg_color=COLORS["bg_card"],
            text_color=COLORS["text"])
        self.start_menu.pack(side="left", padx=6, pady=12)

        ctk.CTkLabel(ctrl, text="Length:",
                     text_color=COLORS["text_dim"],
                     font=font(12)).pack(side="left", padx=(16, 4))
        self.length_var = ctk.StringVar(value="12")
        self.length_entry = ctk.CTkEntry(
            ctrl, textvariable=self.length_var, width=50, height=34,
            fg_color=COLORS["bg_input"], border_color=COLORS["bg_input"],
            text_color=COLORS["text"])
        self.length_entry.pack(side="left", pady=12)

        self.gen_btn = ctk.CTkButton(
            ctrl, text="Generate Setlist", height=36, width=160,
            font=font(14, "bold"),
            fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
            text_color=COLORS["bg_dark"],
            command=self._generate)
        self.gen_btn.pack(side="right", padx=16, pady=12)

        self.export_btn = ctk.CTkButton(
            ctrl, text="Export", height=36, width=100,
            font=font(13),
            fg_color=COLORS["accent2"], hover_color="#e0356f",
            text_color=COLORS["on_accent2"],
            command=self._export, state="disabled")
        self.export_btn.pack(side="right", padx=4, pady=12)

        # Persisted setlists — save the current one or recall a saved one
        self.save_btn = ctk.CTkButton(
            ctrl, text="Save…", height=36, width=80,
            font=font(13),
            fg_color=COLORS["bg_input"], hover_color=COLORS["accent"],
            text_color=COLORS["text"],
            command=self._save_dialog, state="disabled")
        self.save_btn.pack(side="right", padx=4, pady=12)

        self.load_btn = ctk.CTkButton(
            ctrl, text="Load…", height=36, width=80,
            font=font(13),
            fg_color=COLORS["bg_input"], hover_color=COLORS["accent"],
            text_color=COLORS["text"],
            command=self._load_dialog)
        self.load_btn.pack(side="right", padx=4, pady=12)

        # Setlist table ────────────────────────────────────────────
        # 🔒 column shows lock status. Click row → toggle lock.
        # ↑ / ↓ buttons (below the table) act on the selected row.
        self.table = FastList(
            self,
            [("n",     "#",         32),
             ("lock",  "🔒",        32),
             ("title", "Title",    280),
             ("bpm",   "BPM",       60),
             ("key",   "Key",      110),
             ("nrg",   "E",         40),
             ("dur",   "Dur",       60),
             ("score", "Score",     60)],
            sortable=False,
            height=16,
            on_double_click=self._toggle_lock_for_row,
        )
        self.table.pack(fill="both", expand=True, padx=30, pady=(0, 8))

        # Edit toolbar — move ↑ / ↓ / lock / unlock / regenerate
        edit_bar = ctk.CTkFrame(self, fg_color="transparent")
        edit_bar.pack(fill="x", padx=30, pady=(0, 8))

        for txt, fn, color in [
            ("↑",          self._move_up,    COLORS["bg_card"]),
            ("↓",          self._move_down,  COLORS["bg_card"]),
            ("Lock",       self._lock_sel,   COLORS["accent"]),
            ("Unlock",     self._unlock_sel, COLORS["bg_card"]),
        ]:
            ctk.CTkButton(
                edit_bar, text=txt, width=70, height=30,
                font=font(12),
                fg_color=color, hover_color=COLORS["accent"],
                text_color=COLORS["text"],
                command=fn).pack(side="left", padx=2)

        self.regen_btn = ctk.CTkButton(
            edit_bar, text="Regenerate (keep locked)", width=200, height=30,
            font=font(12, "bold"),
            fg_color=COLORS["accent2"], hover_color="#e0356f",
            text_color=COLORS["on_accent2"],
            command=self._regenerate, state="disabled")
        self.regen_btn.pack(side="right", padx=2)

        self.summary_label = ctk.CTkLabel(
            self, text="Pick a starting track and click Generate",
            font=font(12), text_color=COLORS["text_dim"])
        self.summary_label.pack(pady=(0, 16))

    def on_show(self):
        self.after_idle(lambda: threading.Thread(
            target=self._load_thread, daemon=True).start())

    def _load_thread(self):
        try:
            tracks = all_tracks(get_connection())
        except Exception as e:
            log_error("setlist.load failed", e)
            tracks = []
        self.after(0, lambda t=tracks: self._on_loaded(t))

    def _on_loaded(self, tracks: list[dict]):
        self._tracks = tracks
        if not tracks:
            self.start_menu.configure(values=["(no tracks yet)"])
            self.start_var.set("(no tracks yet)")
            return
        labels = [
            f"{(t['title'] or '?')[:40]}  ({t['camelot'] or '?'} / {(t['bpm'] or 0):.0f})"
            for t in tracks
        ]
        # CTkOptionMenu chokes on >1000 entries — cap and let the user
        # filter via search if they need a deep cut
        self.start_menu.configure(values=labels[:1000])
        self.start_var.set(labels[0])

    def _generate(self):
        if not self._tracks:
            return
        # Find selected track
        sel = self.start_var.get()
        idx = 0
        for i, t in enumerate(self._tracks):
            label = f"{(t['title'] or '?')[:40]}  ({t['camelot'] or '?'} / {(t['bpm'] or 0):.0f})"
            if label == sel:
                idx = i
                break

        try:
            length = int(self.length_var.get())
        except ValueError:
            length = 12
        length = max(2, min(length, 50))

        start = self._tracks[idx]
        self.gen_btn.configure(state="disabled", text="Generating…")
        self.summary_label.configure(text="Computing best path…",
                                      text_color=COLORS["accent"])
        threading.Thread(
            target=self._generate_thread,
            args=(start, length), daemon=True).start()

    def _generate_thread(self, start, length):
        try:
            setlist = build_setlist_auto(get_connection(), start, length)
        except Exception as e:
            log_error("build_setlist_auto failed", e)
            setlist = []
        self.after(0, lambda sl=setlist: self._on_generated(sl))

    def _on_generated(self, setlist):
        # Engine returns list[(track, score)]; promote to (track, score, locked)
        self._setlist = [(t, s, False) for (t, s) in setlist]
        self.gen_btn.configure(state="normal", text="Generate Setlist")
        active = "normal" if self._setlist else "disabled"
        self.export_btn.configure(state=active)
        self.regen_btn.configure(state=active)
        self.save_btn.configure(state=active)
        self._render()

    def _export(self):
        if not self._setlist:
            return
        from app.ui.export_dialog import ExportDialog
        tracks = [t for (t, _s, _l) in self._setlist]
        ExportDialog(self.winfo_toplevel(), tracks,
                      default_name="Ultimate DJ — Setlist")

    # ── Persisted setlists ─────────────────────────────────────

    def _save_dialog(self):
        """Prompt for a name, persist the current setlist to the DB."""
        if not self._setlist:
            return
        from tkinter import simpledialog
        name = simpledialog.askstring(
            "Save setlist",
            "Nom du setlist :",
            parent=self.winfo_toplevel())
        if not name:
            return
        name = name.strip()[:60]
        if not name:
            return
        try:
            existing = {s["name"] for s in list_setlists(get_connection())}
        except Exception:
            existing = set()
        if name in existing:
            if not confirm("Écraser ?",
                            f"Un setlist nommé « {name} » existe déjà. "
                            f"Écraser ?"):
                return
        try:
            save_setlist(get_connection(), name, self._setlist)
        except Exception as e:
            log_error("save_setlist failed", e)
            self.summary_label.configure(
                text=f"Erreur de sauvegarde : {e}",
                text_color=COLORS["error"])
            return
        self.summary_label.configure(
            text=f"Setlist « {name} » sauvegardé "
                 f"({len(self._setlist)} tracks).",
            text_color=COLORS["success"])

    def _load_dialog(self):
        """List saved setlists in a small picker. Click to load."""
        try:
            saved = list_setlists(get_connection())
        except Exception as e:
            log_error("list_setlists failed", e)
            saved = []
        if not saved:
            self.summary_label.configure(
                text="Aucun setlist sauvegardé pour l'instant.",
                text_color=COLORS["text_dim"])
            return

        win = ctk.CTkToplevel(self)
        win.title("Charger un setlist")
        win.geometry("520x420")
        win.configure(fg_color=COLORS["bg_dark"])
        win.transient(self.winfo_toplevel())
        try:
            win.grab_set()
        except Exception:
            pass

        ctk.CTkLabel(
            win, text="Setlists sauvegardés",
            font=font(16, "bold"),
            text_color=COLORS["text"]
        ).pack(anchor="w", padx=20, pady=(16, 8))

        scroll = ctk.CTkScrollableFrame(win, fg_color="transparent")
        scroll.pack(fill="both", expand=True, padx=20, pady=(0, 8))

        from datetime import datetime
        for s in saved:
            row = ctk.CTkFrame(scroll, fg_color=COLORS["bg_card"],
                                corner_radius=8)
            row.pack(fill="x", pady=3)

            info = ctk.CTkFrame(row, fg_color="transparent")
            info.pack(side="left", fill="x", expand=True, padx=10, pady=8)
            ctk.CTkLabel(
                info, text=s["name"], font=font(13, "bold"),
                text_color=COLORS["accent"], anchor="w"
            ).pack(anchor="w")
            updated = datetime.fromtimestamp(
                s["updated_at"] or 0).strftime("%Y-%m-%d %H:%M")
            ctk.CTkLabel(
                info,
                text=f"{s['count']} tracks · maj {updated}",
                font=font(10), text_color=COLORS["text_dim"], anchor="w"
            ).pack(anchor="w")

            ctk.CTkButton(
                row, text="Charger", width=80, height=28,
                font=font(11),
                fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
                text_color=COLORS["bg_dark"],
                command=lambda n=s["name"], w=win:
                    self._do_load(n, w),
            ).pack(side="right", padx=4, pady=8)
            ctk.CTkButton(
                row, text="✗", width=32, height=28,
                font=font(11),
                fg_color="transparent", hover_color=COLORS["error"],
                text_color=COLORS["text_dim"],
                command=lambda n=s["name"], w=win:
                    self._do_delete(n, w),
            ).pack(side="right", padx=2, pady=8)

        ctk.CTkButton(
            win, text="Fermer", width=100, height=32,
            fg_color=COLORS["bg_card"], hover_color=COLORS["bg_input"],
            text_color=COLORS["text"],
            command=win.destroy
        ).pack(pady=(0, 16))

    def _do_load(self, name: str, win):
        try:
            slots = load_setlist(get_connection(), name)
        except Exception as e:
            log_error("load_setlist failed", e)
            return
        if not slots:
            self.summary_label.configure(
                text=f"« {name} » est vide ou ses tracks ont été supprimées.",
                text_color=COLORS["warning"])
            win.destroy()
            return
        self._setlist = slots
        active = "normal" if self._setlist else "disabled"
        self.export_btn.configure(state=active)
        self.regen_btn.configure(state=active)
        self.save_btn.configure(state=active)
        self._render()
        self.summary_label.configure(
            text=f"Setlist « {name} » chargé ({len(slots)} tracks).",
            text_color=COLORS["success"])
        win.destroy()

    def _do_delete(self, name: str, win):
        if not confirm(
                "Supprimer ?",
                f"Supprimer définitivement le setlist « {name} » ?"):
            return
        try:
            delete_setlist(get_connection(), name)
        except Exception as e:
            log_error("delete_setlist failed", e)
            return
        win.destroy()
        # Re-open with the refreshed list
        self._load_dialog()

    # ── Editor actions ─────────────────────────────────────────

    def _selected_index(self) -> int | None:
        """Return the 0-based index of the currently selected slot."""
        sel = self.table.selected_row()
        if not sel:
            return None
        try:
            return int(sel[0]) - 1
        except (ValueError, IndexError):
            return None

    def _move_up(self):
        i = self._selected_index()
        if i is None or i <= 0:
            return
        self._setlist[i - 1], self._setlist[i] = (
            self._setlist[i], self._setlist[i - 1])
        self._recompute_scores()
        self._render(select_index=i - 1)

    def _move_down(self):
        i = self._selected_index()
        if i is None or i >= len(self._setlist) - 1:
            return
        self._setlist[i + 1], self._setlist[i] = (
            self._setlist[i], self._setlist[i + 1])
        self._recompute_scores()
        self._render(select_index=i + 1)

    def _lock_sel(self):
        i = self._selected_index()
        if i is None:
            return
        track, score, _ = self._setlist[i]
        self._setlist[i] = (track, score, True)
        self._render(select_index=i)

    def _unlock_sel(self):
        i = self._selected_index()
        if i is None:
            return
        track, score, _ = self._setlist[i]
        self._setlist[i] = (track, score, False)
        self._render(select_index=i)

    def _toggle_lock_for_row(self, _row: tuple):
        i = self._selected_index()
        if i is None:
            return
        track, score, locked = self._setlist[i]
        self._setlist[i] = (track, score, not locked)
        self._render(select_index=i)

    def _recompute_scores(self):
        """Redo the transition_score chain after a manual reorder."""
        if not self._setlist:
            return
        new = [(self._setlist[0][0], 100.0, self._setlist[0][2])]
        for j in range(1, len(self._setlist)):
            prev_track = new[-1][0]
            track, _old_score, locked = self._setlist[j]
            score = transition_score(prev_track, track)
            new.append((track, score, locked))
        self._setlist = new

    def _regenerate(self):
        """Re-pick unlocked slots, preserving locked positions in place."""
        if not self._setlist:
            return
        self.regen_btn.configure(state="disabled", text="…")
        threading.Thread(
            target=self._regenerate_thread, daemon=True).start()

    def _regenerate_thread(self):
        try:
            conn = get_connection()
            pool = all_tracks(conn)
            # Used set starts with the locked tracks so we don't re-pick them
            used = {t["path"] for (t, _s, locked) in self._setlist if locked}
            new_list: list[tuple[dict, float, bool]] = []
            prev_track = None

            for slot in self._setlist:
                track, _score, locked = slot
                if locked:
                    if prev_track is None:
                        # First slot; locked — keep as-is
                        new_list.append((track, 100.0, True))
                    else:
                        new_list.append(
                            (track, transition_score(prev_track, track), True))
                    prev_track = track
                    continue

                # Find best unlocked candidate not in used
                best, best_score = None, -1.0
                for cand in pool:
                    if cand["path"] in used:
                        continue
                    if prev_track is None:
                        s = 100.0
                    else:
                        s = transition_score(prev_track, cand)
                    if s > best_score:
                        best, best_score = cand, s
                if best is None:
                    # No candidate left — keep the original
                    new_list.append((track, 0.0, False))
                else:
                    new_list.append((best, best_score, False))
                    used.add(best["path"])
                    prev_track = best
        except Exception as e:
            log_error("setlist regenerate failed", e)
            new_list = self._setlist

        self.after(0, lambda nl=new_list: self._on_regenerated(nl))

    def _on_regenerated(self, new_list):
        self._setlist = new_list
        self.regen_btn.configure(state="normal",
                                  text="Regenerate (keep locked)")
        self._render()

    def _render(self, *, select_index: int | None = None):
        if not self._setlist:
            self.table.clear()
            self.summary_label.configure(text="No setlist generated.",
                                          text_color=COLORS["text_dim"])
            return

        rows = []
        tags = []
        total_dur = 0
        for i, (track, score, locked) in enumerate(self._setlist, 1):
            dur_s = int(track.get("duration", 0) or 0)
            total_dur += dur_s
            rows.append((
                f"{i}",
                "🔒" if locked else "",
                (track["title"] or "?")[:60],
                f"{(track['bpm'] or 0):.0f}",
                f"{track['key'] or '?'} ({track['camelot'] or '?'})",
                f"{(track['energy'] or 0):.1f}",
                f"{dur_s // 60}:{dur_s % 60:02d}",
                "—" if i == 1 else f"{score:.0f}",
            ))
            if i == 1 or score >= 80:
                tags.append(("ok",))
            elif score >= 50:
                tags.append(("warn",))
            else:
                tags.append(("err",))
        self.table.set_rows(rows, row_tags=tags)

        # Restore selection if requested
        if select_index is not None:
            iids = self.table.tree.get_children("")
            if 0 <= select_index < len(iids):
                self.table.tree.selection_set(iids[select_index])
                self.table.tree.see(iids[select_index])

        total_min = total_dur // 60
        locked_n = sum(1 for (_, _, l) in self._setlist if l)
        suffix = f"  ·  {locked_n} verrouillé(s)" if locked_n else ""
        self.summary_label.configure(
            text=f"Total: {len(self._setlist)} tracks  |  "
                 f"~{total_min} min{suffix}",
            text_color=COLORS["accent"])
