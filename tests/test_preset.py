"""app.preset -- the named scene model, pure and dependency-free.

A preset is a *combination of commands*: design 5.1 says it always carries all
four fields (power included), so applying one is never a partial update. The
timer is a fixed property of the preset and fires at most once per click
(design 5.3): nothing here schedules anything by itself.
"""

import datetime as dt

from app.preset import Preset, actions, normalize_name, resolve


def test_actions_always_carry_all_four_fields():
    preset = Preset(name="夏天", power=1, mode=1, temp=26, fan=0)
    assert actions(preset) == (("power", 1), ("mode", 1), ("temp", 26), ("fan", 0))


def test_actions_carry_power_off_too():
    preset = Preset(name="全关", power=0, mode=2, temp=25, fan=1)
    assert actions(preset)[0] == ("power", 0)


def test_name_is_trimmed_and_inner_whitespace_collapsed():
    assert normalize_name("  夏   天 ") == "夏 天"


def test_name_is_capped_at_twelve_characters():
    assert len(normalize_name("x" * 40)) == 12


def test_name_may_be_chinese():
    assert normalize_name("睡觉模式") == "睡觉模式"


def test_no_timer_means_run_now():
    preset = Preset(name="夏天", timer_enabled=False, timer_at="22:30")
    assert resolve(preset, dt.datetime(2026, 10, 15, 21, 0)) == ("immediate", None)


def test_timer_still_in_the_future_is_scheduled_for_today():
    preset = Preset(name="睡觉", timer_enabled=True, timer_at="22:30")
    when, at = resolve(preset, dt.datetime(2026, 10, 15, 21, 0))
    assert when == "at"
    assert dt.datetime.fromtimestamp(at) == dt.datetime(2026, 10, 15, 22, 30)


def test_timer_already_past_today_runs_immediately():
    preset = Preset(name="睡觉", timer_enabled=True, timer_at="22:30")
    assert resolve(preset, dt.datetime(2026, 10, 15, 23, 0)) == ("immediate", None)


def test_timer_at_the_exact_minute_runs_immediately():
    # 「已经过了」包含此刻 —— 到点了就直接开，不必再等一秒。
    preset = Preset(name="睡觉", timer_enabled=True, timer_at="22:30")
    assert resolve(preset, dt.datetime(2026, 10, 15, 22, 30)) == ("immediate", None)


def test_timer_enabled_without_a_time_means_run_now():
    # 表单可以同步勾了「定时」却没选时间；把空时间当「没定时」更安全。
    preset = Preset(name="夏天", timer_enabled=True, timer_at="")
    assert resolve(preset, dt.datetime(2026, 10, 15, 21, 0)) == ("immediate", None)
