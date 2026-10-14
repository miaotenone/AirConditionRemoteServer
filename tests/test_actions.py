"""Unit tests for app.actions -- normalising Feishu callback payloads.

Spec 5.2: buttons report via `action.value`, selects via `action.option`. The
rest of the service must never see that difference, so both are folded into one
canonical (action, value) pair -- or rejected.
"""

from app.actions import UIAction, normalize_action, normalize_ui_action
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


# --------------------------------------------------------------- UI actions
# 设计 18.2：预设 / 设置 / 天气卡片用同一套 {a, v} 外壳，但它们是 UI 动作 ——
# 绝不进空调队列，所以由另一个函数归一化，和 AC 动作彻底分开。


def test_a_ui_button_normalises_to_a_ui_action():
    assert normalize_ui_action(value={"a": "preset_apply", "v": 3}) == \
        UIAction(kind="preset_apply", target=3)
    assert normalize_ui_action(value={"a": "preset_new", "v": 0}).kind == "preset_new"


def test_the_delete_and_edit_buttons_carry_the_preset_id():
    assert normalize_ui_action(value={"a": "preset_delete", "v": 7}).target == 7
    assert normalize_ui_action(value={"a": "preset_edit", "v": 7}).target == 7


def test_the_weather_buttons_are_ui_actions():
    assert normalize_ui_action(value={"a": "weather_exec", "v": 0}).kind == "weather_exec"
    assert normalize_ui_action(value={"a": "weather_ignore", "v": 0}).kind == "weather_ignore"


def test_night_toggle_carries_the_requested_state():
    assert normalize_ui_action(value={"a": "night_toggle", "v": 1}).target == 1


def test_a_form_submit_carries_the_form_values():
    action = normalize_ui_action(value={"a": "preset_save"},
                                 form_value={"name": "夏天", "temp": "26"})
    assert action == UIAction(kind="preset_save", form={"name": "夏天", "temp": "26"})


def test_a_form_submit_without_values_is_rejected():
    assert normalize_ui_action(value={"a": "preset_save"}) is None


def test_an_ac_action_is_not_a_ui_action():
    # 两条通道互不认领，服务先问 UI 再问 AC，永远只有一个接手。
    assert normalize_ui_action(value={"a": "mode", "v": 1}) is None
    assert normalize_ui_action(value={"a": "temp", "v": 24}) is None


def test_unknown_ui_actions_are_rejected():
    assert normalize_ui_action(value={"a": "launch_missiles", "v": 1}) is None
    assert normalize_ui_action(value="garbage") is None
