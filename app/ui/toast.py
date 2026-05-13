"""
Non-blocking toast notifications.

Why this exists:
    Most app feedback used to be either a `messagebox` (modal — blocks
    the user) or a `status_label` somewhere on the page (invisible if
    the user is on another page). Toasts solve both: they float in the
    bottom-right of the main window, auto-dismiss after a few seconds,
    and don't steal focus.

Optional Undo button:
    A toast can include an action like « Annuler » that fires a
    callback before dismissing. Used by Library bulk-remove so the DJ
    can undo a wrong delete in one click.

Public API:
    show_toast(parent, message, *, kind="info",
               duration_ms=4500,
               action_label=None, action=None)

`kind` ∈ {"info", "success", "warning", "error"}.
"""
from __future__ import annotations

from typing import Callable, Literal

import customtkinter as ctk

from app.config import COLORS


_ToastKind = Literal["info", "success", "warning", "error"]

# Track currently-mounted toasts on each Tk root so they stack instead
# of overlapping. Stack grows upward from the bottom-right corner.
_active_by_root: dict[int, list["_Toast"]] = {}
_TOAST_W = 360
_TOAST_GAP = 8


def _color_for(kind: _ToastKind) -> str:
    return {
        "info":    COLORS["accent"],
        "success": COLORS["success"],
        "warning": COLORS["warning"],
        "error":   COLORS["error"],
    }.get(kind, COLORS["accent"])


def show_toast(parent, message: str, *,
                kind: _ToastKind = "info",
                duration_ms: int = 4500,
                action_label: str | None = None,
                action: Callable[[], None] | None = None) -> None:
    """Pop a toast in the bottom-right corner. Non-blocking."""
    try:
        root = parent.winfo_toplevel()
    except Exception:
        root = parent
    _Toast(root, message, kind, duration_ms, action_label, action)


class _Toast(ctk.CTkFrame):
    def __init__(self, root, message: str, kind: _ToastKind,
                  duration_ms: int,
                  action_label: str | None,
                  action: Callable[[], None] | None):
        # Use root.tk so the toast lives on top of the main window but
        # is repositioned manually (no Toplevel — keeps things simple).
        super().__init__(root, fg_color=COLORS["bg_card"], corner_radius=10,
                         border_width=2, border_color=_color_for(kind))
        self._root = root
        self._kind = kind
        self._action = action
        self._dismiss_job: str | None = None

        # Coloured side-stripe + message + optional action
        ctk.CTkLabel(
            self, text=" ", width=4,
            fg_color=_color_for(kind), corner_radius=4,
        ).pack(side="left", fill="y", padx=(6, 8), pady=6)

        ctk.CTkLabel(
            self, text=message,
            font=ctk.CTkFont(size=12),
            text_color=COLORS["text"],
            anchor="w", justify="left", wraplength=_TOAST_W - 110,
        ).pack(side="left", fill="x", expand=True, padx=4, pady=8)

        if action_label and action is not None:
            ctk.CTkButton(
                self, text=action_label, width=80, height=28,
                font=ctk.CTkFont(size=11, weight="bold"),
                fg_color=_color_for(kind),
                hover_color=COLORS["accent_hover"],
                text_color=COLORS["bg_dark"],
                command=self._fire_action,
            ).pack(side="right", padx=8, pady=6)

        ctk.CTkButton(
            self, text="✕", width=24, height=24,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="transparent", hover_color=COLORS["bg_input"],
            text_color=COLORS["text_dim"],
            command=self._dismiss,
        ).pack(side="right", padx=4, pady=6)

        # Place + auto-dismiss
        self._mount()
        self._dismiss_job = self.after(duration_ms, self._dismiss)

    def _stack(self) -> list["_Toast"]:
        return _active_by_root.setdefault(id(self._root), [])

    def _mount(self):
        """Position the toast above any existing ones in the stack."""
        stack = self._stack()
        # Compute Y offset: 16px from bottom, then stack height + gap
        # for each existing toast.
        root_h = self._root.winfo_height()
        if root_h <= 1:
            self._root.update_idletasks()
            root_h = self._root.winfo_height() or 600
        # Put the toast inside the root; place by anchored bottom-right
        offset_y = 16
        for t in stack:
            try:
                offset_y += t.winfo_reqheight() + _TOAST_GAP
            except Exception:
                pass
        x = self._root.winfo_width() - _TOAST_W - 16
        if x < 16:
            x = 16
        y = root_h - offset_y - 64
        self.place(x=x, y=y, width=_TOAST_W)
        stack.append(self)

    def _fire_action(self):
        if self._action:
            try:
                self._action()
            except Exception:
                pass
        self._dismiss()

    def _dismiss(self):
        if self._dismiss_job:
            try:
                self.after_cancel(self._dismiss_job)
            except Exception:
                pass
            self._dismiss_job = None
        try:
            self._stack().remove(self)
        except ValueError:
            pass
        try:
            self.destroy()
        except Exception:
            pass
        # Reflow the remaining stack
        for i, t in enumerate(list(self._stack())):
            try:
                t._reflow(index=i)
            except Exception:
                pass

    def _reflow(self, *, index: int):
        """Recompute Y position after a sibling toast was dismissed."""
        root_h = self._root.winfo_height()
        offset_y = 16
        stack = self._stack()
        for t in stack[:index]:
            try:
                offset_y += t.winfo_reqheight() + _TOAST_GAP
            except Exception:
                pass
        x = self._root.winfo_width() - _TOAST_W - 16
        if x < 16:
            x = 16
        y = root_h - offset_y - 64
        try:
            self.place(x=x, y=y, width=_TOAST_W)
        except Exception:
            pass
