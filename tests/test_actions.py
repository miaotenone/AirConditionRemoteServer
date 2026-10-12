"""Unit tests for app.actions -- normalising Feishu callback payloads.

Spec 5.2: buttons report via `action.value`, selects via `action.option`. The
rest of the service must never see that difference, so both are folded into one
canonical (action, value) pair -- or rejected.
"""

from app.actions import normalize_action
from app.state import AC_MODE, TEMP_MAX, TEMP_MIN


def test_button_value_becomes_an_action():
    assert normalize_action(value={"a": "mode", "v": AC_MODE["cool"]}) == ("mode", AC_MODE["cool"])


def test_power_button_value_becomes_an_action():
    assert normalize_action(value={"a": "power", "v": 1}) == ("power", 1)


def test_refresh_is_an_accepted_action():
    assert normalize_action(value={"a": "refresh", "v": 0}) == ("refresh", 0)


def test_select_option_becomes_an_absolute_temperature():
    assert normalize_action(option=str(TEMP_MIN)) == ("temp", TEMP_MIN)
    assert normalize_action(option=str(TEMP_MAX)) == ("temp", TEMP_MAX)


def test_value_wins_when_both_are_present():
    assert normalize_action(value={"a": "mode", "v": 4}, option=str(TEMP_MIN)) == ("mode", 4)


def test_unknown_action_is_rejected():
    assert normalize_action(value={"a": "drop_everything", "v": 1}) is None


def test_malformed_value_is_rejected():
    assert normalize_action(value={"nope": 1}) is None
    assert normalize_action(value="garbage") is None
    assert normalize_action(value={"a": "temp", "v": "not-a-number"}) is None


def test_out_of_range_select_option_is_rejected():
    assert normalize_action(option=str(TEMP_MAX + 1)) is None
    assert normalize_action(option=str(TEMP_MIN - 1)) is None


def test_non_numeric_and_empty_options_are_rejected():
    assert normalize_action(option="cool") is None
    assert normalize_action(option="") is None


def test_nothing_present_is_rejected():
    assert normalize_action() is None
