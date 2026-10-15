"""DHT11 readings and history aggregation -- pure and dependency-free.

The firmware publishes ``ac/env``; the service records it and answers
"当前 / 今夜 / 一天 / 一周 / 全部" (design 7.2). Two rules matter here:

* a failed read never becomes data -- ``ok=false`` is excluded from every
  statistic, so the history never claims a temperature we did not measure;
* an empty window says so, rather than rendering the min of nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from .schedule import night_bounds

#: The windows a history query may ask for (design 18.5).
WINDOWS = ("current", "tonight", "day", "week", "all")


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
class ChartPoint:
    """One point of the history chart -- a bucket of samples, averaged."""

    at: float
    temp: int
    humidity: Optional[int] = None
    count: int = 1


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
    return night_bounds(now, start, end)


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
    selected = _select(samples, now=now, window=window,
                       night_start=night_start, night_end=night_end)
    if not selected:
        return Summary(window=window)

    if window == "current":
        latest = selected[0]
        return Summary(window=window, count=1, latest=latest,
                       temp_min=latest.temp, temp_max=latest.temp,
                       temp_avg=float(latest.temp),
                       humidity_min=latest.humidity, humidity_max=latest.humidity,
                       humidity_avg=float(latest.humidity) if latest.humidity is not None else None)

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


# ------------------------------------------------------------------ the chart
# 2026-10-15（设计 18.11）：历史卡片要画折线。一天的 5 分钟行是 288 条、一周
# 2016 条 —— 飞书整张卡上限 30 KB，而且这么多点眼睛也读不出来，所以先把窗口压成
# 至多 CHART_POINTS 个桶，每桶取均值。

#: How many points the history chart is drawn from. Two lines at this size keep
#: the whole card around 4 KB, two orders of magnitude under the message cap.
CHART_POINTS = 48


def _select(samples: list[EnvSample], *, now: datetime, window: str,
            night_start: str, night_end: str) -> list[EnvSample]:
    """The readings a window covers -- the one place text and chart agree.

    Shared by `aggregate` and `chart_points` so the picture can never show a
    reading the sentence left out (or the other way round).
    """
    good = [row for row in samples if row.ok]
    if not good:
        return []
    if window == "current":
        return [max(good, key=lambda row: row.at)]
    bounds = _bounds(window, now, night_start, night_end)
    if bounds is None:
        return good
    start, end = bounds
    return [row for row in good if start <= row.at <= end]


def chart_points(samples: list[EnvSample], *, now: datetime, window: str = "day",
                 night_start: str = "", night_end: str = "",
                 max_points: int = CHART_POINTS) -> tuple[ChartPoint, ...]:
    """Downsample one window into at most ``max_points`` averaged buckets.

    A bucket nobody measured in is skipped, never filled: the chart must not
    draw a temperature that was not read (design 18.6).
    """
    selected = sorted(_select(samples, now=now, window=window,
                              night_start=night_start, night_end=night_end),
                      key=lambda row: row.at)
    if not selected:
        return ()
    if len(selected) <= max_points:
        return tuple(ChartPoint(at=row.at, temp=row.temp, humidity=row.humidity)
                     for row in selected)

    first, last = selected[0].at, selected[-1].at
    width = (last - first) / max_points
    if width <= 0:                       # every reading at the same instant
        return (_average(selected, at=first),)

    buckets: dict[int, list[EnvSample]] = {}
    for row in selected:
        index = min(int((row.at - first) / width), max_points - 1)
        buckets.setdefault(index, []).append(row)
    return tuple(_average(buckets[index], at=first + (index + 0.5) * width)
                 for index in sorted(buckets))


def _average(group: list[EnvSample], *, at: float) -> ChartPoint:
    temps = [row.temp for row in group]
    humidities = [row.humidity for row in group if row.humidity is not None]
    return ChartPoint(
        at=at,
        temp=int(round(sum(temps) / len(temps))),
        humidity=int(round(sum(humidities) / len(humidities))) if humidities else None,
        count=len(group),
    )
