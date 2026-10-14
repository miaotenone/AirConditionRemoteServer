"""Weather-based suggestions -- the decision is pure, the fetching is not.

Thresholds are the user's own table (design Q2): outdoors 35℃ and up suggests
26℃ cooling, down through "just ventilate" to "heat". A forecast about to get
much cooler nudges the suggestion up a degree (design Q2b).

The network calls live here too but behind their own functions, so the whole
decision is unit tested from a fixed payload.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .state import AC_MODE, TEMP_DEFAULT, clamp_temp

COOL = AC_MODE["cool"]
FAN = AC_MODE["fan"]
HEAT = AC_MODE["heat"]

#: A drop of this much over the next two hours counts as a cold snap.
COLD_SNAP_DELTA = -2


@dataclass(frozen=True)
class Recommendation:
    """A suggested AC setting, plus a short line for the card."""

    mode: int
    temp: int
    fan: int
    note: str

    @property
    def temp_used(self) -> bool:
        """False for a fan-only suggestion, where the temperature is moot."""
        return self.mode != FAN


@dataclass(frozen=True)
class WeatherNow:
    """The slice of a forecast we actually use."""

    outdoor_c: int
    humidity: Optional[int] = None
    next2h_delta_c: int = 0


def recommend(outdoor_c: float, trend_delta_c: float = 0) -> Recommendation:
    """Turn an outdoor temperature into a suggested AC setting.

    Bands (design Q2): ``>=35 -> 26``, ``30..35 -> 25``, ``26..30 -> 24``,
    ``18..26 -> fan only``, ``<18 -> heat``. A coming cold snap raises the
    cooling/heating target by one degree (design Q2b).
    """
    outdoor = int(round(outdoor_c))

    if outdoor >= 35:
        mode, temp, note = COOL, 26, f"外面 {outdoor}℃，建议制冷 26℃"
    elif outdoor >= 30:
        mode, temp, note = COOL, 25, f"外面 {outdoor}℃，建议制冷 25℃"
    elif outdoor >= 26:
        mode, temp, note = COOL, 24, f"外面 {outdoor}℃，建议制冷 24℃"
    elif outdoor >= 18:
        mode, temp, note = FAN, TEMP_DEFAULT, f"外面 {outdoor}℃，凉爽，建议送风通风"
    else:
        mode, temp, note = HEAT, 20, f"外面 {outdoor}℃，建议制热 20℃"

    if mode != FAN and trend_delta_c <= COLD_SNAP_DELTA:
        temp = clamp_temp(temp + 1)
        note += "（两小时内要降温，上调 1℃）"

    return Recommendation(mode=mode, temp=temp, fan=0, note=note)


# ------------------------------------------------------------------- fetching


def parse_open_meteo(payload: dict) -> Optional[WeatherNow]:
    """Read the fields we need out of an Open-Meteo response.

    Returns ``None`` when the payload carries no current temperature -- better
    to say "没拿到天气" than to suggest a setting off a missing value.
    """
    if not isinstance(payload, dict):
        return None
    current = payload.get("current") or {}
    try:
        outdoor = int(round(float(current.get("temperature_2m"))))
    except (TypeError, ValueError):
        return None

    humidity = current.get("relative_humidity_2m")
    try:
        humidity = int(humidity) if humidity is not None else None
    except (TypeError, ValueError):
        humidity = None

    delta = 0
    hourly = ((payload.get("hourly") or {}).get("temperature_2m")) or []
    if len(hourly) >= 3 and isinstance(hourly[0], (int, float)) \
            and isinstance(hourly[2], (int, float)):
        delta = int(round(float(hourly[2]) - float(hourly[0])))

    return WeatherNow(outdoor_c=outdoor, humidity=humidity, next2h_delta_c=delta)
