"""app.env -- DHT11 readings and history aggregation, pure.

The firmware publishes ``ac/env``; the service records it and answers "当前 /
今夜 / 一天 / 一周 / 全部" (design 7.2). Two rules matter here:

* a failed read never becomes data -- ``ok=false`` is excluded from every
  statistic, so the history never claims a temperature we did not measure;
* an empty window says so, rather than rendering ``min()`` of nothing.
"""

import datetime as dt

from app.env import EnvReading, EnvSample, aggregate, parse_env


def _at(*parts) -> float:
    return dt.datetime(*parts).timestamp()


def test_parse_env_reads_a_good_reading():
    reading = parse_env({"temp": 26, "humidity": 55, "ok": True})
    assert reading == EnvReading(temp=26, humidity=55, ok=True)


def test_parse_env_treats_a_failed_read_as_not_ok():
    assert parse_env({"ok": False}) == EnvReading(temp=None, humidity=None, ok=False)


def test_parse_env_rejects_a_payload_without_temperature():
    assert parse_env({"humidity": 55, "ok": True}).ok is False


def test_parse_env_survives_junk():
    assert parse_env({}).ok is False


def _samples(*rows) -> list[EnvSample]:
    return [
        EnvSample(at=at, temp=temp, humidity=hum, ok=ok)
        for (at, temp, hum, ok) in rows
    ]


def test_current_window_returns_the_latest_reading():
    now = dt.datetime(2026, 10, 15, 23, 0)
    rows = _samples(
        (_at(2026, 10, 15, 22, 0), 25, 50, True),
        (_at(2026, 10, 15, 22, 55), 27, 52, True),
    )
    summary = aggregate(rows, now=now, window="current")
    assert summary.count == 1
    assert summary.latest.temp == 27


def test_statistics_ignore_failed_reads():
    now = dt.datetime(2026, 10, 15, 23, 0)
    rows = _samples(
        (_at(2026, 10, 15, 22, 0), 25, 50, True),
        (_at(2026, 10, 15, 22, 5), 99, 99, False),   # a failed read: not data
        (_at(2026, 10, 15, 22, 10), 27, 52, True),
    )
    summary = aggregate(rows, now=now, window="all")
    assert summary.temp_max == 27
    assert summary.temp_min == 25


def test_day_window_starts_at_local_midnight():
    now = dt.datetime(2026, 10, 15, 23, 0)
    rows = _samples(
        (_at(2026, 10, 14, 23, 59), 11, 11, True),   # yesterday
        (_at(2026, 10, 15, 0, 1), 22, 40, True),     # today
    )
    summary = aggregate(rows, now=now, window="day")
    assert summary.count == 1
    assert summary.temp_min == 22


def test_week_window_is_the_last_seven_days():
    now = dt.datetime(2026, 10, 15, 12, 0)
    rows = _samples(
        (_at(2026, 10, 7, 12, 0), 11, 11, True),     # 8 days ago: outside
        (_at(2026, 10, 9, 12, 0), 22, 40, True),     # 6 days ago: inside
    )
    summary = aggregate(rows, now=now, window="week")
    assert summary.count == 1
    assert summary.temp_min == 22


def test_tonight_window_covers_a_cross_midnight_night():
    now = dt.datetime(2026, 10, 15, 2, 0)            # 02:00, inside 23:00-07:00
    rows = _samples(
        (_at(2026, 10, 14, 22, 0), 11, 11, True),    # before the window
        (_at(2026, 10, 14, 23, 30), 24, 44, True),   # inside
        (_at(2026, 10, 15, 1, 30), 25, 45, True),    # inside
    )
    summary = aggregate(rows, now=now, window="tonight",
                        night_start="23:00", night_end="07:00")
    assert summary.count == 2
    assert summary.temp_min == 24


def test_tonight_window_falls_back_to_last_night_outside_it():
    now = dt.datetime(2026, 10, 15, 12, 0)           # midday: no active night
    rows = _samples(
        (_at(2026, 10, 14, 23, 30), 24, 44, True),   # last night
        (_at(2026, 10, 15, 1, 30), 25, 45, True),    # last night
        (_at(2026, 10, 15, 11, 0), 30, 60, True),    # this morning: outside
    )
    summary = aggregate(rows, now=now, window="tonight",
                        night_start="23:00", night_end="07:00")
    assert summary.count == 2


def test_an_empty_window_reports_itself_as_empty():
    summary = aggregate([], now=dt.datetime(2026, 10, 15, 12, 0), window="all")
    assert summary.count == 0
    assert summary.empty is True
    assert summary.temp_avg is None
