"""app.schedule -- one-shot jobs and the daily push time, pure.

Two different ideas live here on purpose, because they must not be confused
(design 18.5):

* a **one-shot schedule** is what a preset's timer creates -- run once at
  ``run_at``; a restart that finds it long overdue *drops* it rather than
  surprising the user at breakfast;
* **next_daily** is the weather push clock -- a time of day, evaluated fresh
  every tick, never stored as a job.
"""

import datetime as dt

from app.schedule import Schedule, at_today, is_overdue, next_daily, parse_hhmm


def test_parse_hhmm_accepts_the_formats_a_picker_yields():
    assert parse_hhmm("22:30") == (22, 30)
    assert parse_hhmm("7:05") == (7, 5)
    assert parse_hhmm("07:05 +0800") == (7, 5)  # Feishu picker_time appends a zone


def test_parse_hhmm_rejects_junk():
    assert parse_hhmm("") is None
    assert parse_hhmm("25:00") is None
    assert parse_hhmm("22:99") is None
    assert parse_hhmm("晚上") is None


def test_at_today_is_todays_wall_clock():
    got = at_today("22:30", dt.datetime(2026, 10, 15, 9, 0))
    assert dt.datetime.fromtimestamp(got) == dt.datetime(2026, 10, 15, 22, 30)


def test_at_today_returns_none_for_a_bad_time():
    assert at_today("nope", dt.datetime(2026, 10, 15, 9, 0)) is None


def test_next_daily_is_today_when_the_time_is_still_ahead():
    got = next_daily("17:00", dt.datetime(2026, 10, 15, 16, 0))
    assert dt.datetime.fromtimestamp(got) == dt.datetime(2026, 10, 15, 17, 0)


def test_next_daily_rolls_to_tomorrow_once_the_time_has_passed():
    got = next_daily("17:00", dt.datetime(2026, 10, 15, 18, 0))
    assert dt.datetime.fromtimestamp(got) == dt.datetime(2026, 10, 16, 17, 0)


def test_a_job_inside_the_grace_window_is_not_overdue():
    now = dt.datetime(2026, 10, 15, 22, 40)
    run_at = dt.datetime(2026, 10, 15, 22, 30).timestamp()
    assert is_overdue(run_at, now) is False


def test_a_job_past_the_grace_window_is_overdue():
    now = dt.datetime(2026, 10, 15, 23, 0)
    run_at = dt.datetime(2026, 10, 15, 22, 30).timestamp()
    assert is_overdue(run_at, now) is True


def test_a_future_job_is_never_overdue():
    now = dt.datetime(2026, 10, 15, 22, 0)
    run_at = dt.datetime(2026, 10, 15, 22, 30).timestamp()
    assert is_overdue(run_at, now) is False


def test_schedule_carries_a_json_payload_and_a_status():
    job = Schedule(kind="preset", payload={"preset_id": 3}, run_at=1.0)
    assert job.status == "pending"
    assert job.payload == {"preset_id": 3}
