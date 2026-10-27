"""Pure adapters from Feishu form values to existing domain objects.

Keep parsing separate from persistence and card delivery. Defaults deliberately
match the original service; validation/clamping still belongs to its existing
downstream boundaries, not to this adapter.
"""

from __future__ import annotations

from typing import Any, Mapping

from .preset import Preset, normalize_name
from .state import AC_FAN

SETTINGS_FIELDS = ("push_time", "night_start", "night_end", "night_target")


def preset_from_form(form: Mapping[str, Any], *, preset_id: int = 0) -> Preset:
    """Parse a preset without I/O, retaining legacy defaults and timer text."""

    def number(key: str, default: int) -> int:
        try:
            return int(form.get(key))
        except (TypeError, ValueError):
            return default

    timer_at = str(form.get("at") or "").strip()
    return Preset(
        id=preset_id,
        name=normalize_name(str(form.get("name") or "")),
        power=number("power", 1),
        mode=number("mode", 1),
        temp=number("temp", 24),
        fan=number("fan", AC_FAN["auto"]),
        timer_enabled=bool(timer_at),
        timer_at=timer_at,
    )


def settings_updates(form: Mapping[str, Any]) -> dict[str, str]:
    """Only persist known, supplied truthy fields, in their original order."""
    return {key: str(form[key]).strip() for key in SETTINGS_FIELDS if form.get(key)}
