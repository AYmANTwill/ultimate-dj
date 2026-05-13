"""
Small modal: pick an export format and a destination path.

Used by Library ("Export library") and Setlist ("Export setlist") to
write a playlist out to M3U8 / Rekordbox XML / Serato crate.
"""
from __future__ import annotations

from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk

from app.config import COLORS
from app.engine import export
from app.ui import helpers
from app.ui.helpers import font


_FORMATS = [
    ("M3U8 (universal)",      "m3u8",
     [("M3U8 playlist", "*.m3u8"), ("All", "*.*")]),
    ("Rekordbox XML",         "rekordbox",
     [("Rekordbox XML", "*.xml"), ("All", "*.*")]),
    ("Serato crate",          "serato",
     [("Serato crate", "*.crate"), ("All", "*.*")]),
]


class ExportDialog(ctk.CTkToplevel):
    def __init__(self, parent, tracks: list[dict], *,
                 default_name: str = "Ultimate DJ"):
        super().__init__(parent)
        self.title("Exporter")
        self.geometry("420x260")
        self.configure(fg_color=COLORS["bg_dark"])
        self.transient(parent)
        try:
            self.grab_set()
        except Exception:
            pass

        self._tracks = tracks
        self._default_name = default_name
        self._fmt_var = ctk.StringVar(value="m3u8")
        self._build()

    def _build(self):
        ctk.CTkLabel(
            self, text="Exporter la sélection",
            font=font(16, "bold"),
            text_color=COLORS["text"]
        ).pack(anchor="w", padx=20, pady=(20, 4))
        ctk.CTkLabel(
            self,
            text=f"{len(self._tracks)} morceau(x) à exporter.",
            font=font(11), text_color=COLORS["text_dim"]
        ).pack(anchor="w", padx=20, pady=(0, 12))

        for label, key, _filters in _FORMATS:
            ctk.CTkRadioButton(
                self, text=label, variable=self._fmt_var, value=key,
                font=font(12), text_color=COLORS["text"],
                fg_color=COLORS["accent"]
            ).pack(anchor="w", padx=24, pady=4)

        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", padx=20, pady=(20, 16), side="bottom")
        ctk.CTkButton(
            bar, text="Annuler", width=100, height=34,
            fg_color=COLORS["bg_card"], hover_color=COLORS["bg_input"],
            text_color=COLORS["text"],
            command=self.destroy
        ).pack(side="right", padx=4)
        ctk.CTkButton(
            bar, text="Exporter…", width=140, height=34,
            font=font(13, "bold"),
            fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
            text_color=COLORS["bg_dark"],
            command=self._do_export
        ).pack(side="right", padx=4)

    def _do_export(self):
        if not self._tracks:
            helpers.warn("Rien à exporter", "La liste est vide.")
            return

        fmt = self._fmt_var.get()
        ext_map = {"m3u8": ".m3u8", "rekordbox": ".xml", "serato": ".crate"}
        defaults = {"m3u8": "playlist.m3u8",
                     "rekordbox": "rekordbox_export.xml",
                     "serato": f"{self._default_name}.crate"}
        ext = ext_map[fmt]
        # Find filters for this format
        filters = next(f[2] for f in _FORMATS if f[1] == fmt)

        path = filedialog.asksaveasfilename(
            parent=self,
            title="Choisir le fichier de sortie",
            defaultextension=ext,
            initialfile=defaults[fmt],
            filetypes=filters)
        if not path:
            return

        try:
            if fmt == "m3u8":
                export.export_m3u8(self._tracks, path,
                                    playlist_name=self._default_name)
            elif fmt == "rekordbox":
                export.export_rekordbox_xml(self._tracks, path,
                                             playlist_name=self._default_name)
            elif fmt == "serato":
                export.export_serato_crate(self._tracks, path)
        except Exception as e:
            helpers.error("Erreur d'export",
                           f"Impossible d'écrire le fichier.",
                           detail=str(e))
            return

        helpers.info(
            "Export terminé",
            f"{len(self._tracks)} morceau(x) écrit(s) dans :\n{Path(path).name}")
        self.destroy()
