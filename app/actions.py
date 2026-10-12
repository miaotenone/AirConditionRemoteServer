"""Normalise Feishu callback payloads into canonical button actions.

Spec 5.2: a button reports through ``action.value`` while a ``select_static``
reports through ``action.option``. Folding that difference away here means the
rest of the service (state, MQTT) never has to know which Feishu component the
user touched -- it only ever sees ``(action, value)``.

Pure and dependency-free, so it is unit tested without the Feishu SDK.
"""

from __future__ import annotations

from typing import Any, Optional

from .state import ALLOWED_ACTIONS, TEMP_MAX, TEMP_MIN


def _as_int(raw: Any) -> Optional[int]:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def normalize_action(*, value: Any = None, option: Any = None) -> Optional[tuple[str, int]]:
    """Return the canonical ``(action, value)``, or ``None`` if not actionable.

    ``value`` is the ``{"a": ..., "v": ...}`` object from a callback button;
    ``option`` is the chosen ``select_static`` option, whose value is the plain
    temperature string. Anything unrecognised, malformed or out of range returns
    ``None`` so the caller can log and ignore it (spec 5.2).
    """
    if isinstance(value, dict):
        action = value.get("a")
        if action not in ALLOWED_ACTIONS:
            return None
        parsed = _as_int(value.get("v"))
        if parsed is None:
            return None
        return (action, parsed)

    if option is not None:
        temperature = _as_int(option)
        if temperature is None or not TEMP_MIN <= temperature <= TEMP_MAX:
            return None
        return ("temp", temperature)

    return None
