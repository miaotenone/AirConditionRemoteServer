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
from typing import Literal

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
_RULE_TIE_BREAK = {"fan": 20, "mode": 10, "power": 30}


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
    """One recognisable fragment: what it matches, and what it writes."""

    pattern: re.Pattern
    field: str          # which canonical field it owns; "temp" covers temp_step too
    action: str         # the action name that goes on the wire
    value: int

    @property
    def priority(self) -> int:
        return _RULE_TIE_BREAK.get(self.field, 0)


def _compile(text: str, field: str, action: str, value: int) -> _Rule:
    return _Rule(re.compile(re.escape(text)), field, action, value)


def _build_rules() -> tuple[_Rule, ...]:
    rules: list[_Rule] = []

    for word, value in _MODE_WORDS.items():
        rules.append(_compile(word, "mode", "mode", value))

    for level, value in _FAN_LEVELS.items():
        for shape in (f"风速{level}", f"{level}风", f"{level}档"):
            rules.append(_compile(shape, "fan", "fan", value))

    for word in _POWER_ON_WORDS:
        rules.append(_compile(word, "power", "power", 1))
    for word in _POWER_OFF_WORDS:
        rules.append(_compile(word, "power", "power", 0))

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
        best_span = 0

        for rule in _RULES:
            match = rule.pattern.match(text, index)
            if match is None:
                continue
            span = match.end() - match.start()
            if span > best_span or (
                span == best_span and best is not None and rule.priority > best.priority
            ):
                best, best_span = rule, span

        if best is None:
            index += 1
            continue

        found[best.field] = (best.action, best.value)
        index += best_span

    return found


def parse(text: str) -> Intent:
    """Turn one message into the actions it asks for (design 4.2/4.3)."""
    found = _scan(normalize(text))
    if not found:
        return Intent(kind="unclear")
    actions = tuple(found[field] for field in FIELD_ORDER if field in found)
    return Intent(kind="actions", actions=actions)
