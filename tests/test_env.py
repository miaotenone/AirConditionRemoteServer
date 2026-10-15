"""app.env -- DHT11 readings and history aggregation, pure.

The firmware publishes ``ac/env``; the service records it and answers "当前 /
今夜 / 一天 / 一周 / 全部" (design 7.2). Two rules matter here:

* a failed read never becomes data -- ``ok=false`` is excluded from every
  statistic, so the history never claims a temperature we did not measure;
* an empty window says so, rather than rendering ``min()`` of nothing.
"""

import datetime as dt

from app.env import (CHART_POINTS, EnvReading, EnvSample, aggregate,
                     chart_points, parse_env)


def _at(*parts) -> float:
    return dt.datetime(*parts).timestamp()


def test_parse_env_reads_a_good_reading():
    reading = parse_env({"temp": 26, "humidity": 55, "ok": True})
    assert reading == EnvReading(temp=26, humidity=55, ok=True)


def test_parse_env_treats_a_failed_read_as_not_ok():
    assert parse_env({"ok": False}) == EnvReading(temp=None, humidity=None, ok=False)


def test_parse_env_rejects_a_payload_without_temperature():
    assert parse_env({"humidity": 55, "ok": True}).ok is False


def test_parse_env_rejects_the_all_zero_frame():
    """2026-10-15: 第一次真接上 DHT11，第一条记录就是 0℃/0% 且 ok=true.

    A DHT11 with nothing to say answers an all-zero frame, and the zero frame
    passes the checksum -- so it arrives as ``ok: true``. It is not a reading:
    the sensor's own spec starts at 20% RH, so 0% is the absence of data. One
    of these in the history would drag the chart down to freezing.
    """
    assert parse_env({"temp": 0, "humidity": 0, "ok": True}).ok is False


def test_parse_env_rejects_an_impossible_humidity():
    # 0% and 150% RH are both outside anything a DHT11 can report.
    assert parse_env({"temp": 26, "humidity": 0, "ok": True}).ok is False
    assert parse_env({"temp": 26, "humidity": 150, "ok": True}).ok is False


def test_parse_env_rejects_an_impossible_temperature():
    assert parse_env({"temp": 120, "humidity": 50, "ok": True}).ok is False


def test_parse_env_still_accepts_a_cold_but_real_room():
    """Guard against over-tightening: a cold room is weather, not a glitch."""
    assert parse_env({"temp": 3, "humidity": 45, "ok": True}) == \
        EnvReading(temp=3, humidity=45, ok=True)


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


# ------------------------------------------------------------- chart points
# 2026-10-15：历史卡片要画折线。图不能把 5 分钟一条的原样端上去 —— 一天 288 条、
# 一周 2016 条，飞书整张卡的上限是 30 KB，而且这么多点眼睛也读不出来。所以先把
# 窗口压成至多 CHART_POINTS 个桶，每桶取均值；**没读数的桶不补点**，图里不能出现
# 没测过的温度。


def test_chart_points_are_capped_however_long_the_window_is():
    now = dt.datetime(2026, 10, 15, 23, 59)
    rows = _samples(*[(_at(2026, 10, 15, 0, 0) + 300 * i, 25, 50, True)
                      for i in range(288)])
    points = chart_points(rows, now=now, window="day")
    assert 2 <= len(points) <= CHART_POINTS
    assert [p.at for p in points] == sorted(p.at for p in points)


def test_chart_points_average_each_bucket():
    now = dt.datetime(2026, 10, 15, 23, 59)
    # Two hours of 5-minute rows alternating 24/26 -> every bucket averages 25.
    rows = _samples(*[(_at(2026, 10, 15, 0, 0) + 300 * i, 24 + 2 * (i % 2), 50, True)
                      for i in range(24)])
    points = chart_points(rows, now=now, window="day", max_points=4)
    assert [p.temp for p in points] == [25, 25, 25, 25]
    assert [p.humidity for p in points] == [50, 50, 50, 50]


def test_chart_points_leave_out_buckets_with_no_readings():
    """A silent device must leave a gap, not a line drawn across thin air."""
    now = dt.datetime(2026, 10, 15, 23, 59)
    rows = _samples(
        *[(_at(2026, 10, 15, 0, 0) + 300 * i, 25, 50, True) for i in range(12)],
        *[(_at(2026, 10, 15, 12, 0) + 300 * i, 27, 50, True) for i in range(12)],
    )
    points = chart_points(rows, now=now, window="day", max_points=12)
    assert len(points) == 2                       # one hour here, one hour there
    assert [p.temp for p in points] == [25, 27]


def test_chart_points_ignore_failed_reads():
    now = dt.datetime(2026, 10, 15, 12, 0)
    rows = _samples((_at(2026, 10, 15, 9, 0), 25, 50, True),
                    (_at(2026, 10, 15, 9, 5), 99, 99, False))
    points = chart_points(rows, now=now, window="day", max_points=1)
    assert [(p.temp, p.humidity) for p in points] == [(25, 50)]


def test_chart_points_for_the_current_window_are_the_latest_reading():
    now = dt.datetime(2026, 10, 15, 12, 0)
    rows = _samples((_at(2026, 10, 15, 11, 0), 25, 50, True),
                    (_at(2026, 10, 15, 11, 55), 27, 52, True))
    points = chart_points(rows, now=now, window="current")
    assert [p.temp for p in points] == [27]


def test_chart_points_share_the_summary_window():
    """The picture and the line of text must describe the same readings."""
    now = dt.datetime(2026, 10, 15, 12, 0)
    rows = _samples((_at(2026, 10, 14, 20, 0), 30, 50, True),    # yesterday
                    (_at(2026, 10, 15, 9, 0), 25, 50, True))     # today
    summary = aggregate(rows, now=now, window="day")
    points = chart_points(rows, now=now, window="day")
    assert summary.count == len(points) == 1
    assert points[0].temp == 25


def test_chart_points_of_nothing_are_empty():
    assert chart_points([], now=dt.datetime(2026, 10, 15, 12, 0), window="day") == ()
