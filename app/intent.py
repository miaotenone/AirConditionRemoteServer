"""Text -> Intent: the one place that turns a sentence into button actions.

Pure and dependency-free (design section 2), so the whole grammar is unit tested
without Feishu, MQTT or a broker. The output is deliberately the same
``(action, value)`` shape a card click produces, so nothing downstream can tell
the two apart -- and nothing downstream needed to change.

Only the vocabulary the AC actually has is understood (design 4.2); anything
outside it becomes ``unclear`` rather than a guess, because sending the wrong
``ac/cmd`` moves a physical machine.

The grammar is deliberately lopsided: it must never turn a sentence that merely
*mentions* the AC into an action (design 1.4 is the whole point of the feature's
design). So the short power words are on a short leash --

* ``开`` / ``关`` / ``停`` / ``打开`` / ``关闭`` / ``停了`` only count when the
  sentence is addressed to the AC (polite prefix, then the word, then the device
  name or nothing but particles) or when a real action follows immediately
  ("开制冷24度"). Without that, "没关系" would switch the air conditioner off.
* a relative step ("低两度") needs a measure word, or "身高180" becomes "+180".
* a bare number needs a temperature to already be on the table.

"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Callable, Literal

from .preset import NAME_MAX

Kind = Literal["actions", "query", "help", "unclear", "suggest",
               "history", "night", "weather", "preset"]

#: Canonical order of the actions in an Intent (design 4.3).
FIELD_ORDER = ("power", "mode", "temp", "fan")

#: Modes the AC has, as words (design 4.2). The English names are the ones
#: printed on the unit's own panel, so they are recognised too (design 4.1).
_MODE_WORDS = {
    "制冷": 1, "冷风": 1,
    "除湿": 2, "抽湿": 2,
    "送风": 3, "吹风": 3,
    "制热": 4, "暖风": 4, "热风": 4, "加热": 4,
    "自动": 0,
    "cool": 1, "dry": 2, "fan": 3, "heat": 4, "auto": 0,
}

#: Fan steps, expressed as "风速<level>", "<level>风" or "<level>档".
_FAN_LEVELS = {"最大": 3, "大": 3, "强": 3, "中": 2, "小": 1, "低": 1, "弱": 1, "自动": 0}

_POWER_ON_WORDS = ("开机", "开空调", "打开空调", "开一下", "启动", "打开", "开到", "开成", "开")
_POWER_OFF_WORDS = ("关机", "关空调", "关掉", "关闭", "关一下", "停了", "不用了", "停", "关")

#: One-character power words, plus the short ones that also occur in ordinary
#: speech ("打开心扉", "雨停了"). They are only a command when the sentence is
#: addressed to the AC -- see `_context_word_fits`.
_NEEDS_AC_CONTEXT = ("开", "关", "停", "打开", "关闭", "停了")

#: What may trail a one-word command ("关了", "开吧"). `吗` and `呢` are
#: deliberately absent: "开吗" is a question, and questions are handled first.
_TRAILING_PARTICLES = "了吧啊哦呀啦嘛哈着"

#: What may stand in front of one: words that mean "you, air conditioner".
#: Anything else in front means the sentence is about something else -- which is
#: what keeps "把窗户关了" and "开关" from switching the unit off.
_COMMAND_PREFIXES = ("", "帮我", "帮", "请", "麻烦", "给我", "空调", "把空调")

#: The device's own name. It may follow a short power word ("打开空调"), which is
#: how those words tell a command from ordinary speech.
_DEVICE_WORDS = ("空调",)

#: Asking about the AC, never commanding it. Checked *before* actions on
#: purpose: "开着吗" contains "开", and reading that as "turn on" is the single
#: most obvious way to move the machine by accident.
_QUERY_WORDS = ("状态", "多少度", "几度", "开着吗", "开着没", "怎么样")
#: "开机了吗" asks whether it happened; "开空调好吗" asks to make it happen, and
#: stays a command. Only the perfective endings are state questions.
_QUERY_SUFFIXES = ("了吗", "了呢", "了没")
_HELP_WORDS = ("帮助", "怎么用", "能做什么", "指令", "help")

#: --- v1.2 categories (design 9.4) -----------------------------------------
#: These are matched against the WHOLE normalised sentence, so a sentence that
#: merely mentions them stays `unclear`. They are checked before the query
#: branch because "夜间状态" contains "状态".
#: 历史：`历史` is the menu entry the design puts under 其他; the five window
#: words are the individual views.
_HISTORY_WORDS = {"当前": "current", "今夜": "tonight", "一天": "day",
                  "一周": "week", "全部": "all", "历史": "day"}
#: `夜间` (the design's 其他 ▸ 夜间 menu entry) opens the settings card;
#: `夜间状态` only reports. A bare word cannot say on or off, so it must not
#: toggle -- the destructive reading of an ambiguous word is the wrong guess.
_NIGHT_WORDS = {"开夜间": "on", "关夜间": "off", "夜间状态": "status",
                "夜间": "open"}
_WEATHER_WORDS = ("天气", "今天天气", "建议", "今天开多少度")
_PRESET_OPEN_WORDS = ("预设", "设置预设")
#: Applying a preset by text needs a verb: a bare name must never move the AC.
_PRESET_APPLY_PREFIXES = ("套用", "用")

#: Feeling words. They carry no action (design D12) -- only a direction for the
#: reply, because "太热了" is equally satisfied by -1, -3 or switching to cool.
_HOT_WORDS = ("太热", "好热", "有点热", "热死", "闷热")
_COLD_WORDS = ("太冷", "好冷", "有点冷", "冻死")
_NEUTRAL_WORDS = ("闷", "不舒服", "难受")

#: Longest first: "不需要" must win over "不用" so a prefix never shadows it.
_NEGATION_WORDS = ("不需要", "不要", "不用", "别")

#: Tie-break when two patterns match the same span; higher wins. The order
#: follows design 4.3 (power/mode words -> temperature -> fan). It is
#: unreachable for the tables below -- two rules can only tie on the same span
#: if they are the same string -- but it is here so a future addition cannot
#: silently flip a word's meaning.
_RULE_TIE_BREAK = {"power": 40, "mode": 30, "temp": 20, "fan": 10}

#: A bare number is the weakest thing in the grammar: it must lose to every
#: explicit rule of the same length (design 4.3).
_BARE_NUMBER_PRIORITY = 1

_DIGITS = r"[0-9]+"
_CHINESE_DIGITS = r"[一二两三四五六七八九十]+"
_NUMBER = rf"(?:{_DIGITS}|{_CHINESE_DIGITS})"
_UNIT = r"(?:度|°c)"
#: A number that turns out to be a wind step belongs to the fan, never to the
#: temperature ("调到2档" is not "调到2度").
_NOT_A_WIND_UNIT = r"(?!档|档位|风)"

_CN_SINGLE = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}

#: Direction words for a relative change, and which way they move the setpoint.
_DIRECTION = {
    "调低": -1, "降": -1, "低": -1, "凉快": -1, "冷": -1, "凉": -1,
    "调高": 1, "升": 1, "高": 1, "暖": 1, "热": 1,
}
#: "调低" already means "one step down" on its own, and cannot be confused with
#: anything else. The single characters can: "血压高140" is not a command.
_UNAMBIGUOUS_DIRECTIONS = ("调低", "调高")


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
    anything on its own (design 4.3). `needs_context` marks a short power word
    that has to earn the right to be read as a command.
    """

    pattern: re.Pattern
    field: str          # the canonical field it owns; "temp" covers temp_step too
    priority: int
    build: Callable[[re.Match], tuple[tuple[str, str, int], ...]]
    requires: str | None = None
    needs_context: bool = False


def _to_int(token: str) -> int:
    """Arabic or Chinese numeral -> int.

    Never raises. The token comes from a greedy regex over whatever the user
    typed, so it can be something like "两三" ("lower it a couple degrees");
    dropping the whole command because of that is worse than rounding it to the
    first digit we recognise.
    """
    if token.isdigit():
        return int(token)
    if "十" in token:
        tens, _, ones = token.partition("十")
        high = _CN_SINGLE.get(tens[-1], 1) if tens else 1
        low = _CN_SINGLE.get(ones[0], 0) if ones else 0
        return high * 10 + low
    for char in token:
        if char in _CN_SINGLE:
            return _CN_SINGLE[char]
    return 1


def _alternation(words) -> str:
    """Build a regex alternation, longest word first so a prefix cannot shadow."""
    return "|".join(sorted(words, key=lambda word: (-len(word), word)))


def _static(text: str, field: str, action: str, value: int, *,
            needs_context: bool = False) -> _Rule:
    writes = ((field, action, value),)
    return _Rule(re.compile(re.escape(text)), field,
                 _RULE_TIE_BREAK.get(field, 0), lambda _match: writes,
                 needs_context=needs_context)


def _build_rules() -> tuple[_Rule, ...]:
    rules: list[_Rule] = []

    # -- temperature, most specific first -----------------------------------

    def kaidao(match: re.Match) -> tuple[tuple[str, str, int], ...]:
        return (("power", "power", 1), ("temp", "temp", _to_int(match.group("num"))))

    def kaidao_fan(match: re.Match) -> tuple[tuple[str, str, int], ...]:
        return (("power", "power", 1), ("fan", "fan", int(match.group("step"))))

    power_first = _RULE_TIE_BREAK["power"] + 10
    # "开到 26" reads as power *and* setpoint; the following `开` in the power
    # table must not fire again, which is why this consumes the whole span.
    rules.append(_Rule(
        re.compile(rf"(?:开到|开成)(?P<num>{_NUMBER}){_UNIT}?{_NOT_A_WIND_UNIT}"),
        "power", power_first, kaidao))
    # "开到2档" is a fan step, not a temperature of 2.
    rules.append(_Rule(
        re.compile(r"(?:开到|开成)(?P<step>[0-3])(?:档位|档|风)"),
        "power", power_first, kaidao_fan))

    def absolute(match: re.Match) -> tuple[tuple[str, str, int], ...]:
        return (("temp", "temp", _to_int(match.group("num"))),)

    rules.append(_Rule(
        re.compile(
            rf"(?:调到|设成|设置成|设到|温度|调成)"
            rf"(?P<num>{_NUMBER}){_UNIT}?{_NOT_A_WIND_UNIT}"),
        "temp", _RULE_TIE_BREAK["temp"], absolute))
    rules.append(_Rule(
        re.compile(rf"(?P<num>{_NUMBER}){_UNIT}"),
        "temp", _RULE_TIE_BREAK["temp"], absolute))

    def relative(match: re.Match) -> tuple[tuple[str, str, int], ...]:
        if match.group("one") is not None:
            step = 1                                 # "一点" / "一会儿"
        else:
            token = match.group("num")
            step = 1 if token is None else _to_int(token)
        sign = _DIRECTION[match.group("dir")]
        return (("temp", "temp_step", sign * step),)

    # Unambiguous verbs: "调低" on its own is already one step down.
    rules.append(_Rule(
        re.compile(rf"(?P<dir>{_alternation(_UNAMBIGUOUS_DIRECTIONS)})"
                   rf"(?:(?P<one>一点)|(?P<num>{_NUMBER}))?{_UNIT}?"),
        "temp", _RULE_TIE_BREAK["temp"], relative))
    # Everything else needs a measure word, or any sentence with a number near
    # "高"/"热" becomes a setpoint change.
    single_dirs = [word for word in _DIRECTION if word not in _UNAMBIGUOUS_DIRECTIONS]
    rules.append(_Rule(
        re.compile(rf"(?P<dir>{_alternation(single_dirs)})"
                   rf"(?:(?P<one>一点)|(?P<num>{_NUMBER}){_UNIT})"),
        "temp", _RULE_TIE_BREAK["temp"], relative))

    # A bare number only means a temperature once the sentence has already
    # established one ("调到24，算了26" -> 26, but "2" alone -> nothing).
    rules.append(_Rule(
        re.compile(rf"(?P<num>{_DIGITS})"),
        "temp", _BARE_NUMBER_PRIORITY,
        lambda match: (("temp", "temp", int(match.group("num"))),),
        requires="temp"))

    # -- mode / fan / power words -------------------------------------------
    for word, value in _MODE_WORDS.items():
        rules.append(_static(word, "mode", "mode", value))

    for level, value in _FAN_LEVELS.items():
        for shape in (f"风速{level}", f"{level}风", f"{level}档"):
            rules.append(_static(shape, "fan", "fan", value))

    # A numbered step ("风速2", "2档", "3风") -- the word table above can only
    # express the named levels (最大/大/中/…), not a bare digit.
    def fan_step(match: re.Match) -> tuple[tuple[str, str, int], ...]:
        return (("fan", "fan", int(match.group("step"))),)

    rules.append(_Rule(re.compile(r"风速(?P<step>[0-3])"),
                       "fan", _RULE_TIE_BREAK["fan"], fan_step))
    rules.append(_Rule(re.compile(r"(?P<step>[0-3])(?:档位|档|风)"),
                       "fan", _RULE_TIE_BREAK["fan"], fan_step))

    for word in _POWER_ON_WORDS:
        rules.append(_static(word, "power", "power", 1,
                             needs_context=word in _NEEDS_AC_CONTEXT))
    for word in _POWER_OFF_WORDS:
        rules.append(_static(word, "power", "power", 0,
                             needs_context=word in _NEEDS_AC_CONTEXT))

    return tuple(rules)


_RULES = _build_rules()


# ------------------------------------------------------------------------ parse


def _context_word_fits(text: str, match: re.Match,
                       found: dict[str, tuple[str, int]]) -> bool:
    """May this short, ambiguous power word be read as a command?

    Two ways to earn it:

    * the whole message is addressed to the AC -- optional polite prefix, then
      the word, then either the device's name or nothing but particles:
      "关", "关了", "帮我开", "空调关了", "打开空调";
    * a real non-power action follows immediately: "开制冷24度", "开2档",
      "开到最大风".

    Everything else is chat: "没关系", "开心", "关灯", "开关", "把窗户关了",
    "打开心扉", "雨停了". These words are far too easy to hit by accident for
    anything looser, and the cost of being wrong is a physical machine.
    """
    head = text[:match.start()]
    tail = text[match.end():]
    if head in _COMMAND_PREFIXES:
        if all(char in _TRAILING_PARTICLES for char in tail):
            return True
        if tail.startswith(_DEVICE_WORDS):
            return True
    following = _best_rule_at(text, match.end(), found, allow_context=False)
    return following is not None and following[0].field != "power"


def _best_rule_at(text: str, index: int, found: dict[str, tuple[str, int]],
                  *, allow_context: bool = True) -> tuple[_Rule, re.Match, int] | None:
    """The longest rule matching at `index`, ties broken by priority."""
    best: _Rule | None = None
    best_match: re.Match | None = None
    best_span = 0

    for rule in _RULES:
        if rule.needs_context and not allow_context:
            continue
        if rule.requires is not None and rule.requires not in found:
            continue
        match = rule.pattern.match(text, index)
        if match is None:
            continue
        if rule.needs_context and not _context_word_fits(text, match, found):
            continue
        span = match.end() - match.start()
        if span > best_span or (
            span == best_span and best is not None and rule.priority > best.priority
        ):
            best, best_match, best_span = rule, match, span

    if best is None or best_match is None:
        return None
    return best, best_match, best_span


def _strip_negations(text: str) -> tuple[str, list[str]]:
    """Drop "<negation><action>" spans, remembering which field was refused.

    Only a negation *immediately* followed by something we recognise counts.
    That is what keeps "不用了" whole as the power-off word instead of reading it
    as "不用" plus a stray "了" (design 4.3).
    """
    tokens: list[str] = []
    kept: list[str] = []
    index = 0

    while index < len(text):
        for marker in _NEGATION_WORDS:
            if not text.startswith(marker, index):
                continue
            hit = _best_rule_at(text, index + len(marker), {})
            if hit is None:
                continue
            rule, match, _span = hit
            tokens.append(f"negated:{rule.field}")
            index = match.end()
            break
        else:
            kept.append(text[index])
            index += 1

    return "".join(kept), tokens


def _scan(text: str) -> dict[str, tuple[str, int]]:
    """Walk the sentence left to right, letting the longest match win.

    Matched text is consumed, so a word cannot be recognised twice and the
    fragments that made it up cannot be recognised on their own ("打开空调" is
    one power word, not "打开" plus "空调"). A field written twice keeps the
    **last** mention, which is how "调到24，算了26" ends up at 26.
    """
    found: dict[str, tuple[str, int]] = {}
    index = 0

    while index < len(text):
        hit = _best_rule_at(text, index, found)
        if hit is None:
            index += 1
            continue
        rule, match, span = hit
        for field, action, value in rule.build(match):
            found[field] = (action, value)
        index += span

    return found


def _adjective(text: str) -> str | None:
    """The direction a feeling word points in, or None if it is not one."""
    for word in _HOT_WORDS:
        if word in text:
            return "hot"
    for word in _COLD_WORDS:
        if word in text:
            return "cold"
    for word in _NEUTRAL_WORDS:
        if word in text:
            return ""
    return None


def _exact_intent(clean: str) -> Intent | None:
    """The v1.2 categories, matching the whole sentence only (design 9.4).

    Kept separate from the value grammar because these do not act on the AC at
    all -- they ask for a card, a history window, the night switch or a preset.
    """
    if clean in _HISTORY_WORDS:
        return Intent(kind="history", hint=_HISTORY_WORDS[clean])
    if clean in _NIGHT_WORDS:
        return Intent(kind="night", hint=_NIGHT_WORDS[clean])
    if clean in _WEATHER_WORDS:
        return Intent(kind="weather")
    if clean in _PRESET_OPEN_WORDS:
        return Intent(kind="preset", hint="open")
    for prefix in _PRESET_APPLY_PREFIXES:
        if clean.startswith(prefix):
            name = clean[len(prefix):]
            if 0 < len(name) <= NAME_MAX:
                return Intent(kind="preset", hint=f"apply:{name}")
    return None


def parse(text: str) -> Intent:
    """Turn one message into what it asks for (design 4.2/4.3).

    Order matters: questions and help come first (they must never reach the
    action grammar), then explicit actions, then the purely-refusal case, and
    only then the feeling words -- which never carry an action themselves.

    Total: whatever the user typed, this returns an Intent rather than raising.
    """
    clean, negated = _strip_negations(normalize(text))

    exact = _exact_intent(clean)
    if exact is not None:
        return exact

    if any(word in clean for word in _QUERY_WORDS) or clean.endswith(_QUERY_SUFFIXES):
        return Intent(kind="query")
    if any(word in clean for word in _HELP_WORDS):
        return Intent(kind="help")

    found = _scan(clean)
    if found:
        actions = tuple(found[field] for field in FIELD_ORDER if field in found)
        return Intent(kind="actions", actions=actions, hint=",".join(negated))

    if negated:
        # We understood the message -- there was just nothing left to do.
        return Intent(kind="actions", hint=",".join(negated))

    direction = _adjective(clean)
    if direction is not None:
        return Intent(kind="suggest", hint=direction)

    return Intent(kind="unclear")
