"""One-shot jobs and the daily push clock -- pure and dependency-free.

Two different ideas share this module because they must not be confused
(design 18.5):

* a **one-shot schedule** is what a preset's timer creates -- it runs once at
  ``run_at``; a restart that finds it long overdue *drops* it instead of
  surprising the user hours later;
* **``next_daily``** is the weather-push clock -- a time of day re-evaluated
  every tick, never stored as a job.

``store.py`` persists schedules; nothing here touches I/O.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

#: How late a one-shot job may still fire after a restart (design 18.5).
DEFAULT_GRACE_S = 900.0

#: Job lifecycle.
STATUS_PENDING = "pending"
STATUS_DONE = "done"
STATUS_CANCELLED = "cancelled"
STATUS_DROPPED = "dropped"


@dataclass
class Schedule:
    """A queued one-shot. ``payload`` is free-form JSON for the handler."""

    kind: str
    run_at: float
    payload: dict = field(default_factory=dict)
    status: str = STATUS_PENDING
    id: int = 0


def parse_hhmm(text: str) -> Optional[tuple[int, int]]:
    """Parse "HH:MM" (tolerating a trailing " +0800" from the picker).

    Returns ``None`` for anything that is not a valid time rather than raising:
    the value can arrive straight from a card form.
    """
    if not text:
        return None
    head = str(text).strip().split(" ", 1)[0]
    parts = head.split(":")
    if len(parts) != 2:
        return None
    try:
        hours, minutes = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if not (0 <= hours <= 23 and 0 <= minutes <= 59):
        return None
    return (hours, minutes)


def at_today(hhmm: str, now: datetime) -> Optional[float]:
    """Epoch of ``HH:MM`` on the same calendar day as ``now`` (local time)."""
    parsed = parse_hhmm(hhmm)
    if parsed is None:
        return None
    hours, minutes = parsed
    target = now.replace(hour=hours, minute=minutes, second=0, microsecond=0)
    return target.timestamp()


def next_daily(hhmm: str, now: datetime) -> Optional[float]:
    """The next occurrence of ``HH:MM``: today if still ahead, else tomorrow."""
    target = at_today(hhmm, now)
    if target is None:
        return None
    if now.timestamp() >= target:
        target += 24 * 3600
    return target


def is_overdue(run_at: float, now: datetime, grace_s: float = DEFAULT_GRACE_S) -> bool:
    """True when a job's time passed more than ``grace_s`` ago.

    A job that missed by less than the grace (a quick restart) still runs; one
    that missed by more is dropped, so a "睡觉 22:30" preset cannot fire at
    breakfast (design 18.5).
    """
    return (now.timestamp() - run_at) > grace_s
