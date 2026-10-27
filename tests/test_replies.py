"""Unit tests for app.replies -- Intent + state -> receipt text (design 5.4)."""

import datetime as dt

from app.env import EnvSample, Summary, aggregate
from app.intent import parse
from app.preset import Preset
from app.replies import (
    HELP_TEXT,
    render,
    render_history,
    render_night,
    render_preset_applied,
    render_preset_missing,
    render_weather,
    state_line,
)
from app.state import AcState, Presence
from app.weather import WeatherNow, recommend

ON = AcState(power=1, mode=1, temp=24, fan=0)
OFF = AcState(power=0, mode=1, temp=24, fan=0)
ONLINE = Presence(online=True)
OFFLINE = Presence(online=False)


def test_state_line_reads_like_the_card():
    assert state_line(ON) == "开机 · 制冷 · 24℃ · 自动风"
    assert state_line(OFF) == "关机 · 制冷 · 24℃ · 自动风"


def test_success_line_shows_the_whole_state_after_the_command():
    assert render(parse("开机"), OFF, ONLINE) == "✅ 已开机 · 制冷 · 24℃ · 自动风"


def test_clamped_value_is_explained():
    assert (
        render(parse("调到99度"), ON, ONLINE) == "✅ 已开机 · 制冷 · 30℃ · 自动风（温度范围 16~30）"
    )


def test_the_lower_bound_is_explained_too():
    assert (
        render(parse("调到0度"), ON, ONLINE) == "✅ 已开机 · 制冷 · 16℃ · 自动风（温度范围 16~30）"
    )


def test_a_clamped_relative_step_is_explained_too():
    # 「调低99度」同样被夹了，不能只对绝对温度说
    assert (
        render(parse("调低99度"), ON, ONLINE) == "✅ 已开机 · 制冷 · 16℃ · 自动风（温度范围 16~30）"
    )
    assert (
        render(parse("升99度"), ON, ONLINE) == "✅ 已开机 · 制冷 · 30℃ · 自动风（温度范围 16~30）"
    )


def test_setting_temperature_while_off_is_flagged():
    assert (
        render(parse("调到24度"), OFF, ONLINE)
        == "✅ 已关机 · 制冷 · 24℃ · 自动风（当前关机，开机后生效）"
    )


def test_power_off_receipt_has_no_extra_note():
    # 「关机」本身就会让状态变成关机，不该再补一句「开机后生效」
    assert render(parse("关机"), ON, ONLINE) == "✅ 已关机 · 制冷 · 24℃ · 自动风"


def test_offline_device_is_flagged():
    assert (
        render(parse("开机"), OFF, OFFLINE)
        == "✅ 已开机 · 制冷 · 24℃ · 自动风（设备离线，可能不生效）"
    )


def test_negated_only_message_gets_a_short_ack():
    assert render(parse("不要制冷"), ON, ONLINE) == "好，模式没动"


def test_negation_alongside_an_action_is_noted_on_the_receipt():
    assert (
        render(parse("不要制冷，调到24度"), ON, ONLINE)
        == "✅ 已开机 · 制冷 · 24℃ · 自动风（模式没动）"
    )


def test_query_reports_the_device_truth():
    assert (
        render(parse("状态"), ON, ONLINE, updated_at="12:03:41")
        == "现在 开机 · 制冷 · 24℃ · 自动风 · 🟢 在线（更新于 12:03:41）"
    )


def test_query_notes_a_stale_state():
    assert (
        render(parse("状态"), ON, ONLINE, stale=True, updated_at="12:03:41")
        == "现在 开机 · 制冷 · 24℃ · 自动风 · 🟢 在线（更新于 12:03:41） · 状态可能过期"
    )


def test_query_reports_an_offline_device():
    assert (
        render(parse("开着吗"), ON, OFFLINE, updated_at="12:03:41")
        == "现在 开机 · 制冷 · 24℃ · 自动风 · 🔴 离线（更新于 12:03:41）"
    )


def test_help_and_unclear():
    assert render(parse("帮助"), ON, ONLINE) == HELP_TEXT
    # 设计 5.4：没听懂用「试试：」，与帮助行的「能听懂这些：」不同前缀
    assert (
        render(parse("你好"), ON, ONLINE)
        == "没听懂这句。试试：开机 / 关掉 / 调到24度 / 制冷 / 风速最大 / 状态"
    )


def test_suggest_wording_follows_the_direction():
    assert (
        render(parse("太热了"), ON, ONLINE)
        == "看着有点热？说「制冷 22 度」，或点卡片上的「制冷」。"
    )
    assert render(parse("有点冷"), ON, ONLINE) == "有点冷？说「制热 26 度」，或点卡片上的「制热」。"
    assert render(parse("有点闷"), ON, ONLINE) == "要调一下吗？说「制冷 22 度」或「风速最大」。"


def test_an_explicit_action_wins_over_the_feeling_word():
    assert render(parse("太热了，制冷24度"), ON, ONLINE) == "✅ 已开机 · 制冷 · 24℃ · 自动风"


# ------------------------------------------------------- new v1.2 reply kinds
# 设计 5.4 / §18：历史、天气、预设套用都要数据，所以各有自己的渲染函数；
# `render` 只管不需要数据的夜间与预设入口。


def test_night_switch_replies():
    assert (
        render_night("on", enabled=True, target=26, start="23:00", end="07:00")
        == "🌙 夜间模式已打开（目标 26℃，23:00~07:00）"
    )
    assert (
        render_night("off", enabled=False, target=26, start="23:00", end="07:00")
        == "🌙 夜间模式已关闭"
    )


def test_night_status_replies():
    on = render_night("status", enabled=True, target=26, start="23:00", end="07:00")
    assert "已开" in on and "26℃" in on
    assert "未开" in render_night("status", enabled=False, target=26, start="23:00", end="07:00")


def test_the_preset_entry_points_at_the_card():
    assert "预设" in render(parse("预设"), ON, ONLINE)


def test_history_says_so_when_there_is_nothing():
    empty = Summary(window="week")
    assert "还没有" in render_history(empty)


def test_history_reports_the_latest_and_the_range():
    def at(h, m):
        return dt.datetime(2026, 10, 15, h, m).timestamp()

    rows = [
        EnvSample(at(11, 0), 25, 50),
        EnvSample(at(11, 30), 26, 51),
        EnvSample(at(11, 59), 27, 52),
    ]
    summary = aggregate(rows, now=dt.datetime(2026, 10, 15, 12, 0), window="day")
    text = render_history(summary)
    assert "室温 27℃" in text
    assert "3 条" in text
    assert "25~27℃" in text


def test_history_shows_the_gap_to_the_target():
    rows = [EnvSample(1.0, 28, 50)]
    summary = aggregate(rows, now=dt.datetime(2026, 10, 15, 12, 0), window="current")
    assert "差 +2℃" in render_history(summary, target=26)
    rows = [EnvSample(1.0, 25, 50)]
    summary = aggregate(rows, now=dt.datetime(2026, 10, 15, 12, 0), window="current")
    assert "差 -1℃" in render_history(summary, target=26)


def test_weather_uses_the_recommendation_note():
    rec = recommend(36)
    text = render_weather(WeatherNow(outdoor_c=36), rec)
    assert rec.note in text


def test_weather_says_so_when_the_fetch_failed():
    assert "没拿到" in render_weather(None, None)


def test_preset_apply_receipts():
    preset = Preset(name="夏天")
    assert "夏天" in render_preset_applied(preset, "immediate", None)
    scheduled = render_preset_applied(preset, "at", dt.datetime(2026, 10, 15, 22, 30).timestamp())
    assert "22:30" in scheduled


def test_a_missing_preset_says_so():
    assert "没有" in render_preset_missing("冬天")
