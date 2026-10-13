"""Intent + state -> the one line the bot answers with (design 5.4).

Pure, like ``intent`` and ``cards``: it needs no Feishu SDK and no broker, so
every wording decision is pinned by a unit test instead of by squinting at a
phone. The receipt always describes **what the device is now**, not what we
sent -- that is what lets a user notice a mistyped command and fix it from the
card.
"""

from __future__ import annotations

from .cards import FAN_LABELS, MODE_LABELS
from .intent import Intent
from .state import TEMP_MAX, TEMP_MIN, AcState, Presence, apply_action, clamp_temp

#: The one-line grammar reminder. Both replies below quote it, but with
#: different intros -- saying "能听懂这些" to someone you just failed to
#: understand reads as a rebuke (design 5.4).
_GRAMMAR_HINT = "开机 / 关掉 / 调到24度 / 制冷 / 风速最大 / 状态"
HELP_TEXT = f"能听懂这些：{_GRAMMAR_HINT}"
UNCLEAR_TEXT = f"没听懂这句。试试：{_GRAMMAR_HINT}"

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


def _modifiers(intent: Intent, before: AcState, after: AcState,
               presence: Presence) -> list[str]:
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


def _status_text(state: AcState, presence: Presence, *, stale: bool,
                 updated_at: str) -> str:
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


def render(intent: Intent, state: AcState, presence: Presence, *,
           stale: bool = False, updated_at: str = "--:--:--") -> str:
    """The reply for one message. Never empty, never says more than it knows."""
    if intent.kind == "query":
        return _status_text(state, presence, stale=stale, updated_at=updated_at)

    if intent.kind == "help":
        return HELP_TEXT

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
