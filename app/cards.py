"""Feishu card JSON builder -- a pure function, no Feishu SDK required.

Implements the "remote control" card from docs/interface-spec.md 5.3/5.4 as
**Card JSON 2.0** (``"schema": "2.0"``). Verified against the official docs:

* button ---- https://open.feishu.cn/document/feishu-cards/card-json-v2-components/interactive-components/button
* structure - https://open.feishu.cn/document/feishu-cards/card-json-v2-structure

A clickable element therefore carries its action as::

    "behaviors": [{"type": "callback", "value": {"a": "mode", "v": 1}}]

and Feishu delivers it in the ``card.action.trigger`` event (see
``feishu_client.py`` for the normalisation to ``{a, v}``).

**Temperature has two entry points** (spec 5.2/5.3): the ``-``/``+`` buttons emit
``temp_step``, and a ``select_static`` emits an absolute ``temp``. The select's
per-option ``value`` is the plain temperature string; Feishu returns it as
``action.option``, which ``feishu_client`` maps back to ``{"a": "temp", "v": n}``.
(The spec skeleton drew an object there, but the documented option value is a
string -- so normalisation lives in feishu_client, not here.)

Everything is built from plain values so the layout stays testable; the caller
passes the already-formatted ``updated_at`` clock string.
"""

from __future__ import annotations

from typing import Mapping, Optional, Sequence

from .env import Summary
from .preset import Preset
from .state import AC_FAN, AC_MODE, TEMP_MAX, TEMP_MIN, AcState, Presence
from .weather import Recommendation, WeatherNow

HEADER_TITLE = "❄️ 空调遥控器"

#: Headers for the v1.2 cards (design §9.2).
PRESET_TITLE = "🎛 预设"
SETTINGS_TITLE = "⚙️ 设置"
WEATHER_TITLE = "🌤 天气建议"
HISTORY_TITLE = "📈 温度历史"

#: Human labels, derived from the single source of truth for the values.
_MODE_NAMES = {"auto": "自动", "cool": "制冷", "dry": "除湿", "fan": "送风", "heat": "制热"}
_FAN_NAMES = {"auto": "自动", "low": "低", "medium": "中", "high": "高"}

MODE_LABELS = {AC_MODE[name]: label for name, label in _MODE_NAMES.items()}
FAN_LABELS = {AC_FAN[name]: label for name, label in _FAN_NAMES.items()}

#: Header theme by presence, so an offline card looks obviously different.
THEME_ONLINE, THEME_OFFLINE = "green", "grey"

_MARK = "⏳"
_UNCONFIRMED = "⚠️ 未确认"
_STALE = "状态可能过期：设备在线，但已超过 90 秒没有上报状态。"


# ------------------------------------------------------------------ building


def _button(label: str, action: str, value: int, *,
            active: bool = False, disabled: bool = False, size: str = "medium") -> dict:
    """One callback button. `active` is rendered as the highlighted choice."""
    button = {
        "tag": "button",
        "size": size,
        "type": "primary" if active else "default",
        "text": {"tag": "plain_text", "content": label},
        "behaviors": [{"type": "callback", "value": {"a": action, "v": value}}],
    }
    if disabled:
        button["disabled"] = True
    return button


def _label(text: str) -> dict:
    return {"tag": "markdown", "content": f"**{text}**"}


def _auto_column(child: dict) -> dict:
    return {"tag": "column", "width": "auto", "vertical_align": "center", "elements": [child]}


def _weighted_column(child: dict) -> dict:
    return {"tag": "column", "width": "weighted", "weight": 1, "vertical_align": "center",
            "elements": [child]}


def _row(children: list, *, weighted_at: int | None = None) -> dict:
    """A `column_set` flowing left to right; one child index gets the slack."""
    columns = [
        _weighted_column(child) if weighted_at == index else _auto_column(child)
        for index, child in enumerate(children)
    ]
    return {"tag": "column_set", "flex_mode": "flow", "horizontal_spacing": "small",
            "columns": columns}


def _status_markdown(state: AcState, presence: Presence, updated_at: str, *,
                     pending: bool, unconfirmed: bool, stale: bool) -> str:
    power = "开机" if state.power else "关机"
    mode = MODE_LABELS.get(state.mode, str(state.mode))
    fan = FAN_LABELS.get(state.fan, str(state.fan))
    dot = "🟢 在线" if presence.online else "🔴 离线"

    if unconfirmed:
        marker = f" · {_UNCONFIRMED}"
    elif pending:
        marker = f" · {_MARK}"
    else:
        marker = ""

    lines = [f"**状态**：{power} · {mode} · {state.temp}℃ · {fan}风",
             f"**连接**：{dot} · 更新于 {updated_at}{marker}"]
    if stale:
        lines.append(_STALE)
    return "\n".join(lines)


def _temperature_select(state: AcState, disabled: bool) -> dict:
    options = [
        {"text": {"tag": "plain_text", "content": f"{t}℃"}, "value": str(t)}
        for t in range(TEMP_MIN, TEMP_MAX + 1)
    ]
    select = {
        "tag": "select_static",
        "placeholder": {"tag": "plain_text", "content": f"选择温度（当前 {state.temp}℃）"},
        "options": options,
    }
    if disabled:
        select["disabled"] = True
    return select


#: Notice wording: the remote card's (design D22) and the v1.2 cards' -- those
#: move because a *menu tap* has to answer where the user is standing.
MOVED_NOTICE_TEXT = "遥控器已更新，最新一张在下面 👇"
MENU_MOVED_NOTICE_TEXT = "这张卡已更新到最新位置，请看下面 👇"


def build_moved_notice_card(title: str = HEADER_TITLE,
                           *, text: str = MOVED_NOTICE_TEXT) -> dict:
    """A one-line placeholder for an old card that could not be recalled.

    Summoning a fresh card (design D22, and every v1.2 menu tap) tries to recall
    the previous one; Feishu only allows that for ~24 h, so the old card is
    downgraded to this notice instead -- pointing at the new card, which now
    sits below it in the chat.
    """
    return {
        "schema": "2.0",
        "config": {"update_multi": True},
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "template": THEME_OFFLINE,
        },
        "body": {
            "elements": [
                {"tag": "markdown", "content": text},
            ]
        },
    }


def build_card(state: AcState, presence: Presence, *,
               pending: bool = False, unconfirmed: bool = False, stale: bool = False,
               updated_at: str = "--:--:--") -> dict:
    """Render the remote-control card for `state` / `presence`.

    Args:
        pending:     an optimistic update is in flight -> show "⏳"
        unconfirmed: `ac/cmd` went out but nothing came back -> show "⚠️ 未确认"
        stale:       online, but the state is old -> show the soft warning
        updated_at:  pre-formatted clock string for when the state arrived
    """
    is_off = state.power == 0
    # Everything except power is meaningless while the unit is off (spec 5.3).
    greyed = is_off

    power_row = _row([
        _button("开机", "power", 1, active=state.power == 1),
        _button("关机", "power", 0, active=state.power == 0),
    ])

    mode_row = _row([
        _button(MODE_LABELS[value], "mode", value, active=state.mode == value, disabled=greyed)
        for value in sorted(MODE_LABELS)
    ])

    temperature_row = _row([
        _button("－", "temp_step", -1, disabled=greyed),
        _temperature_select(state, disabled=greyed),
        _button("＋", "temp_step", 1, disabled=greyed),
    ], weighted_at=1)

    fan_row = _row([
        _button(FAN_LABELS[value], "fan", value, active=state.fan == value, disabled=greyed)
        for value in sorted(FAN_LABELS)
    ])

    refresh_row = _row([_button("刷新", "refresh", 0, size="small")])

    return {
        "schema": "2.0",
        "config": {"update_multi": True},
        "header": {
            "title": {"tag": "plain_text", "content": HEADER_TITLE},
            "template": THEME_ONLINE if presence.online else THEME_OFFLINE,
        },
        "body": {
            "elements": [
                {"tag": "markdown", "content": _status_markdown(
                    state, presence, updated_at,
                    pending=pending, unconfirmed=unconfirmed, stale=stale)},
                _label("电源"), power_row,
                _label("模式"), mode_row,
                _label("温度"), temperature_row,
                _label("风速"), fan_row,
                refresh_row,
            ]
        },
    }


# ------------------------------------------------------- v1.2 cards (§9.2)
# 每张卡各占一条消息，各自原地 patch（设计 18.1）。回调一律是 {a, v}；表单
# 提交走 action.form_value，键就是下面每个元素的 name。


def _confirm(title: str, text: str) -> dict:
    return {
        "title": {"tag": "plain_text", "content": title},
        "text": {"tag": "plain_text", "content": text},
    }


def _submit_button(label: str, action: str) -> dict:
    """A form's submit button: the click carries `action.form_value`.

    Card JSON 2.0 replaced ``action_type: form_submit`` with
    ``form_action_type: submit``; without the new field Feishu rejects the whole
    card ("there is no submit button in the form container", ErrCode 300123).
    Both are sent: the old one is deprecated but still read by older clients.
    """
    return {
        "tag": "button",
        "size": "medium",
        "type": "primary",
        "name": "submit",
        "action_type": "form_submit",
        "form_action_type": "submit",
        "text": {"tag": "plain_text", "content": label},
        "behaviors": [{"type": "callback", "value": {"a": action}}],
    }


def _options(pairs) -> list:
    return [{"text": {"tag": "plain_text", "content": label}, "value": str(value)}
            for label, value in pairs]


def _select(name: str, placeholder: str, pairs, *, tag: str = "select_static") -> dict:
    return {
        "tag": tag,
        "name": name,
        "placeholder": {"tag": "plain_text", "content": placeholder},
        "options": _options(pairs),
    }


def _time_picker(name: str, placeholder: str) -> dict:
    return {
        "tag": "picker_time",
        "name": name,
        "placeholder": {"tag": "plain_text", "content": placeholder},
    }


def _plain_input(name: str, placeholder: str, *, value: str = "") -> dict:
    element = {
        "tag": "input",
        "name": name,
        "label": {"tag": "plain_text", "content": placeholder},
    }
    if value:
        element["default_value"] = value
    return element


def _form(name: str, children: list) -> dict:
    """A card-root form container (2.0 requires it at the root)."""
    return {"tag": "form", "name": name, "elements": children}


_MODE_CHOICES = [(MODE_LABELS[value], value) for value in sorted(MODE_LABELS)]
_FAN_CHOICES = [(FAN_LABELS[value], value) for value in sorted(FAN_LABELS)]
_TEMP_CHOICES = [(f"{t}℃", t) for t in range(TEMP_MIN, TEMP_MAX + 1)]


def _card_shell(title: str, template: str, elements: list) -> dict:
    return {
        "schema": "2.0",
        "config": {"update_multi": True},
        "header": {"title": {"tag": "plain_text", "content": title},
                   "template": template},
        "body": {"elements": elements},
    }


def build_preset_card(presets: Sequence[Preset], *, status: str = "") -> dict:
    """The preset list: apply / edit / delete each, plus create."""
    elements: list = []
    if status:
        elements.append({"tag": "markdown", "content": status})

    if not presets:
        elements.append({"tag": "markdown", "content": "还没有预设。点下面的「＋ 新建」创建。"})
    else:
        elements.append({"tag": "markdown", "content": "**点名字套用，右侧可改可删**"})
        for preset in presets:
            label = f"{preset.name}（{'开' if preset.power else '关'} · "
            label += f"{MODE_LABELS.get(preset.mode, preset.mode)} · {preset.temp}℃"
            label += f" · {FAN_LABELS.get(preset.fan, preset.fan)}风）"
            elements.append(_row([
                _button(label, "preset_apply", preset.id),
                _button("改", "preset_edit", preset.id, size="small"),
                _delete_button(preset),
            ]))

    elements.append(_row([_button("＋ 新建", "preset_new", 0, size="small")]))
    return _card_shell(PRESET_TITLE, "blue", elements)


def _delete_button(preset: Preset) -> dict:
    button = _button("删", "preset_delete", preset.id, size="small")
    button["confirm"] = _confirm("删除预设", f"确定删除「{preset.name}」？")
    return button


def build_preset_form(preset: Optional[Preset] = None, *, status: str = "") -> dict:
    """The create/edit form. All four fields are present (design 5.1)."""
    editing = preset is not None
    current = preset or Preset(name="", power=1, mode=AC_MODE["cool"],
                               temp=24, fan=AC_FAN["auto"])

    children = [
        _plain_input("name", "预设名字（如 夏天、睡觉）", value=current.name if editing else ""),
        _select("power", "电源", [("开机", 1), ("关机", 0)]),
        _select("mode", "模式", _MODE_CHOICES),
        _select("fan", "风速", _FAN_CHOICES),
        _select("temp", f"温度（当前 {current.temp}℃）", _TEMP_CHOICES),
        _time_picker("at", "定时（可选，不填＝点一下立刻执行）"),
        _submit_button("保存", "preset_save"),
    ]
    elements = []
    if status:
        elements.append({"tag": "markdown", "content": status})
    elements.append(_form("preset_form", children))
    return _card_shell(PRESET_TITLE, "blue", elements)


def build_settings_card(settings: Mapping[str, str], *, night_enabled: bool,
                        status: str = "") -> dict:
    """Push time and the night window/target -- all editable here (design §7)."""
    children = [
        _time_picker("push_time", f"天气推送时间（当前 {settings.get('push_time', '17:00')}）"),
        _time_picker("night_start", f"夜间开始（当前 {settings.get('night_start', '23:00')}）"),
        _time_picker("night_end", f"夜间结束（当前 {settings.get('night_end', '07:00')}）"),
        _select("night_target",
                f"夜间目标温度（当前 {settings.get('night_target', '26')}℃）",
                _TEMP_CHOICES),
        _submit_button("保存设置", "settings_save"),
    ]

    elements = []
    if status:
        elements.append({"tag": "markdown", "content": status})
    elements.append({
        "tag": "markdown",
        "content": f"**夜间模式**：{'已开' if night_enabled else '未开'}"
                   "（开着的夜间才按上面的目标自动调节，风速只到低）",
    })
    elements.append(_row([
        _button("关 夜间" if night_enabled else "开 夜间", "night_toggle",
                0 if night_enabled else 1),
    ]))
    elements.append(_form("settings_form", children))
    return _card_shell(SETTINGS_TITLE, "turquoise", elements)


def build_weather_card(now: Optional[WeatherNow],
                       recommendation: Optional[Recommendation],
                       *, status: str = "") -> dict:
    """The daily suggestion: outdoor reading + one-tap apply / ignore."""
    elements = []
    if status:
        elements.append({"tag": "markdown", "content": status})

    if now is None or recommendation is None:
        elements.append({"tag": "markdown", "content": "🌤 天气没拿到，稍后再试。"})
        return _card_shell(WEATHER_TITLE, "grey", elements)

    lines = [f"**室外** {now.outdoor_c}℃"]
    if now.humidity is not None:
        lines.append(f"湿度 {now.humidity}%")
    if now.next2h_delta_c:
        lines.append(f"两小时内 {'↓' if now.next2h_delta_c < 0 else '↑'}"
                     f"{abs(now.next2h_delta_c)}℃")
    elements.append({"tag": "markdown", "content": " · ".join(lines)})
    elements.append({"tag": "markdown", "content": f"**建议**：{recommendation.note}"})
    elements.append(_row([
        _button("立即执行", "weather_exec", 0),
        _button("忽略", "weather_ignore", 0),
    ]))
    return _card_shell(WEATHER_TITLE, "orange", elements)


def build_history_card(summary: Summary, *, target: Optional[int] = None,
                       text: str = "") -> dict:
    """A read-only view of one history window (the text reply, on a card)."""
    from .replies import render_history  # local import: avoids an import cycle

    content = text or render_history(summary, target=target)
    return _card_shell(HISTORY_TITLE, "indigo",
                       [{"tag": "markdown", "content": content}])
