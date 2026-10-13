"""Text -> Intent: the one place that turns a sentence into button actions.

Pure and dependency-free (design section 2), so the whole grammar is unit tested
without Feishu, MQTT or a broker. The output is deliberately the same
``(action, value)`` shape a card click produces, so nothing downstream can tell
the two apart -- and nothing downstream needed to change.

Only the vocabulary the AC actually has is understood (design 4.2); anything
outside it becomes ``unclear`` rather than a guess, because sending the wrong
``ac/cmd`` moves a physical machine.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Callable, Literal

Kind = Literal["actions", "query", "help", "unclear", "suggest"]

#: Canonical order of the actions in an Intent (design 4.3).
FIELD_ORDER = ("power", "mode", "temp", "fan")

#: Modes the AC has, as words (design 4.2).
_MODE_WORDS = {
    "制冷": 1, "冷风": 1,
    "除湿": 2, "抽湿": 2,
    "送风": 3, "吹风": 3,
    "制热": 4, "暖风": 4, "热风": 4, "加热": 4,
    "自动": 0,
}

#: Fan steps, expressed as "风速<level>", "<level>风" or "<level>档".
_FAN_LEVELS = {"最大": 3, "大": 3, "强": 3, "中": 2, "小": 1, "低": 1, "弱": 1, "自动": 0}

_POWER_ON_WORDS = ("开机", "开空调", "打开空调", "开一下", "启动", "打开", "开")
_POWER_OFF_WORDS = ("关机", "关掉", "关闭", "关一下", "停了", "不用了", "停", "关")

#: Tie-break when two patterns match the same span; higher wins. Only reachable
#: for identical words living in two tables, which the current tables avoid --
#: it is here so a future addition cannot silently flip a word's meaning.
_RULE_TIE_BREAK = {"fan": 20, "temp": 15, "mode": 10, "power": 30}

_DIGITS = r"[0-9]+"
_CHINESE_DIGITS = r"[一二两三四五六七八九十]+"
_NUMBER = rf"(?:{_DIGITS}|{_CHINESE_DIGITS})"
_UNIT = r"(?:度|°c)"

_CN_SINGLE = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}

#: Warming words move the setpoint up, cooling words move it down (design 4.2).
_COOLER = ("调低", "降", "低", "凉快", "冷")
_WARMER = ("调高", "升", "高", "暖", "热")


@dataclass(frozen=True)
class Intent:
    """What one message asked for.

    ``actions`` is empty for every kind except ``"actions"`` -- with one
    deliberate exception (design 3.1): a message that is *only* negation
    ("不要制冷") is still ``kind="actions"`` with an empty tuple, and carries
    ``hint="negated:mode"`` so the receipt can say what was left alone.
    """

    kind: Kind
    actions: tuple[tuple[str, int], ...] = ()
    hint: str = ""


# --------------------------------------------------------------------- normalise


def normalize(text: str) -> str:
    """Fold a sentence down to what the grammar sees.

    Full width to half width (so ``２４`` is ``24``), lower case, and every
    symbol/emoji, format character and punctuation dropped -- an emoji in the
    middle of a word ("开🥶机") must not hide the verb. ``°`` is kept because
    ``℃`` folds to ``°C`` and is part of the temperature vocabulary.
    """
    folded = unicodedata.normalize("NFKC", text).lower()
    kept = []
    for char in folded:
        if char == "°":
            kept.append(char)
            continue
        if char.isspace():
            continue
        category = unicodedata.category(char)
        if category.startswith("S") or category[0] in ("C", "M", "P"):
            continue
        kept.append(char)
    return "".join(kept)


# ------------------------------------------------------------------------ rules


@dataclass(frozen=True)
class _Rule:
    """One recognisable fragment: what it matches, and what it writes.

    `build` turns a match into the fields it sets, so a word that implies two
    things at once ("开到26" = power *and* temperature) stays one rule instead of
    a special case. `requires` names a field that must already have been found
    earlier in the sentence -- that is what keeps a bare "26" from meaning
    anything on its own (design 4.3).
    """

    pattern: re.Pattern
    field: str          # the canonical field it owns; "temp" covers temp_step too
    priority: int
    build: Callable[[re.Match], tuple[tuple[str, str, int], ...]]
    requires: str | None = None


def _to_int(token: str) -> int:
    """Arabic or Chinese numeral -> int (Chinese only needs to reach 30)."""
    if token.isdigit():
        return int(token)
    if "十" in token:
        tens, _, ones = token.partition("十")
        return (_CN_SINGLE.get(tens, 1) if tens else 1) * 10 + (
            _CN_SINGLE.get(ones, 0) if ones else 0
        )
    return _CN_SINGLE[token]


def _static(text: str, field: str, action: str, value: int) -> _Rule:
    writes = ((field, action, value),)
    return _Rule(re.compile(re.escape(text)), field,
                 _RULE_TIE_BREAK.get(field, 0), lambda _match: writes)


def _build_rules() -> tuple[_Rule, ...]:
    rules: list[_Rule] = []

    # -- temperature, most specific first -----------------------------------
    def kaidao(match: re.Match) -> tuple[tuple[str, str, int], ...]:
        temperature = _to_int(match.group("num"))
        return (("power", "power", 1), ("temp", "temp", temperature))

    rules.append(_Rule(
        re.compile(rf"(?:开到|开成)(?P<num>{_NUMBER}){_UNIT}?"),
        "power", _RULE_TIE_BREAK["power"] + 10, kaidao))

    def absolute(match: re.Match) -> tuple[tuple[str, str, int], ...]:
        return (("temp", "temp", _to_int(match.group("num"))),)

    rules.append(_Rule(
        re.compile(rf"(?:调到|设成|设置成|设到|温度|调成)(?P<num>{_NUMBER}){_UNIT}?"),
        "temp", 15, absolute))
    rules.append(_Rule(
        re.compile(rf"(?P<num>{_NUMBER}){_UNIT}"),
        "temp", 15, absolute))

    def relative(match: re.Match) -> tuple[tuple[str, str, int], ...]:
        amount = match.group("amount")
        step = 1 if amount == "一点" else _to_int(amount)
        sign = -1 if match.group("dir") in _COOLER else 1
        return (("temp", "temp_step", sign * step),)

    rules.append(_Rule(
        re.compile(
            rf"(?P<dir>{'|'.join(_COOLER + _WARMER)})"
            rf"(?P<amount>{_NUMBER}|一点){_UNIT}?"),
        "temp", 15, relative))

    # A bare number only means a temperature once the sentence has already
    # established one ("调到24，算了26" -> 26, but "2" alone -> nothing).
    rules.append(_Rule(
        re.compile(rf"(?P<num>{_DIGITS})"),
        "temp", 5,
        lambda match: (("temp", "temp", int(match.group("num"))),),
        requires="temp"))

    # -- mode / fan / power words -------------------------------------------
    for word, value in _MODE_WORDS.items():
        rules.append(_static(word, "mode", "mode", value))

    for level, value in _FAN_LEVELS.items():
        for shape in (f"风速{level}", f"{level}风", f"{level}档"):
            rules.append(_static(shape, "fan", "fan", value))

    for word in _POWER_ON_WORDS:
        rules.append(_static(word, "power", "power", 1))
    for word in _POWER_OFF_WORDS:
        rules.append(_static(word, "power", "power", 0))

    return tuple(rules)


_RULES = _build_rules()


# ------------------------------------------------------------------------ parse


def _scan(text: str) -> dict[str, tuple[str, int]]:
    """Walk the sentence left to right, letting the longest match win.

    Matched text is consumed, so a word cannot be recognised twice and the
    fragments that made it up cannot be recognised on their own ("打开空调" is
    one power word, not "打开" plus "空调"). A field written twice keeps the
    **last** mention, which is how "调到24，算了26" ends up at 26.
    """
    found: dict[str, tuple[str, int]] = {}
    index = 0
    length_of_text = len(text)

    while index < length_of_text:
        best: _Rule | None = None
        best_match: re.Match | None = None
        best_span = 0

        for rule in _RULES:
            if rule.requires is not None and rule.requires not in found:
                continue
            match = rule.pattern.match(text, index)
            if match is None:
                continue
            span = match.end() - match.start()
            if span > best_span or (
                span == best_span and best is not None and rule.priority > best.priority
            ):
                best, best_match, best_span = rule, match, span

        if best is None or best_match is None:
            index += 1
            continue

        for field, action, value in best.build(best_match):
            found[field] = (action, value)
        index += best_span

    return found


def parse(text: str) -> Intent:
    """Turn one message into the actions it asks for (design 4.2/4.3)."""
    found = _scan(normalize(text))
    if not found:
        return Intent(kind="unclear")
    actions = tuple(found[field] for field in FIELD_ORDER if field in found)
    return Intent(kind="actions", actions=actions)
