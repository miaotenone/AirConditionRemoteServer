"""The night closed loop -- pure and dependency-free.

Night mode drives the *room* to a target using the DHT11 reading, the recent
trend and the forecast (design 7.3). Three guards keep it from fiddling: a
deadband that respects the sensor's 1℃ resolution, a minimum interval between
the loop's own actions, and a yield window after any manual command so the loop
never fights the user (design 18.7).

A hard rule rides on top: during night the fan is **always low**.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Optional, Sequence

from .state import AC_FAN, AC_MODE, STATE_FIELDS, AcState, clamp_temp

#: How far the room may drift before the loop acts (DHT11 is 1℃ resolution).
DEADBAND = 1
#: The loop's own actions are spaced at least this far apart.
MIN_INTERVAL_S = 600.0
#: A manual command wins for this long (design 18.7).
MANUAL_YIELD_S = 1800.0
#: "Already moving this fast the right way" -- 1℃ over the trend window.
MOVING_RIGHT = 1
#: A forecast this much cooler warms the plan by enough to hold back.
COLD_SNAP = -2

FAN_LOW = AC_FAN["low"]


@dataclass(frozen=True)
class LoopState:
    """The loop's memory across ticks."""

    last_action_at: float = 0.0
    manual_until: float = 0.0


@dataclass(frozen=True)
class Decision:
    """What the loop wants to change, and why (for the log)."""

    actions: tuple[tuple[str, int], ...] = ()
    reason: str = "ok"


def _trend(history: Sequence[int]) -> int:
    """Signed change across the window (newest minus oldest); 0 if too short."""
    if len(history) < 2:
        return 0
    return int(history[-1]) - int(history[0])


def _diff(current: AcState, desired: AcState) -> tuple[tuple[str, int], ...]:
    """Only the fields that actually change, in canonical order."""
    return tuple(
        (field, int(getattr(desired, field)))
        for field in STATE_FIELDS
        if getattr(current, field) != getattr(desired, field)
    )


def decide(
    room: Optional[int],
    *,
    target: int,
    ac: AcState,
    now: float,
    state: LoopState = LoopState(),
    history: Sequence[int] = (),
    forecast_delta: int = 0,
    deadband: int = DEADBAND,
    min_interval_s: float = MIN_INTERVAL_S,
) -> Decision:
    """What to do this tick, given the room, the target and the situation.

    Returns no actions when the room is close enough, when the loop acted
    recently, when the user just took over, or when the room (and the weather)
    is already heading for the target on its own.
    """
    if room is None:
        return Decision(reason="no reading")

    target = clamp_temp(target)
    if now < state.manual_until:
        return Decision(reason="manual yield")
    if state.last_action_at and (now - state.last_action_at) < min_interval_s:
        return Decision(reason="cooldown")

    heating = ac.mode == AC_MODE["heat"]
    too_warm = room > target + deadband
    too_cold = room < target - deadband

    effective = _trend(history) + min(forecast_delta, 0)
    moving_right = effective >= MOVING_RIGHT if heating else effective <= -MOVING_RIGHT

    # A night always runs the fan low; start from that and decide the rest.
    desired = replace(ac, fan=FAN_LOW)

    if ac.power == 0:
        # Only wake up when the room has drifted clearly the wrong way.
        wake = too_cold if heating else too_warm
        if wake and not moving_right:
            desired = AcState(
                power=1,
                mode=AC_MODE["heat"] if heating else AC_MODE["cool"],
                temp=target,
                fan=FAN_LOW,
            )
            reason = "heat" if heating else "cool"
        else:
            reason = "off"
    else:
        needs_action = too_cold if heating else too_warm
        over_corrected = too_warm if heating else too_cold
        if needs_action and not moving_right:
            desired = AcState(
                power=1,
                mode=AC_MODE["heat"] if heating else AC_MODE["cool"],
                temp=target,
                fan=FAN_LOW,
            )
            reason = "heat" if heating else "cool"
        elif over_corrected and not moving_right:
            desired = replace(ac, power=0, fan=FAN_LOW)
            reason = "off"
        else:
            reason = "hold"

    actions = _diff(ac, desired)
    if not actions:
        return Decision(reason="hold")
    return Decision(actions=actions, reason=reason)
