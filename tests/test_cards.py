"""Unit tests for app.cards -- the card JSON builder is a pure function.

The card is the fiddliest part of the whole service and the easiest thing to
get subtly wrong, so it is asserted structurally here rather than by eyeballing
JSON. No Feishu, no MQTT.
"""

import datetime as dt
import json

from app.cards import (
    build_card,
    build_history_card,
    build_preset_card,
    build_preset_form,
    build_settings_card,
    build_weather_card,
)
from app.state import AC_FAN, AC_MODE, TEMP_MAX, TEMP_MIN, AcState, Presence

# -------------------------------------------------------------- test helpers


def _walk(elements):
    for element in elements:
        yield element
        for column in element.get("columns") or []:
            yield from _walk(column.get("elements") or [])
        if element.get("tag") == "form":
            yield from _walk(element.get("elements") or [])


def elements(card):
    return list(_walk(card["body"]["elements"]))


def markdown(card):
    return "\n".join(e.get("content", "") for e in elements(card) if e.get("tag") == "markdown")


def callback_value(button):
    for behavior in button.get("behaviors") or []:
        if behavior.get("type") == "callback":
            return behavior.get("value")
    return None


def buttons(card, action=None):
    found = [e for e in elements(card) if e.get("tag") == "button"]
    if action is not None:
        found = [b for b in found if (callback_value(b) or {}).get("a") == action]
    return found


def button_values(card, action):
    return {callback_value(b)["v"] for b in buttons(card, action)}


def selects(card):
    return [e for e in elements(card) if e.get("tag") == "select_static"]


# ------------------------------------------------------------------ shape


def test_card_declares_schema_two():
    card = build_card(AcState(), Presence(online=True))
    assert card["schema"] == "2.0"


def test_card_is_json_serialisable():
    card = build_card(AcState(temp=26), Presence(online=True, last_state_at=1.0), updated_at="12:00:00")
    json.dumps(card)  # must not raise


# ------------------------------------------------------------ status content


def test_status_line_reports_every_field():
    card = build_card(AcState(power=1, mode=AC_MODE["cool"], temp=24, fan=AC_FAN["medium"]),
                      Presence(online=True))
    line = markdown(card)
    assert "开机" in line
    assert "制冷" in line
    assert "24℃" in line
    assert "中" in line


def test_status_line_says_off_when_powered_off():
    assert "关机" in markdown(build_card(AcState(power=0), Presence(online=True)))


def test_status_line_shows_online_and_offline():
    assert "在线" in markdown(build_card(AcState(), Presence(online=True)))
    offline = markdown(build_card(AcState(), Presence(online=False)))
    assert "离线" in offline
    assert "在线" not in offline.replace("离线", "")


def test_updated_at_is_rendered():
    card = build_card(AcState(), Presence(online=True), updated_at="12:03:41")
    assert "12:03:41" in markdown(card)


def test_header_theme_reflects_presence():
    assert build_card(AcState(), Presence(online=True))["header"]["template"] != \
           build_card(AcState(), Presence(online=False))["header"]["template"]


# ------------------------------------------------------------- pending / stale


def test_pending_adds_the_hourglass_marker():
    assert "⏳" in markdown(build_card(AcState(), Presence(online=True), pending=True))
    assert "⏳" not in markdown(build_card(AcState(), Presence(online=True)))


def test_unconfirmed_replaces_the_marker_with_a_warning():
    line = markdown(build_card(AcState(), Presence(online=True), unconfirmed=True))
    assert "未确认" in line
    assert "⏳" not in line


def test_stale_state_is_called_out():
    assert "状态可能过期" in markdown(build_card(AcState(), Presence(online=True), stale=True))
    assert "状态可能过期" not in markdown(build_card(AcState(), Presence(online=True)))


# ------------------------------------------------------------------- buttons


def test_power_group_offers_on_and_off():
    assert button_values(build_card(AcState(), Presence(online=True)), "power") == {0, 1}


def test_mode_group_covers_every_mode():
    assert button_values(build_card(AcState(), Presence(online=True)), "mode") == set(AC_MODE.values())


def test_fan_group_covers_every_speed():
    assert button_values(build_card(AcState(), Presence(online=True)), "fan") == set(AC_FAN.values())


def test_temperature_group_is_minus_and_plus():
    assert button_values(build_card(AcState(), Presence(online=True)), "temp_step") == {-1, 1}


def test_refresh_button_is_present():
    refresh = buttons(build_card(AcState(), Presence(online=True)), "refresh")
    assert len(refresh) == 1
    assert callback_value(refresh[0])["v"] == 0


def test_active_mode_button_is_highlighted_and_the_rest_are_not():
    card = build_card(AcState(mode=AC_MODE["heat"]), Presence(online=True))
    active = [b for b in buttons(card, "mode") if callback_value(b)["v"] == AC_MODE["heat"]]
    others = [b for b in buttons(card, "mode") if callback_value(b)["v"] != AC_MODE["heat"]]
    assert active[0]["type"] == "primary"
    assert all(b["type"] == "default" for b in others)


def test_active_fan_button_is_highlighted():
    card = build_card(AcState(fan=AC_FAN["high"]), Presence(online=True))
    active = [b for b in buttons(card, "fan") if callback_value(b)["v"] == AC_FAN["high"]]
    assert active[0]["type"] == "primary"


def test_power_off_disables_mode_temp_and_fan_but_not_power():
    card = build_card(AcState(power=0), Presence(online=True))
    for action in ("mode", "temp_step", "fan"):
        assert all(b.get("disabled") is True for b in buttons(card, action)), action
    assert all(not b.get("disabled") for b in buttons(card, "power"))


def test_power_on_leaves_every_group_enabled():
    card = build_card(AcState(power=1), Presence(online=True))
    assert not any(e.get("disabled") for e in buttons(card) if e.get("tag") == "button")


def test_power_off_disables_the_temperature_select():
    assert selects(build_card(AcState(power=0), Presence(online=True)))[0].get("disabled") is True


def test_power_on_leaves_the_temperature_select_enabled():
    assert not selects(build_card(AcState(power=1), Presence(online=True)))[0].get("disabled")


# --------------------------------------------------------------- temperature


def test_temperature_select_offers_the_whole_range():
    options = selects(build_card(AcState(), Presence(online=True)))[0]["options"]
    assert len(options) == TEMP_MAX - TEMP_MIN + 1
    assert [int(o["value"]) for o in options] == list(range(TEMP_MIN, TEMP_MAX + 1))


def test_temperature_select_options_are_labelled_in_degrees():
    options = selects(build_card(AcState(), Presence(online=True)))[0]["options"]
    assert options[0]["text"]["content"] == f"{TEMP_MIN}℃"


def test_temperature_select_placeholder_shows_the_current_temperature():
    placeholder = selects(build_card(AcState(temp=27), Presence(online=True)))[0]["placeholder"]
    assert "27℃" in placeholder["content"]


# ---------------------------------------------------------------- edge cases


def test_card_renders_when_offline_everything_else_unknown():
    card = build_card(AcState(), Presence(online=False))
    assert "离线" in markdown(card)
    assert buttons(card, "power")  # still interactive, the service decides


def test_every_callback_value_is_a_supported_action():
    from app.state import ALLOWED_ACTIONS

    for button in buttons(build_card(AcState(), Presence(online=True))):
        assert callback_value(button)["a"] in ALLOWED_ACTIONS


# --------------------------------------------------------- preset / settings
# 设计 §9.2 / 18.1：预设、设置、天气各占一张卡；所有交互都带 {a, v}，表单
# 提交走 action.form_value（键 = 元素 name）。


def test_the_preset_card_lists_every_preset_with_apply_edit_delete():
    from app.preset import Preset

    presets = [Preset(id=1, name="夏天", temp=26), Preset(id=2, name="冬天", mode=4)]
    card = build_preset_card(presets)
    assert button_values(card, "preset_apply") == {1, 2}
    assert button_values(card, "preset_edit") == {1, 2}
    assert button_values(card, "preset_delete") == {1, 2}
    assert buttons(card, "preset_new")


def test_the_delete_button_asks_for_confirmation():
    from app.preset import Preset

    (delete,) = buttons(build_preset_card([Preset(id=1, name="夏天")]), "preset_delete")
    assert delete.get("confirm"), "删除预设必须二次确认"


def test_the_preset_card_renders_empty():
    assert "还没有" in markdown(build_preset_card([]))


def test_the_preset_form_is_a_root_level_form():
    from app.preset import Preset

    card = build_preset_form(Preset(id=1, name="夏天", temp=26))
    (form,) = [e for e in card["body"]["elements"] if e.get("tag") == "form"]
    names = {e.get("name") for e in form["elements"]}
    assert {"name", "power", "mode", "fan", "temp", "at"} <= names


def test_the_preset_form_submits_as_a_form_submit_button():
    (submit,) = buttons(build_preset_form(), "preset_save")
    assert submit["action_type"] == "form_submit"


def test_the_preset_form_prefills_an_existing_preset():
    from app.preset import Preset

    values = {e["name"]: e.get("default_value")
              for e in elements(build_preset_form(Preset(id=1, name="夏天", temp=26)))
              if e.get("tag") == "input"}
    assert values["name"] == "夏天"


def test_the_settings_card_collects_push_time_and_the_night_window():
    card = build_settings_card({"push_time": "17:00", "night_target": "26"},
                               night_enabled=True)
    names = {e.get("name") for e in elements(card) if e.get("tag") == "form"}
    # the form is itself an element with a name
    assert "settings_form" in names
    inner = {e.get("name") for e in elements(card)}
    assert {"push_time", "night_start", "night_end", "night_target"} <= inner
    assert buttons(card, "settings_save")
    assert buttons(card, "night_toggle")


def test_the_settings_night_toggle_reflects_the_current_state():
    on = callback_value(buttons(build_settings_card({}, night_enabled=True), "night_toggle")[0])
    off = callback_value(buttons(build_settings_card({}, night_enabled=False), "night_toggle")[0])
    assert on["v"] != off["v"]


def test_the_weather_card_carries_the_recommendation_and_two_buttons():
    from app.weather import WeatherNow, recommend

    card = build_weather_card(WeatherNow(outdoor_c=36, humidity=50), recommend(36))
    assert "制冷 26℃" in markdown(card)
    assert buttons(card, "weather_exec") and buttons(card, "weather_ignore")


def test_the_history_card_renders_a_summary():
    from app.env import EnvSample, aggregate

    summary = aggregate([EnvSample(1.0, 26, 50)],
                        now=dt.datetime(2026, 10, 15, 12, 0), window="current")
    assert "26℃" in markdown(build_history_card(summary))


def test_the_new_cards_are_all_json_serialisable():
    from app.env import Summary
    from app.preset import Preset
    from app.weather import WeatherNow, recommend

    for card in (build_preset_card([Preset(id=1, name="夏天")]),
                 build_preset_form(),
                 build_settings_card({}, night_enabled=False),
                 build_weather_card(WeatherNow(outdoor_c=30), recommend(30)),
                 build_history_card(Summary(window="all"))):
        json.dumps(card)
