"""
Camelot Wheel reference page — interactive visual wheel.
"""
from __future__ import annotations

import math
import customtkinter as ctk

from app.config import COLORS, CAMELOT_KEYS
from app.engine.library import compatible_camelot


# Camelot wheel layout: number -> (outer=major B, inner=minor A)
_WHEEL = [
    (1,  "G# minor", "B major"),
    (2,  "D# minor", "F# major"),
    (3,  "Bb minor", "Db major"),
    (4,  "F minor",  "Ab major"),
    (5,  "C minor",  "Eb major"),
    (6,  "G minor",  "Bb major"),
    (7,  "D minor",  "F major"),
    (8,  "A minor",  "C major"),
    (9,  "E minor",  "G major"),
    (10, "B minor",  "D major"),
    (11, "F# minor", "A major"),
    (12, "C# minor", "E major"),
]

_COLORS_WHEEL = [
    "#FF6B6B", "#FF8E72", "#FFB347", "#FFD700",
    "#ADFF2F", "#00E676", "#00BCD4", "#00D4FF",
    "#448AFF", "#7C4DFF", "#E040FB", "#FF4081",
]


class CamelotPage(ctk.CTkFrame):
    def __init__(self, parent):
        super().__init__(parent, fg_color=COLORS["bg_dark"])
        self._selected_code: str = ""
        self._build_ui()

    def _build_ui(self):
        ctk.CTkLabel(
            self, text="Camelot Wheel",
            font=ctk.CTkFont(size=26, weight="bold"),
            text_color=COLORS["text"],
        ).pack(anchor="w", padx=30, pady=(24, 4))
        ctk.CTkLabel(
            self, text="Click any key to see compatible mixing options",
            font=ctk.CTkFont(size=13),
            text_color=COLORS["text_dim"],
        ).pack(anchor="w", padx=30, pady=(0, 12))

        # Main area: wheel + info panel
        main = ctk.CTkFrame(self, fg_color="transparent")
        main.pack(fill="both", expand=True, padx=30, pady=(0, 16))

        # Canvas for the wheel
        self.canvas = ctk.CTkCanvas(
            main, bg=COLORS["bg_dark"], highlightthickness=0,
            width=460, height=460)
        self.canvas.pack(side="left", padx=(0, 20))

        # Info panel
        self.info_frame = ctk.CTkFrame(main, fg_color=COLORS["bg_card"], corner_radius=12,
                                        width=300)
        self.info_frame.pack(side="right", fill="both", expand=True)

        self.info_title = ctk.CTkLabel(
            self.info_frame, text="Select a key",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color=COLORS["accent"])
        self.info_title.pack(pady=(20, 8), padx=16)

        self.info_detail = ctk.CTkLabel(
            self.info_frame, text="",
            font=ctk.CTkFont(size=13), text_color=COLORS["text"],
            justify="left", wraplength=260)
        self.info_detail.pack(pady=4, padx=16)

        self.compat_frame = ctk.CTkFrame(self.info_frame, fg_color="transparent")
        self.compat_frame.pack(fill="both", expand=True, padx=16, pady=(8, 16))

        # Quick reference table
        ref_frame = ctk.CTkFrame(self.info_frame, fg_color=COLORS["bg_input"], corner_radius=8)
        ref_frame.pack(fill="x", padx=12, pady=(0, 12))
        ctk.CTkLabel(ref_frame, text="Mixing rules:",
                     font=ctk.CTkFont(size=11, weight="bold"),
                     text_color=COLORS["accent"]).pack(anchor="w", padx=8, pady=(6, 2))
        for rule in [
            "Same number = perfect match",
            "+1 / -1 = energy up/down",
            "A <-> B = mood switch (minor/major)",
        ]:
            ctk.CTkLabel(ref_frame, text=f"  {rule}",
                         font=ctk.CTkFont(size=10),
                         text_color=COLORS["text_dim"]).pack(anchor="w", padx=8)
        ctk.CTkLabel(ref_frame, text="").pack(pady=2)  # spacer

        self._draw_wheel()

    def _draw_wheel(self):
        self.canvas.delete("all")
        cx, cy = 230, 230
        r_outer = 200
        r_mid = 140
        r_inner = 80

        self._segments = []  # (code, bbox_or_arc_info)

        for i, (num, minor_name, major_name) in enumerate(_WHEEL):
            angle_start = (i * 30) - 90 - 15  # start angle for this slice
            color = _COLORS_WHEEL[i]

            # Outer ring (B = major)
            code_b = f"{num}B"
            oid = self.canvas.create_arc(
                cx - r_outer, cy - r_outer, cx + r_outer, cy + r_outer,
                start=angle_start, extent=30, fill=color, outline=COLORS["bg_dark"],
                width=2, style="pieslice")
            self.canvas.tag_bind(oid, "<Button-1>",
                                  lambda e, c=code_b: self._on_click(c))

            # Inner ring (A = minor) — slightly darker
            dark = self._darken(color, 0.6)
            code_a = f"{num}A"
            iid = self.canvas.create_arc(
                cx - r_mid, cy - r_mid, cx + r_mid, cy + r_mid,
                start=angle_start, extent=30, fill=dark, outline=COLORS["bg_dark"],
                width=2, style="pieslice")
            self.canvas.tag_bind(iid, "<Button-1>",
                                  lambda e, c=code_a: self._on_click(c))

            # Labels
            mid_angle = math.radians(angle_start + 15)
            # Outer label
            lx = cx + (r_outer + r_mid) / 2 * math.cos(-mid_angle)
            ly = cy - (r_outer + r_mid) / 2 * math.sin(-mid_angle)
            self.canvas.create_text(lx, ly, text=code_b,
                                     font=("Segoe UI", 9, "bold"),
                                     fill=COLORS["bg_dark"])
            # Inner label
            lx2 = cx + (r_mid + r_inner) / 2 * math.cos(-mid_angle)
            ly2 = cy - (r_mid + r_inner) / 2 * math.sin(-mid_angle)
            self.canvas.create_text(lx2, ly2, text=code_a,
                                     font=("Segoe UI", 9, "bold"),
                                     fill="#eee")

        # Center circle
        self.canvas.create_oval(
            cx - r_inner, cy - r_inner, cx + r_inner, cy + r_inner,
            fill=COLORS["bg_dark"], outline=COLORS["accent"], width=2)
        self.canvas.create_text(cx, cy - 10, text="CAMELOT",
                                 font=("Segoe UI", 12, "bold"),
                                 fill=COLORS["accent"])
        self.canvas.create_text(cx, cy + 10, text="WHEEL",
                                 font=("Segoe UI", 10),
                                 fill=COLORS["text_dim"])

    def _darken(self, hex_color: str, factor: float) -> str:
        r = int(hex_color[1:3], 16)
        g = int(hex_color[3:5], 16)
        b = int(hex_color[5:7], 16)
        return f"#{int(r*factor):02x}{int(g*factor):02x}{int(b*factor):02x}"

    def _on_click(self, code: str):
        self._selected_code = code
        key_name = CAMELOT_KEYS.get(code, "Unknown")
        compat = compatible_camelot(code)

        self.info_title.configure(text=f"{code}  —  {key_name}")
        self.info_detail.configure(
            text=f"Compatible keys for harmonic mixing:")

        for w in self.compat_frame.winfo_children():
            w.destroy()

        for c in compat:
            cname = CAMELOT_KEYS.get(c, "?")
            if c == code:
                relation = "Same key"
            elif c[-1] != code[-1]:
                relation = "Mood switch"
            elif int(c[:-1]) == (int(code[:-1]) % 12) + 1:
                relation = "Energy up"
            else:
                relation = "Energy down"

            row = ctk.CTkFrame(self.compat_frame, fg_color=COLORS["bg_input"],
                                corner_radius=8)
            row.pack(fill="x", pady=3)
            ctk.CTkLabel(row, text=c, width=50,
                         font=ctk.CTkFont(size=14, weight="bold"),
                         text_color=COLORS["accent"]).pack(side="left", padx=(10, 6), pady=6)
            ctk.CTkLabel(row, text=cname,
                         font=ctk.CTkFont(size=12),
                         text_color=COLORS["text"]).pack(side="left", padx=4)
            ctk.CTkLabel(row, text=relation,
                         font=ctk.CTkFont(size=11),
                         text_color=COLORS["text_dim"]).pack(side="right", padx=10)
