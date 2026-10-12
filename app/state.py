"""AC state model -- pure and dependency-free.

Everything here takes plain values and returns plain values, so it can be unit
tested without MQTT, Feishu or SQLite (see docs/interface-spec.md section 6/9).

The real world's truth is the ESP32's retained `ac/state`; this module only
models it locally, applies button actions to it, and turns actions into the
`ac/cmd` payload.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from typing import Any, Iterable, Optional

# --- field value tables (mirror the firmware / docs/interface-spec.md 4.2) ---

POWER_OFF, POWER_ON = 0, 1

AC_MODE = {"auto": 0, "cool": 1, "dry": 2, "fan": 3, "heat": 4}
AC_FAN = {"auto": 0, "low": 1, "medium": 2, "high": 3}

TEMP_MIN, TEMP_MAX, TEMP_DEFAULT = 16, 30, 24

#: Fields that make up the AC state (what goes on the wire and into card state).
STATE_FIELDS = ("power", "mode", "temp", "fan")

#: The only button actions the service accepts (spec 5.2).
ALLOWED_ACTIONS = ("power", "mode", "temp", "temp_step", "fan", "refresh")


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _clamp(value: int, low: int, high: int) -> int:
    return low if value < low else high if value > high else value


def clamp_power(v: Any) -> int:
    return _clamp(_as_int(v, POWER_OFF), POWER_OFF, POWER_ON)


def clamp_mode(v: Any) -> int:
    return _clamp(_as_int(v, AC_MODE["cool"]), 0, max(AC_MODE.values()))


def clamp_temp(v: Any) -> int:
    return _clamp(_as_int(v, TEMP_DEFAULT), TEMP_MIN, TEMP_MAX)


def clamp_fan(v: Any) -> int:
    return _clamp(_as_int(v, AC_FAN["auto"]), 0, max(AC_FAN.values()))


_CLAMPERS = {
    "power": clamp_power,
    "mode": clamp_mode,
    "temp": clamp_temp,
    "fan": clamp_fan,
}


# --------------------------------------------------------------------- state


@dataclass(frozen=True)
class AcState:
    """The AC state we mirror from the device."""

    power: int = POWER_OFF
    mode: int = AC_MODE["cool"]
    temp: int = TEMP_DEFAULT
    fan: int = AC_FAN["auto"]

    def fingerprint(self) -> str:
        """Stable id for content comparison, so identical states don't re-render."""
        payload = json.dumps({f: getattr(self, f) for f in STATE_FIELDS}, sort_keys=True)
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True)
class Presence:
    """Whether the device is reachable, and how fresh our state is."""

    online: bool = False
    last_state_at: float = 0.0


def from_payload(payload: dict) -> AcState:
    """Build a state from an `ac/state` payload (missing fields keep defaults)."""
    return AcState(
        power=clamp_power(payload.get("power", POWER_OFF)),
        mode=clamp_mode(payload.get("mode", AC_MODE["cool"])),
        temp=clamp_temp(payload.get("temp", TEMP_DEFAULT)),
        fan=clamp_fan(payload.get("fan", AC_FAN["auto"])),
    )


def apply_action(state: AcState, action: str, value: int) -> AcState:
    """Apply one button action, returning a NEW state.

    Unknown actions are ignored -- the caller decides whether to log them.
    `temp_step` is relative; everything else is absolute. All values are clamped
    here so the service never relies on the firmware to do it (spec 4.2).
    """
    if action == "temp_step":
        return replace(state, temp=clamp_temp(state.temp + _as_int(value, 0)))
    if action in _CLAMPERS:
        return replace(state, **{action: _CLAMPERS[action](value)})
    return state


def to_command(state: AcState, action: str, value: int) -> Optional[dict]:
    """Payload for `ac/cmd`, carrying ONLY the field the action changes.

    Returns:
        dict  -- publish this
        {}    -- valid action that needs no publish (e.g. `refresh`)
        None  -- not an action we accept; caller logs and ignores
    """
    if action == "refresh":
        return {}
    if action == "temp_step":
        return {"temp": apply_action(state, action, value).temp}
    if action in _CLAMPERS:
        return {action: _CLAMPERS[action](value)}
    return None


def merge_actions(state: AcState, actions: Iterable[tuple[str, int]]) -> dict:
    """Collapse a burst of queued actions into ONE minimal `ac/cmd` payload.

    Implements the debounce in spec 5.5: three fast "+" taps from 24 become a
    single `{"temp": 27}` instead of three round trips. Actions are applied in
    order, so later absolute values win.
    """
    merged: dict = {}
    for action, value in actions:
        command = to_command(state, action, value)
        if not command:
            continue
        merged.update(command)
        state = apply_action(state, action, value)
    return merged


# ------------------------------------------------------------------ presence


def presence_from_status(presence: Presence, payload: dict) -> Presence:
    """Fold an `ac/status` payload (or the LWT) into the presence flag."""
    return replace(presence, online=bool(payload.get("online", presence.online)))


def presence_from_state(presence: Presence, now: float) -> Presence:
    """A fresh `ac/state` proves the device is online; stamp its arrival time."""
    return replace(presence, online=True, last_state_at=now)


def is_stale(presence: Presence, now: float, max_age: float = 90.0) -> bool:
    """State older than `max_age` while still 'online' -- show a soft warning.

    Offline is the LWT's job, not ours, so an offline device is never 'stale'.
    """
    if not presence.online or presence.last_state_at <= 0.0:
        return False
    return (now - presence.last_state_at) > max_age
