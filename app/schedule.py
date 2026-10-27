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
from datetime import datetime, timedelta
from typing import Optional

#: How late a one-shot job may still fire after a restart (design 18.5).
DEFAULT_GRACE_S = 900.0

_DAY = timedelta(days=1)

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


def night_bounds(now: datetime, start: str, end: str) -> Optional[tuple[float, float]]:
    """Bounds of the most recent night window, or ``None`` if unparseable.

    Handles a window that crosses midnight (23:00~07:00): at 02:00 the bounds
    are last night's 23:00 to today's 07:00; at noon they are the same window --
    "the night just gone", which is what both the loop and the history mean.
    """
    parsed_start, parsed_end = parse_hhmm(start), parse_hhmm(end)
    if parsed_start is None or parsed_end is None:
        return None
    start_today = now.replace(hour=parsed_start[0], minute=parsed_start[1], second=0, microsecond=0)
    end_today = now.replace(hour=parsed_end[0], minute=parsed_end[1], second=0, microsecond=0)

    if start_today <= end_today:  # same-day window
        if now >= start_today:
            return start_today.timestamp(), end_today.timestamp()
        return (start_today - _DAY).timestamp(), (end_today - _DAY).timestamp()

    if now >= start_today:  # crosses midnight, we are inside
        return start_today.timestamp(), (end_today + _DAY).timestamp()
    return (start_today - _DAY).timestamp(), end_today.timestamp()


def in_window(now: datetime, start: str, end: str) -> bool:
    """True when ``now`` sits inside the current/just-gone night window."""
    bounds = night_bounds(now, start, end)
    if bounds is None:
        return False
    low, high = bounds
    return low <= now.timestamp() <= high
