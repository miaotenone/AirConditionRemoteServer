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

from .state import AC_FAN, AC_MODE, TEMP_MAX, TEMP_MIN, AcState, Presence

HEADER_TITLE = "❄️ 空调遥控器"

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
