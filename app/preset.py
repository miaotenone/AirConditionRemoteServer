"""Named presets ("scenes") -- pure and dependency-free.

A preset is a **combination of commands** (design 5.1): it always carries all
four fields, power included, so applying one is never a partial update. The
optional timer is a *fixed property* of the preset (design 5.3): clicking it
schedules **at most one** run -- there is no daily repeat and nothing here
fires on its own.

The module is deliberately free of I/O: `store.py` persists presets and the
service decides when to apply them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

#: Longest a preset name may be, after normalisation (design 18.9).
NAME_MAX = 12

_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class Preset:
    """One saved scene. ``timer_at`` is "HH:MM" (local), empty when unset."""

    name: str
    power: int = 1
    mode: int = 1
    temp: int = 24
    fan: int = 0
    timer_enabled: bool = False
    timer_at: str = ""
    id: int = 0


def normalize_name(text: str) -> str:
    """Trim, collapse inner whitespace, and cap the length.

    Chinese names have no case to fold, so this is only about making
    "  夏   天 " and "夏天" the same preset.
    """
    return _WHITESPACE.sub(" ", (text or "").strip())[:NAME_MAX]


def actions(preset: Preset) -> tuple[tuple[str, int], ...]:
    """The four commands a preset always applies, in canonical field order.

    Power is included on purpose: a preset is a full scene, so "关机 + 除湿
    + 25℃ + 低风" is just as valid as "开机 + 制冷 + 26℃ + 自动风".
    """
    return (
        ("power", preset.power),
        ("mode", preset.mode),
        ("temp", preset.temp),
        ("fan", preset.fan),
    )


def resolve(preset: Preset, now: datetime) -> tuple[str, Optional[float]]:
    """Decide what a click on this preset does *right now*.

    Returns ``("immediate", None)`` to apply at once, or ``("at", epoch)`` to
    run once at that wall-clock time today. There is no tomorrow: a preset
    whose time has already passed today applies immediately (design 5.3), and
    one with the timer off never schedules at all.
    """
    if not preset.timer_enabled or not preset.timer_at:
        return ("immediate", None)
    try:
        hours, minutes = (int(part) for part in preset.timer_at.split(":", 1))
        target = now.replace(hour=hours, minute=minutes, second=0, microsecond=0)
    except (ValueError, TypeError):
        return ("immediate", None)
    if now >= target:
        return ("immediate", None)
    return ("at", target.timestamp())
