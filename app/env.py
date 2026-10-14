"""DHT11 readings and history aggregation -- pure and dependency-free.

The firmware publishes ``ac/env``; the service records it and answers
"当前 / 今夜 / 一天 / 一周 / 全部" (design 7.2). Two rules matter here:

* a failed read never becomes data -- ``ok=false`` is excluded from every
  statistic, so the history never claims a temperature we did not measure;
* an empty window says so, rather than rendering the min of nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from .schedule import parse_hhmm

#: The windows a history query may ask for (design 18.5).
WINDOWS = ("current", "tonight", "day", "week", "all")

_DAY = timedelta(days=1)


@dataclass(frozen=True)
class EnvReading:
    """One ``ac/env`` payload after parsing."""

    temp: Optional[int] = None
    humidity: Optional[int] = None
    ok: bool = False


@dataclass(frozen=True)
class EnvSample:
    """One stored history row (``at`` is an epoch seconds)."""

    at: float
    temp: int
    humidity: int
    ok: bool = True


@dataclass(frozen=True)
class Summary:
    """Aggregated view of a window, for the history reply."""

    window: str
    count: int = 0
    latest: Optional[EnvSample] = None
    temp_min: Optional[int] = None
    temp_max: Optional[int] = None
    temp_avg: Optional[float] = None
    humidity_min: Optional[int] = None
    humidity_max: Optional[int] = None
    humidity_avg: Optional[float] = None

    @property
    def empty(self) -> bool:
        return self.count == 0


def parse_env(payload: dict) -> EnvReading:
    """Read one ``ac/env`` object; anything incomplete counts as a failed read.

    The firmware is expected to publish ``{"ok": false}`` on a bad read, but a
    missing temperature is treated the same way rather than trusted.
    """
    if not isinstance(payload, dict):
        return EnvReading(ok=False)
    temp = payload.get("temp")
    humidity = payload.get("humidity")
    ok = bool(payload.get("ok", False)) and temp is not None
    if not ok:
        return EnvReading(ok=False)
    try:
        return EnvReading(temp=int(temp),
                          humidity=int(humidity) if humidity is not None else None,
                          ok=True)
    except (TypeError, ValueError):
        return EnvReading(ok=False)


# ------------------------------------------------------------------- windows


def _night_bounds(now: datetime, start: str, end: str) -> Optional[tuple[float, float]]:
    """Bounds of the most recent night window (handles a cross-midnight one)."""
    parsed_start, parsed_end = parse_hhmm(start), parse_hhmm(end)
    if parsed_start is None or parsed_end is None:
        return None
    start_today = now.replace(hour=parsed_start[0], minute=parsed_start[1],
                              second=0, microsecond=0)
    end_today = now.replace(hour=parsed_end[0], minute=parsed_end[1],
                            second=0, microsecond=0)

    if start_today <= end_today:            # same-day window
        if now >= start_today:
            return start_today.timestamp(), end_today.timestamp()
        return (start_today - _DAY).timestamp(), (end_today - _DAY).timestamp()

    # crosses midnight
    if now >= start_today:
        return start_today.timestamp(), (end_today + _DAY).timestamp()
    return (start_today - _DAY).timestamp(), end_today.timestamp()


def _bounds(window: str, now: datetime, night_start: str,
            night_end: str) -> Optional[tuple[float, float]]:
    """``(start, end)`` epoch bounds for a window, or ``None`` for "no filter"."""
    if window == "all":
        return None
    if window == "current":
        return (now.timestamp(), now.timestamp())      # latest only, handled below
    if window == "tonight":
        return _night_bounds(now, night_start, night_end)
    if window == "day":
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return midnight.timestamp(), now.timestamp()
    if window == "week":
        return (now.timestamp() - 7 * 24 * 3600), now.timestamp()
    return None


def aggregate(samples: list[EnvSample], *, now: datetime, window: str = "current",
              night_start: str = "", night_end: str = "") -> Summary:
    """Summarise the samples in ``window``, ignoring failed reads."""
    good = [row for row in samples if row.ok]
    if not good:
        return Summary(window=window)

    latest = max(good, key=lambda row: row.at)
    if window == "current":
        return Summary(window=window, count=1, latest=latest,
                       temp_min=latest.temp, temp_max=latest.temp,
                       temp_avg=float(latest.temp),
                       humidity_min=latest.humidity, humidity_max=latest.humidity,
                       humidity_avg=float(latest.humidity) if latest.humidity is not None else None)

    bounds = _bounds(window, now, night_start, night_end)
    if bounds is None:
        selected = good
    else:
        start, end = bounds
        selected = [row for row in good if start <= row.at <= end]

    if not selected:
        return Summary(window=window)

    temps = [row.temp for row in selected]
    humidities = [row.humidity for row in selected if row.humidity is not None]
    return Summary(
        window=window,
        count=len(selected),
        latest=max(selected, key=lambda row: row.at),
        temp_min=min(temps), temp_max=max(temps), temp_avg=sum(temps) / len(temps),
        humidity_min=min(humidities) if humidities else None,
        humidity_max=max(humidities) if humidities else None,
        humidity_avg=(sum(humidities) / len(humidities)) if humidities else None,
    )
