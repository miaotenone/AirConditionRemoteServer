"""Intent + state -> the one line the bot answers with (design 5.4).

Pure, like ``intent`` and ``cards``: it needs no Feishu SDK and no broker, so
every wording decision is pinned by a unit test instead of by squinting at a
phone. The receipt always describes **what the device is now**, not what we
sent -- that is what lets a user notice a mistyped command and fix it from the
card.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from .cards import FAN_LABELS, MODE_LABELS
from .env import Summary
from .intent import Intent
from .preset import Preset
from .state import TEMP_MAX, TEMP_MIN, AcState, Presence, apply_action, clamp_temp
from .weather import Recommendation, WeatherNow

#: The one-line grammar reminder, used by the *failure* reply only. It is
#: deliberately short: it appears after "没听懂这句", where length is the
#: problem, not depth. The help reply below is a full cheat sheet instead
#: (user feedback: "帮助给的内容太少了，不足以使用").
_GRAMMAR_HINT = "开机 / 关掉 / 调到24度 / 制冷 / 风速最大 / 状态"
UNCLEAR_TEXT = f"没听懂这句。试试：{_GRAMMAR_HINT}"

#: Someone typed "帮助", so they get the whole vocabulary, grouped the way the
#: card and the bot menu group it. Every example here is pinned by
#: ``tests/test_help_text.py`` against the parser -- an example the bot does not
#: understand would walk the user straight into "没听懂". The two replies keep
#: different intros on purpose: "能听懂这些" answers someone who asked, while
#: "没听懂这句" would read as a rebuke to someone who just failed (design 5.4).
HELP_TEXT = (
    "能听懂这些（也可以直接点输入框上方的菜单）：\n"
    "开关：开机 / 关机 / 关掉 / 帮我开一下\n"
    "模式：自动 / 制冷 / 除湿 / 送风 / 制热\n"
    f"温度：调到26度 / 26度 / 降2度 / 升2度（{TEMP_MIN}~{TEMP_MAX}℃）\n"
    "风速：风速自动 / 风速低 / 风速中 / 风速最大\n"
    "组合：制冷26度（一句话同时改模式和温度）\n"
    "查询：状态 / 现在多少度\n"
    "感觉：太热了 / 有点冷（只给建议，不动空调）\n"
    "历史：当前 / 今夜 / 一天 / 一周 / 全部\n"
    "夜间：开夜间 / 关夜间 / 夜间状态\n"
    "天气：天气\n"
    "预设：预设 / 用夏天（打开预设卡片 / 套用某个预设）\n"
    "卡片：设置卡片（把遥控器卡片重新发到最下面）"
)

#: Human names for the fields a refusal can name.
_FIELD_NAMES = {"power": "电源", "mode": "模式", "temp": "温度", "fan": "风速"}

#: Actions that only make sense once the unit is running -- used to decide
#: whether "off" is worth mentioning on a receipt.
_SETPOINT_ACTIONS = ("mode", "temp", "temp_step", "fan")


def state_line(state: AcState) -> str:
    """The four-part state readout, shared by receipts and status replies."""
    power = "开机" if state.power else "关机"
    mode = MODE_LABELS.get(state.mode, str(state.mode))
    fan = FAN_LABELS.get(state.fan, str(state.fan))
    return f"{power} · {mode} · {state.temp}℃ · {fan}风"


def _negated_fields(hint: str) -> list[str]:
    """Pull the refused fields out of an Intent hint, in the order given."""
    fields = []
    for token in hint.split(","):
        if token.startswith("negated:"):
            field = token.split(":", 1)[1]
            fields.append(_FIELD_NAMES.get(field, field))
    return fields


def _apply_all(state: AcState, actions: tuple[tuple[str, int], ...]) -> AcState:
    result = state
    for action, value in actions:
        result = apply_action(result, action, value)
    return result


def _temperature_clamped(intent: Intent, before: AcState) -> bool:
    """Did the temperature part of the command get clamped by the range check?

    Mirrors `apply_action`: an absolute `temp` replaces the value, a `temp_step`
    moves it. Parsing leaves at most one of the two, but this mirrors the
    pipeline instead of assuming that -- and it has to cover `temp_step`,
    because "调低99度" gets clamped just as hard as "调到99度".
    """
    target = before.temp
    changed = False
    for action, value in intent.actions:
        if action == "temp":
            target, changed = value, True
        elif action == "temp_step":
            target, changed = target + value, True
    return changed and clamp_temp(target) != target


def _modifiers(intent: Intent, before: AcState, after: AcState, presence: Presence) -> list[str]:
    """Reasons the receipt must not be read as a plain "done".

    Order is fixed (design 5.4): clamp, refusal, power, link.
    """
    notes: list[str] = []

    if _temperature_clamped(intent, before):
        notes.append(f"温度范围 {TEMP_MIN}~{TEMP_MAX}")

    refused = _negated_fields(intent.hint)
    if refused:
        notes.append(f"{'、'.join(refused)}没动")

    # Only worth saying when the command was about something an off unit cannot
    # do; a plain "关机" already explains itself.
    setpoint = any(action in _SETPOINT_ACTIONS for action, _value in intent.actions)
    if before.power == 0 and after.power == 0 and setpoint:
        notes.append("当前关机，开机后生效")

    if not presence.online:
        notes.append("设备离线，可能不生效")

    return notes


def _status_text(state: AcState, presence: Presence, *, stale: bool, updated_at: str) -> str:
    dot = "🟢 在线" if presence.online else "🔴 离线"
    text = f"现在 {state_line(state)} · {dot}（更新于 {updated_at}）"
    if stale:
        text += " · 状态可能过期"
    return text


def _suggest_text(hint: str) -> str:
    if hint == "hot":
        return "看着有点热？说「制冷 22 度」，或点卡片上的「制冷」。"
    if hint == "cold":
        return "有点冷？说「制热 26 度」，或点卡片上的「制热」。"
    return "要调一下吗？说「制冷 22 度」或「风速最大」。"


# ---------------------------------------------------- data-driven v1.2 kinds
# 设计 §18：历史 / 天气 / 预设套用都要先查数据，所以各有自己的渲染函数；
# 服务拿到数据后直接调它们，`render` 只负责不需要数据的夜间与预设入口。

#: Human names for the history windows.
_WINDOW_LABELS = {
    "current": "当前",
    "tonight": "今夜",
    "day": "一天",
    "week": "一周",
    "all": "全部",
}

#: What the preset entry word answers with: the real controls live on the card.
_PRESET_OPEN_TEXT = "预设卡片 👇（点名字套用，点「＋新建」新建，点「改」编辑）"

#: A kind that needs data reached `render` -- the service answers these itself.
_NEEDS_CONTEXT = "（这条要查数据，稍后回复）"


def render_history(summary: Summary, *, target: Optional[int] = None) -> str:
    """One line describing a history window, honestly empty when it is."""
    label = _WINDOW_LABELS.get(summary.window, summary.window)
    if summary.latest is None:
        return f"📈 历史（{label}）：还没有记录"

    latest = summary.latest
    text = f"📈 历史（{label}）：室温 {latest.temp}℃"
    if latest.humidity:
        text += f" · 湿度 {latest.humidity}%"
    if summary.count > 1:
        text += (
            f"（{summary.count} 条，{summary.temp_min}~{summary.temp_max}℃，"
            f"均 {summary.temp_avg:.1f}℃）"
        )
    if target is not None:
        delta = latest.temp - target
        text += f" · 目标 {target}℃（差 {'+' if delta >= 0 else ''}{delta}℃）"
    return text


def render_weather(now: Optional[WeatherNow], recommendation: Optional[Recommendation]) -> str:
    """The text half of the daily suggestion; the card carries the buttons."""
    if now is None or recommendation is None:
        return "🌤 天气没拿到，稍后再试。"
    return f"🌤 {recommendation.note}\n点「立即执行」我就照做，或点「忽略」。"


def render_night(mode: str, *, enabled: bool, target: int, start: str, end: str) -> str:
    """The night switch / status receipt."""
    window = f"{start}~{end}" if start and end else ""
    if mode == "on":
        return f"🌙 夜间模式已打开（目标 {target}℃，{window}）"
    if mode == "off":
        return "🌙 夜间模式已关闭"
    if enabled:
        return f"🌙 夜间：已开 · 目标 {target}℃ · {window}"
    return "🌙 夜间：未开"


def render_preset_applied(preset: Preset, when: str, at: Optional[float] = None) -> str:
    """What happened after a preset was clicked: run now, or booked for later."""
    if when == "at" and at is not None:
        return f"✅ 已预约「{preset.name}」 {datetime.fromtimestamp(at).strftime('%H:%M')} 执行"
    return f"✅ 已套用「{preset.name}」（立即执行）"


def render_preset_missing(name: str) -> str:
    return f"没有叫「{name}」的预设。"


def render(
    intent: Intent,
    state: AcState,
    presence: Presence,
    *,
    stale: bool = False,
    updated_at: str = "--:--:--",
) -> str:
    """The reply for one message. Never empty, never says more than it knows."""
    if intent.kind == "query":
        return _status_text(state, presence, stale=stale, updated_at=updated_at)

    if intent.kind == "help":
        return HELP_TEXT

    if intent.kind == "preset" and intent.hint == "open":
        return _PRESET_OPEN_TEXT

    if intent.kind in ("history", "weather", "night", "preset"):
        # The service answers these with data (render_history / render_weather /
        # render_night / render_preset_*); reaching here means wiring skipped a
        # step, so say something harmless rather than a wrong state.
        return _NEEDS_CONTEXT

    if intent.kind == "unclear":
        return UNCLEAR_TEXT

    if intent.kind == "suggest":
        return _suggest_text(intent.hint)

    if not intent.actions:
        # Understood, but everything it asked for was refused.
        return f"好，{'、'.join(_negated_fields(intent.hint))}没动"

    after = _apply_all(state, intent.actions)
    notes = _modifiers(intent, state, after, presence)
    line = f"✅ 已{state_line(after)}"
    if notes:
        line += f"（{'；'.join(notes)}）"
    return line
