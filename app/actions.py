"""Normalise Feishu callback payloads into canonical button actions.

Spec 5.2: a button reports through ``action.value`` while a ``select_static``
reports through ``action.option``. Folding that difference away here means the
rest of the service (state, MQTT) never has to know which Feishu component the
user touched -- it only ever sees ``(action, value)``.

Pure and dependency-free, so it is unit tested without the Feishu SDK.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from .env import WINDOWS
from .state import ALLOWED_ACTIONS, TEMP_MAX, TEMP_MIN

#: UI actions that carry no id -- they just open something (design 18.2).
UI_PLAIN_ACTIONS = ("preset_open", "preset_new", "settings_open", "weather_exec", "weather_ignore")
#: UI actions whose `v` is a preset id.
UI_ID_ACTIONS = ("preset_edit", "preset_delete", "preset_apply")
#: UI actions whose `v` is the requested on/off state.
UI_TOGGLE_ACTIONS = ("night_toggle",)
#: Form-submit buttons: the payload is in `action.form_value`, not `v`.
UI_FORM_ACTIONS = ("preset_save", "settings_save")
#: The history card's window switcher: its `v` is a window name, not a number.
UI_WINDOW_ACTIONS = ("history_switch",)

UI_BUTTON_ACTIONS = UI_PLAIN_ACTIONS + UI_ID_ACTIONS + UI_TOGGLE_ACTIONS


@dataclass(frozen=True)
class UIAction:
    """One card interaction that is *not* an AC command.

    Kept apart from `(action, value)` on purpose (design 18.2): UI actions are
    handled synchronously by the service and never reach the AC queue.
    """

    kind: str
    target: int = 0
    form: Mapping[str, Any] = field(default_factory=dict)
    window: str = ""


def normalize_ui_action(*, value: Any = None, form_value: Any = None) -> Optional[UIAction]:
    """Return the UI action a card click asked for, or ``None``.

    ``preset_save`` / ``settings_save`` arrive on a ``form_submit`` button and
    read their fields from ``form_value``; ``history_switch`` carries a window
    name; everything else is a plain button whose ``v`` is an id or a toggle
    state.
    """
    if not isinstance(value, dict):
        return None

    action = value.get("a")
    if action in UI_FORM_ACTIONS:
        if not isinstance(form_value, dict) or not form_value:
            return None
        return UIAction(kind=action, form=dict(form_value))

    if action in UI_WINDOW_ACTIONS:
        window = value.get("v")
        if not isinstance(window, str) or window not in WINDOWS:
            return None  # an unknown window is ignored, never guessed at
        return UIAction(kind=action, window=window)

    if action in UI_BUTTON_ACTIONS:
        return UIAction(kind=action, target=_as_int(value.get("v")) or 0)

    return None


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
