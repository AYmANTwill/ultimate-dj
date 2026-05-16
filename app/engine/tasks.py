"""
Task registry — central observer of every long-running background job.

Why this exists
---------------
The app fires a lot of work into daemon threads: bulk-embed, sync,
cooccurrence rebuild, playlist downloads, batch scrapes, repair runs.
Until now each one had its own status label or one-shot toast — easy
to miss, especially when the user is on a different page or starts
another job in parallel. This registry is the single source of truth:
every worker registers itself, posts progress, marks itself done.

The UI (``ui.activity_tray.ActivityTray``) subscribes to changes and
draws a persistent status panel showing every active task. When a task
finishes it stays visible for a few seconds with its result, then
auto-clears.

Public API
----------
    Task                          dataclass: id, name, status, progress, message, started_at, finished_at
    register(name) -> Task        create + return; status="running", progress=0
    update(task_id, progress=None, message=None, eta_s=None)
    complete(task_id, success=True, message="")
    cancel(task_id)               cooperative — sets status="cancelled"
    list_active() -> list[Task]   currently visible (running OR recently finished)
    subscribe(callback)           callback() fires after every change
    unsubscribe(callback)

Threading: every public method holds an internal lock, so workers from
any thread can call them safely. Subscribers are dispatched from the
calling thread — UI subscribers must marshal back via ``after(0, …)``.
"""
from __future__ import annotations

import itertools
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional


_FINISHED_HOLD_S = 6.0    # keep "done"/"error" tasks visible this long


@dataclass
class Task:
    id: int
    name: str
    status: str = "running"            # running | done | error | cancelled
    progress: float = 0.0              # 0..1, -1 means "unknown / spinner"
    message: str = ""
    eta_s: Optional[float] = None
    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    # Cooperative cancel: workers check this on each iteration and exit
    # gracefully. Set via cancel(task_id). Threading.Event so blocking
    # waits in the worker can also be unblocked.
    cancel_event: threading.Event = field(default_factory=threading.Event)

    @property
    def is_active(self) -> bool:
        if self.status == "running":
            return True
        if self.finished_at is None:
            return True
        return (time.time() - self.finished_at) < _FINISHED_HOLD_S

    def cancel_requested(self) -> bool:
        return self.cancel_event.is_set()


_lock = threading.RLock()
_id_counter = itertools.count(1)
_tasks: dict[int, Task] = {}
_subscribers: list[Callable[[], None]] = []


def _notify():
    # Snapshot so subscribers calling back into the registry don't deadlock
    for cb in list(_subscribers):
        try:
            cb()
        except Exception:
            pass


# ── Worker-facing API ─────────────────────────────────────────────

def register(name: str, *, progress: float = 0.0,
             message: str = "") -> Task:
    """Create a new task and return it. Workers should keep the
    returned ``Task`` (or its ``id``) for subsequent ``update()`` calls."""
    with _lock:
        t = Task(id=next(_id_counter), name=name,
                  progress=progress, message=message)
        _tasks[t.id] = t
    _notify()
    return t


def update(task_id: int, *, progress: float | None = None,
            message: str | None = None,
            eta_s: float | None = None) -> None:
    with _lock:
        t = _tasks.get(task_id)
        if t is None or t.status != "running":
            return
        if progress is not None:
            t.progress = max(0.0, min(1.0, float(progress)))
        if message is not None:
            t.message = message
        if eta_s is not None:
            t.eta_s = max(0.0, float(eta_s))
    _notify()


def complete(task_id: int, *, success: bool = True,
              message: str = "") -> None:
    with _lock:
        t = _tasks.get(task_id)
        if t is None:
            return
        t.status = "done" if success else "error"
        t.progress = 1.0 if success else t.progress
        if message:
            t.message = message
        t.finished_at = time.time()
    _notify()
    # Schedule a deferred sweep so finished tasks fall out of list_active()
    # without forcing every subscriber to poll
    threading.Timer(_FINISHED_HOLD_S + 0.5, _sweep).start()


def cancel(task_id: int, *, message: str = "Annulé") -> None:
    """Request cancellation. The worker is responsible for checking
    ``task.cancel_requested()`` periodically and breaking out — we
    don't kill threads. The status flips to 'cancelled' immediately
    so the tray shows it, and the cancel_event is set so any workers
    waiting on a blocking event wake up."""
    with _lock:
        t = _tasks.get(task_id)
        if t is None:
            return
        t.cancel_event.set()
        t.status = "cancelled"
        if message:
            t.message = message
        t.finished_at = time.time()
    _notify()
    threading.Timer(_FINISHED_HOLD_S + 0.5, _sweep).start()


def _sweep():
    """Drop tasks whose finished hold-time has expired."""
    changed = False
    with _lock:
        for tid in list(_tasks):
            if not _tasks[tid].is_active:
                del _tasks[tid]
                changed = True
    if changed:
        _notify()


# ── UI-facing API ─────────────────────────────────────────────────

def list_active() -> list[Task]:
    """Snapshot of currently visible tasks (running + recently
    finished) sorted by start time, oldest first."""
    with _lock:
        return sorted(
            (t for t in _tasks.values() if t.is_active),
            key=lambda t: t.started_at)


def has_running() -> bool:
    with _lock:
        return any(t.status == "running" for t in _tasks.values())


def subscribe(callback: Callable[[], None]) -> None:
    with _lock:
        if callback not in _subscribers:
            _subscribers.append(callback)


def unsubscribe(callback: Callable[[], None]) -> None:
    with _lock:
        if callback in _subscribers:
            _subscribers.remove(callback)
