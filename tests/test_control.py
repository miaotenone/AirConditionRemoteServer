"""app.control -- the night closed loop, pure.

Night mode drives the *room* to a target using the DHT11 reading, the recent
trend and the forecast (design 7.3). Three engineering guards keep it from
fiddling: a deadband that respects the sensor's 1℃ resolution, a minimum
interval between the loop's own actions, and a yield window after any manual
command so the loop never fights the user (design 18.7).

A hard rule rides on top: during night the fan is **always low**, whatever
else the loop decides.
"""

import datetime as dt

from app.control import LoopState, decide
from app.state import AC_FAN, AC_MODE, AcState

NOW = dt.datetime(2026, 10, 15, 23, 30).timestamp()


def cooling(**overrides) -> AcState:
    base = dict(power=1, mode=AC_MODE["cool"], temp=26, fan=AC_FAN["low"])
    base.update(overrides)
    return AcState(**base)


def test_a_hot_room_is_cooled_to_the_target():
    ac = cooling(temp=22, mode=AC_MODE["auto"], fan=AC_FAN["high"])
    decision = decide(29, target=26, ac=ac, now=NOW)
    assert ("temp", 26) in decision.actions
    assert ("mode", AC_MODE["cool"]) in decision.actions
    assert decision.actions[-1] == ("fan", AC_FAN["low"])


def test_the_deadband_leaves_a_room_that_is_close_enough_alone():
    assert decide(27, target=26, ac=cooling(), now=NOW).actions == ()


def test_a_cold_room_turns_the_unit_off():
    decision = decide(24, target=26, ac=cooling(), now=NOW)
    assert ("power", 0) in decision.actions


def test_the_night_fan_is_forced_low_even_with_nothing_else_to_do():
    decision = decide(26, target=26, ac=cooling(fan=AC_FAN["high"]), now=NOW)
    assert decision.actions == (("fan", AC_FAN["low"]),)


def test_the_loop_rests_between_its_own_actions():
    state = LoopState(last_action_at=NOW - 100)
    assert decide(29, target=26, ac=cooling(), now=NOW, state=state).actions == ()


def test_the_loop_yields_to_a_manual_command():
    state = LoopState(manual_until=NOW + 600)
    assert decide(29, target=26, ac=cooling(), now=NOW, state=state).actions == ()


def test_a_room_already_falling_is_left_to_finish():
    # 已经降了 2℃，再开就是过冲（DHT11 只有 1℃ 分辨率，过冲会看得见）。
    assert decide(28, target=26, ac=cooling(), now=NOW,
                  history=[30, 29, 28]).actions == ()


def test_a_forecast_cold_snap_holds_the_loop_back():
    assert decide(28, target=26, ac=cooling(), now=NOW,
                  forecast_delta=-2).actions == ()


def test_heating_mode_wakes_a_cold_room():
    ac = AcState(power=0, mode=AC_MODE["heat"], temp=26, fan=AC_FAN["low"])
    decision = decide(20, target=26, ac=ac, now=NOW)
    assert ("power", 1) in decision.actions


def test_heating_mode_turns_off_when_the_room_is_too_warm():
    ac = AcState(power=1, mode=AC_MODE["heat"], temp=26, fan=AC_FAN["low"])
    decision = decide(30, target=26, ac=ac, now=NOW)
    assert ("power", 0) in decision.actions


def test_an_off_unit_wakes_only_once_the_room_drifts_far_enough():
    off = cooling(power=0)
    assert decide(27, target=26, ac=off, now=NOW).actions == ()
    assert ("power", 1) in decide(28, target=26, ac=off, now=NOW).actions


def test_no_reading_means_no_action():
    assert decide(None, target=26, ac=cooling(), now=NOW).actions == ()


def test_the_target_is_kept_inside_the_units_range():
    decision = decide(35, target=99, ac=cooling(), now=NOW)
    assert ("temp", 30) in decision.actions


def test_a_decision_says_why_for_the_log():
    decision = decide(29, target=26, ac=cooling(temp=22), now=NOW)
    assert "cool" in decision.reason
