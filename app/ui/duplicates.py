"""
Duplicate manager — find duplicate tracks and let the user choose
which copy to keep / delete (per group, per folder).

Performance:
    - find_duplicates runs in a worker thread (it's O(N) over the whole
      library). The modal opens instantly with a loading state.
    - Track list is a FastList (ttk.Treeview) instead of one CTk row
      per duplicate. The previous CTkRadioButton-per-row approach
      created thousands of widgets and froze the UI on libraries with
      many duplicate groups.
    - Selection: click a row to mark it as "À garder" within its group;
      siblings auto-flip to "À supprimer". One click = one group resolved.
"""
from __future__ import annotations

import os
import threading
from pathlib import Path

import customtkinter as ctk

from app.config import COLORS
from app.engine.library import get_connection, find_duplicates, delete_track
from app.logger import log_error, log_info
from app.ui import helpers
from app.ui.fastlist import FastList
from app.ui.helpers import font


# Visual markers used in the "État" column
_KEEP_SYM = "●  À garder"
_DROP_SYM = "✗  À supprimer"


class DuplicatesWindow(ctk.CTkToplevel):

    def __init__(self, parent):
        super().__init__(parent)
        self.title("Gestion des doublons")
        self.geometry("960x640")
        self.configure(fg_color=COLORS["bg_dark"])
        self.transient(parent)
        try:
            self.grab_set()
        except Exception:
            pass

        # Each row's iid → (group_idx, path). Used by click handler to
        # promote a row to "keep" + demote its siblings.
        self._row_meta: dict[str, tuple[int, str]] = {}
        # Current "keep" path per group (key = group_idx)
        self._keep: dict[int, str] = {}
        self._groups: list[list[dict]] = []

        self._build()
        # Defer the heavy scan to a worker so the modal pops instantly
        self.after(50, self._start_scan)

    # ── UI ─────────────────────────────────────────────────────

    def _build(self):
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=20, pady=(20, 6))
        ctk.CTkLabel(
            top, text="Doublons détectés",
            font=font(22, "bold"),
            text_color=COLORS["text"]
        ).pack(side="left")

        self.summary = ctk.CTkLabel(
            top, text="Recherche en cours…",
            font=font(12),
            text_color=COLORS["accent"])
        self.summary.pack(side="left", padx=12)

        ctk.CTkButton(
            top, text="Auto (garder + lourd)", width=180, height=30,
            font=font(11),
            fg_color=COLORS["bg_input"], hover_color=COLORS["accent"],
            text_color=COLORS["text"],
            command=self._auto_pick,
        ).pack(side="right", padx=4)

        ctk.CTkLabel(
            self,
            text="Clique sur une ligne pour la marquer « À garder » — les "
                 "autres lignes du même groupe deviennent « À supprimer ».",
            font=font(11),
            text_color=COLORS["text_dim"],
            wraplength=920, justify="left"
        ).pack(anchor="w", padx=20, pady=(0, 8))

        # Table — Treeview backed FastList. Click bound to row → toggle.
        # Folder gets a generous initial width AND stretch=True (since
        # width >= 150) so it absorbs extra horizontal space — that's
        # where the long OneDrive paths usually live.
        self.table = FastList(
            self,
            [("group",  "Groupe",     50),
             ("status", "État",      120),
             ("title",  "Titre",     280),
             ("file",   "Fichier",   280),
             ("folder", "Dossier",   500)],
            sortable=False,
            height=18,
        )
        self.table.pack(fill="both", expand=True, padx=20, pady=(0, 4))
        # Single click = promote this row to "keep" within its group
        self.table.tree.bind("<Button-1>", self._on_row_click, add="+")
        # Selection → show the full path in the status bar below
        self.table.tree.bind("<<TreeviewSelect>>",
                              self._on_select_show_path, add="+")

        # Status bar showing the full path of the selected row. Treeview
        # truncates anything wider than the column visually; this is the
        # "click to read the full path" escape hatch for very long paths.
        self.path_status = ctk.CTkLabel(
            self, text="(clique une ligne pour voir le chemin complet)",
            font=font(11), text_color=COLORS["text_dim"],
            anchor="w", justify="left", wraplength=900)
        self.path_status.pack(fill="x", padx=20, pady=(0, 6))

        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", padx=20, pady=(0, 16))
        ctk.CTkButton(
            bar, text="Annuler", width=100, height=36,
            fg_color=COLORS["bg_card"], hover_color=COLORS["bg_input"],
            text_color=COLORS["text"],
            command=self.destroy,
        ).pack(side="right", padx=4)
        self.delete_btn = ctk.CTkButton(
            bar, text="Appliquer la suppression", width=200, height=36,
            font=font(13, "bold"),
            fg_color=COLORS["error"], hover_color="#cc3333",
            text_color="white",
            state="disabled",
            command=self._apply_deletion,
        )
        self.delete_btn.pack(side="right", padx=4)

    # ── Scan (off-thread) ──────────────────────────────────────

    def _start_scan(self):
        self.summary.configure(text="Recherche en cours…",
                                text_color=COLORS["accent"])
        threading.Thread(target=self._scan_thread, daemon=True).start()

    def _scan_thread(self):
        try:
            # Use a connection on THIS thread (engine uses thread-local)
            groups = find_duplicates(get_connection())
        except Exception as e:
            log_error("find_duplicates failed", e)
            groups = []
        self.after(0, lambda g=groups: self._on_scan_done(g))

    def _on_scan_done(self, groups: list[list[dict]]):
        self._groups = groups
        if not groups:
            self.summary.configure(
                text="0 doublon — la bibliothèque est propre 🎉",
                text_color=COLORS["success"])
            self.table.clear()
            self.delete_btn.configure(state="disabled")
            return

        total_dups = sum(len(g) - 1 for g in groups)
        self.summary.configure(
            text=f"{len(groups)} groupes · {total_dups} fichiers en trop",
            text_color=COLORS["warning"])
        self._render_table()
        self.delete_btn.configure(state="normal")

    # ── Table rendering ────────────────────────────────────────

    def _render_table(self):
        rows = []
        tags = []
        self._row_meta.clear()

        # Default keep = first track of each group (heuristic: usually the
        # higher-rated / earlier-added one in find_duplicates' ordering)
        for gi, group in enumerate(self._groups, 1):
            self._keep.setdefault(gi, group[0]["path"])

        for gi, group in enumerate(self._groups, 1):
            keep_path = self._keep[gi]
            for t in group:
                p = Path(t["path"])
                is_keep = (t["path"] == keep_path)
                # Don't truncate file name / parent — Treeview clips
                # visually inside the column anyway. The user can:
                #   1) drag the column header to widen it, or
                #   2) click the row to read the full path in the
                #      status bar below the table.
                rows.append((
                    str(gi),
                    _KEEP_SYM if is_keep else _DROP_SYM,
                    t["title"] or "?",
                    p.name,
                    str(p.parent),
                ))
                tags.append(("ok",) if is_keep else ("err",))

        self.table.set_rows(rows, row_tags=tags)
        # Map each rendered iid → its group + path for the click handler
        iids = list(self.table.tree.get_children(""))
        idx = 0
        for gi, group in enumerate(self._groups, 1):
            for t in group:
                if idx < len(iids):
                    self._row_meta[iids[idx]] = (gi, t["path"])
                idx += 1

    def _on_row_click(self, event):
        """Click → set this row as the keeper of its group, repaint."""
        iid = self.table.tree.identify_row(event.y)
        if not iid or iid not in self._row_meta:
            return
        gi, path = self._row_meta[iid]
        if self._keep.get(gi) == path:
            return    # already the keeper, no-op
        self._keep[gi] = path
        # Re-render only the rows of this group instead of the whole list
        self._refresh_group_rows(gi)

    def _on_select_show_path(self, _event=None):
        """Surface the full path of the selected row in the status bar.

        Treeview clips column text visually; this gives the user a
        readable, full-width view of long OneDrive paths without
        forcing a wide Dossier column."""
        try:
            sel = self.table.tree.selection()
            if not sel:
                return
            iid = sel[0]
            meta = self._row_meta.get(iid)
            if not meta:
                return
            _gi, path = meta
            self.path_status.configure(
                text=path, text_color=COLORS["text"])
        except Exception:
            pass

    def _refresh_group_rows(self, gi: int):
        """Update status + tag for every row that belongs to group `gi`."""
        keep = self._keep[gi]
        for iid, (g, path) in self._row_meta.items():
            if g != gi:
                continue
            is_keep = (path == keep)
            cur_values = list(self.table.tree.item(iid, "values"))
            cur_values[1] = _KEEP_SYM if is_keep else _DROP_SYM
            # FastList keeps stripe tag in self._row_data, so update via
            # our public API — preserves zebra pattern correctly
            self.table.update_row(iid, tuple(cur_values),
                                   tags=("ok",) if is_keep else ("err",))

    # ── Auto-resolve ───────────────────────────────────────────

    def _auto_pick(self):
        """Heuristic: keep the file with the largest size on disk
        (usually the highest-quality version)."""
        if not self._groups:
            return
        for gi, group in enumerate(self._groups, 1):
            best_path = group[0]["path"]
            best_size = -1
            for t in group:
                try:
                    sz = os.path.getsize(t["path"])
                except OSError:
                    sz = 0
                if sz > best_size:
                    best_size = sz
                    best_path = t["path"]
            self._keep[gi] = best_path
        self._render_table()

    # ── Apply ─────────────────────────────────────────────────

    def _apply_deletion(self):
        to_delete: list[str] = []
        for gi, group in enumerate(self._groups, 1):
            keep = self._keep.get(gi, group[0]["path"])
            for t in group:
                if t["path"] != keep:
                    to_delete.append(t["path"])

        if not to_delete:
            helpers.info("Rien à supprimer",
                         "Aucun fichier marqué pour suppression.")
            return

        if not helpers.confirm(
                "Confirmer la suppression",
                f"{len(to_delete)} fichier(s) vont être supprimés du disque "
                "ET de la bibliothèque.\n\nCette action est irréversible. "
                "Continuer ?"):
            return

        # Run the deletion in a thread so the UI doesn't lock up while
        # we hit the disk + DB N times.
        self.delete_btn.configure(state="disabled", text="Suppression…")
        threading.Thread(
            target=self._delete_thread, args=(to_delete,),
            daemon=True).start()

    def _delete_thread(self, paths: list[str]):
        conn = get_connection()
        ok, fail = 0, 0
        for path in paths:
            try:
                if os.path.isfile(path):
                    os.remove(path)
                delete_track(conn, path)
                ok += 1
            except Exception as e:
                fail += 1
                log_error(f"duplicate delete failed for {path}", e)
        log_info(f"duplicates: {ok} deleted, {fail} failed")
        self.after(0, lambda o=ok, f=fail: self._after_delete(o, f))

    def _after_delete(self, ok: int, fail: int):
        msg = f"{ok} fichier(s) supprimé(s)."
        if fail:
            msg += f"\n{fail} échec(s) — vérifie les permissions."
        helpers.info("Doublons nettoyés", msg)
        # Re-scan to refresh the list
        self._start_scan()
