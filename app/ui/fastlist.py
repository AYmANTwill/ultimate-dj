"""
FastList — a themed wrapper around ttk.Treeview for tabular data.

Why this exists:
    ctk.CTkScrollableFrame + N×CTkFrames is *gorgeous* but every row
    creates ~6 widgets each backed by a Canvas (rounded corners). Past a
    few hundred rows the UI thread chokes. ttk.Treeview is the native Tk
    table widget — written in C, virtualised (only visible rows are
    painted), and renders 10 000 rows in well under 100ms.

Features:
    - Themed to match the active app palette (dark by default)
    - Click column header to sort (toggle asc/desc)
    - Tag system for per-row colouring (success/warning/error highlights)
    - Mouse + keyboard selection, multi-select, double-click callback
    - Row striping for readability
    - clear()/set_rows()/append() API kept tiny so callers stay simple

Usage:
    cols = [("title", "Title", 280), ("bpm", "BPM", 60), ...]
    self.list = FastList(parent, cols, on_double_click=self._open)
    self.list.pack(fill="both", expand=True)
    self.list.set_rows(rows)  # rows = list[tuple] in column order
"""
from __future__ import annotations

from tkinter import ttk
import tkinter as tk
from typing import Callable, Iterable

import customtkinter as ctk

from app.config import COLORS


# Track styles already configured per Tk root so we don't redo work
_styled_roots: set[int] = set()


def _mix(hex_a: str, hex_b: str, t: float) -> str:
    """Linear blend between two #RRGGBB strings. t=0 → A, t=1 → B."""
    a = hex_a.lstrip("#"); b = hex_b.lstrip("#")
    if len(a) != 6 or len(b) != 6:
        return hex_a
    ar, ag, ab = int(a[0:2], 16), int(a[2:4], 16), int(a[4:6], 16)
    br, bg, bb = int(b[0:2], 16), int(b[2:4], 16), int(b[4:6], 16)
    r = int(ar + (br - ar) * t)
    g = int(ag + (bg - ag) * t)
    bl = int(ab + (bb - ab) * t)
    return f"#{r:02x}{g:02x}{bl:02x}"


def _ensure_style(widget: tk.Widget) -> str:
    """Configure a ttk style for FastList trees, once per Tk root.

    Returns the style name to use (`"FastList.Treeview"`).
    """
    root = widget.winfo_toplevel()
    rid = id(root)

    style = ttk.Style(root)
    # Use 'clam' as base — it accepts colour overrides reliably across
    # Windows/macOS/Linux. The default 'vista'/'aqua' themes ignore most
    # background settings.
    try:
        if "clam" in style.theme_names():
            style.theme_use("clam")
    except tk.TclError:
        pass

    bg_card = COLORS.get("bg_card", "#1a1a35")
    bg_input = COLORS.get("bg_input", "#22224a")
    text = COLORS.get("text", "#e0e0e0")
    text_dim = COLORS.get("text_dim", "#777")
    accent = COLORS.get("accent", "#00d4ff")
    bg_dark = COLORS.get("bg_dark", "#0f0f1a")

    # Slightly bigger font + taller rows = much more readable than the
    # cramped default. Matches the density of pro DJ tools (Rekordbox /
    # Engine) where every row needs to be glance-readable in a club.
    style.configure(
        "FastList.Treeview",
        background=bg_card,
        fieldbackground=bg_card,
        foreground=text,
        bordercolor=bg_card,
        borderwidth=0,
        rowheight=30,                # was 26
        font=("Segoe UI", 11),       # was 10
    )
    style.map(
        "FastList.Treeview",
        background=[("selected", accent)],
        foreground=[("selected", bg_dark)],
    )
    # Headings: bigger + more padding so columns are obvious
    style.configure(
        "FastList.Treeview.Heading",
        background=bg_input,
        foreground=accent,
        font=("Segoe UI", 11, "bold"),
        relief="flat",
        borderwidth=0,
        padding=(8, 6),
    )
    style.map(
        "FastList.Treeview.Heading",
        background=[("active", bg_card)],
    )
    # Force re-apply on subsequent calls so theme switches take effect
    _styled_roots.add(rid)
    return "FastList.Treeview"


class FastList(ctk.CTkFrame):
    """High-performance themed table backed by ttk.Treeview."""

    def __init__(
        self,
        parent,
        columns: list[tuple[str, str, int]],
        *,
        on_double_click: Callable[[tuple], None] | None = None,
        on_select: Callable[[list[tuple]], None] | None = None,
        sortable: bool = True,
        height: int = 12,
    ):
        super().__init__(parent, fg_color=COLORS["bg_card"], corner_radius=8)

        self._columns = columns
        self._on_double_click = on_double_click
        self._on_select = on_select
        self._sortable = sortable
        # Map iid -> original tuple so callbacks get the row data back
        self._row_data: dict[str, tuple] = {}
        self._sort_state: dict[str, bool] = {}  # col_id -> ascending?

        style_name = _ensure_style(self)
        col_ids = [c[0] for c in columns]

        container = tk.Frame(self, bg=COLORS["bg_card"], bd=0)
        container.pack(fill="both", expand=True, padx=2, pady=2)

        self.tree = ttk.Treeview(
            container,
            columns=col_ids,
            show="headings",
            style=style_name,
            height=height,
            selectmode="extended",
        )
        # Stretch policy: any column wider than 150 pixels (i.e. the
        # "main content" columns like Title or Genre) grows to absorb
        # extra horizontal space when the table is bigger than the sum
        # of declared widths. Narrow indicator columns (BPM / Cam / E /
        # Score / # / 🔒) keep their tight width so the layout stays
        # punchy and the table never has trailing dead space.
        for col_id, label, width in columns:
            self.tree.heading(
                col_id, text=label,
                command=(lambda c=col_id: self._sort_by(c)) if sortable else "")
            anchor = "e" if width <= 80 else "w"
            stretch = width >= 150
            self.tree.column(col_id, width=width, anchor=anchor,
                              stretch=stretch, minwidth=max(40, width // 3))

        # Scrollbar — vertical only (horizontal is rare for music libs)
        sb = ttk.Scrollbar(container, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        # Row striping tags — applied on insert
        # We mix a 6% lift between odd/even for subtle but readable
        # zebra. Pure bg_card vs bg_input was too high contrast and
        # made the table feel busy.
        bg_card = COLORS.get("bg_card", "#1a1a35")
        bg_alt = _mix(bg_card, "#ffffff", 0.04)
        self.tree.tag_configure("odd",  background=bg_card)
        self.tree.tag_configure("even", background=bg_alt)
        # Status tags for caller use
        self.tree.tag_configure("ok",   foreground=COLORS.get("success", "#00e676"))
        self.tree.tag_configure("warn", foreground=COLORS.get("warning", "#ffab00"))
        self.tree.tag_configure("err",  foreground=COLORS.get("error",   "#ff5252"))

        if on_double_click:
            self.tree.bind("<Double-Button-1>", self._dispatch_double)
        if on_select:
            self.tree.bind("<<TreeviewSelect>>", self._dispatch_select)

    # ── Public API ───────────────────────────────────────────────

    def set_rows(self, rows: Iterable[tuple], *,
                 row_tags: list[tuple[str, ...]] | None = None) -> None:
        """Replace all rows. Renders in a single batch — fast even for
        thousands of rows because Treeview is virtualised."""
        self.tree.delete(*self.tree.get_children())
        self._row_data.clear()

        rows_list = list(rows)
        for i, row in enumerate(rows_list):
            tag_list = ["odd" if i % 2 else "even"]
            if row_tags and i < len(row_tags):
                tag_list.extend(row_tags[i])
            iid = self.tree.insert("", "end", values=row, tags=tag_list)
            self._row_data[iid] = tuple(row)

    def append(self, row: tuple, *, tags: tuple[str, ...] = ()) -> str:
        """Append one row. Returns its iid for later updates."""
        i = len(self._row_data)
        tag_list = ["odd" if i % 2 else "even", *tags]
        iid = self.tree.insert("", "end", values=row, tags=tag_list)
        self._row_data[iid] = tuple(row)
        return iid

    def update_row(self, iid: str, row: tuple, *,
                   tags: tuple[str, ...] | None = None) -> None:
        """Update a row's values (and optionally tags) by iid."""
        if iid not in self._row_data:
            return
        self.tree.item(iid, values=row)
        if tags is not None:
            stripe = "odd" if list(self._row_data).index(iid) % 2 else "even"
            self.tree.item(iid, tags=[stripe, *tags])
        self._row_data[iid] = tuple(row)

    def clear(self) -> None:
        self.tree.delete(*self.tree.get_children())
        self._row_data.clear()

    def selected_rows(self) -> list[tuple]:
        """All currently selected rows (in selection order)."""
        return [self._row_data[iid] for iid in self.tree.selection()
                if iid in self._row_data]

    def selected_row(self) -> tuple | None:
        sel = self.selected_rows()
        return sel[0] if sel else None

    def row_count(self) -> int:
        return len(self._row_data)

    # ── Internals ────────────────────────────────────────────────

    def _dispatch_double(self, _event):
        if not self._on_double_click:
            return
        sel = self.selected_row()
        if sel is not None:
            try:
                self._on_double_click(sel)
            except Exception:
                pass

    def _dispatch_select(self, _event):
        if not self._on_select:
            return
        try:
            self._on_select(self.selected_rows())
        except Exception:
            pass

    def _sort_by(self, col_id: str):
        ascending = self._sort_state.get(col_id, True)
        col_index = next(i for i, (cid, *_) in enumerate(self._columns)
                         if cid == col_id)
        rows = [(self._row_data[iid], iid)
                for iid in self.tree.get_children("")]

        def sort_key(item):
            v = item[0][col_index]
            # Numeric sort if it parses
            if isinstance(v, (int, float)):
                return (0, v)
            try:
                return (0, float(str(v).replace(":", ".").rstrip("BPM ")))
            except (ValueError, AttributeError):
                return (1, str(v).lower())

        rows.sort(key=sort_key, reverse=not ascending)
        self._sort_state[col_id] = not ascending

        # Reattach in new order with restriped tags
        for new_idx, (data, iid) in enumerate(rows):
            self.tree.move(iid, "", new_idx)
            current_tags = list(self.tree.item(iid, "tags"))
            # Replace stripe tag (always first)
            stripe = "odd" if new_idx % 2 else "even"
            new_tags = [stripe] + [t for t in current_tags
                                    if t not in ("odd", "even")]
            self.tree.item(iid, tags=new_tags)
