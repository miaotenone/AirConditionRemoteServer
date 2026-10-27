"""Execution is separate from weather fetching and recommendation policy."""

from app.state import AC_MODE
from app.weather import Recommendation


def test_weather_execution_turns_on_before_setting_mode_and_temperature():
    from app.weather import actions

    recommendation = Recommendation(mode=AC_MODE["cool"], temp=26, fan=2, note="test")
    assert actions(recommendation) == (("power", 1), ("mode", 1), ("temp", 26))


def test_fan_recommendation_does_not_send_an_unused_temperature_or_fan_field():
    from app.weather import actions

    recommendation = Recommendation(mode=AC_MODE["fan"], temp=24, fan=2, note="test")
    assert actions(recommendation) == (("power", 1), ("mode", AC_MODE["fan"]))


def test_a_comfortable_room_sends_no_command():
    from app.weather import actions

    recommendation = Recommendation(mode=AC_MODE["heat"], temp=20, fan=0, note="test", skip=True)
    assert actions(recommendation) == ()
