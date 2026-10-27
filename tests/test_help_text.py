"""HELP_TEXT is the only place that tells the user how to talk to the bot.

Every example on it gets typed back at us by someone who trusted it. So the help
is pinned from both sides: the examples really are understood by `parse`, and
they really are printed in the help. If `intent.py`'s vocabulary moves and the
help doesn't, the user follows the help straight into "没听懂".
"""

from app.intent import parse
from app.main import CARD_COMMANDS
from app.replies import HELP_TEXT

#: (example as printed in the help, the kind it must parse to, its actions)
EXAMPLES = [
    ("开机", "actions", (("power", 1),)),
    ("关机", "actions", (("power", 0),)),
    ("关掉", "actions", (("power", 0),)),
    ("帮我开一下", "actions", (("power", 1),)),
    ("自动", "actions", (("mode", 0),)),
    ("制冷", "actions", (("mode", 1),)),
    ("除湿", "actions", (("mode", 2),)),
    ("送风", "actions", (("mode", 3),)),
    ("制热", "actions", (("mode", 4),)),
    ("调到26度", "actions", (("temp", 26),)),
    ("26度", "actions", (("temp", 26),)),
    ("降2度", "actions", (("temp_step", -2),)),
    ("升2度", "actions", (("temp_step", 2),)),
    ("风速自动", "actions", (("fan", 0),)),
    ("风速低", "actions", (("fan", 1),)),
    ("风速中", "actions", (("fan", 2),)),
    ("风速最大", "actions", (("fan", 3),)),
    ("制冷26度", "actions", (("mode", 1), ("temp", 26))),
    ("状态", "query", ()),
    ("现在多少度", "query", ()),
    ("太热了", "suggest", ()),
    ("当前", "history", ()),
    ("今夜", "history", ()),
    ("一天", "history", ()),
    ("一周", "history", ()),
    ("全部", "history", ()),
    ("开夜间", "night", ()),
    ("关夜间", "night", ()),
    ("夜间状态", "night", ()),
    ("天气", "weather", ()),
    ("预设", "preset", ()),
    ("用夏天", "preset", ()),
]


def test_every_example_in_the_help_is_really_understood():
    for text, kind, actions in EXAMPLES:
        intent = parse(text)
        assert (intent.kind, intent.actions) == (kind, actions), text


def test_the_help_prints_every_example():
    for text, _kind, _actions in EXAMPLES:
        assert text in HELP_TEXT, text


def test_the_help_covers_every_group_the_grammar_has():
    for label in ("开关", "模式", "温度", "风速", "查询", "历史", "夜间", "天气", "预设"):
        assert label in HELP_TEXT, label


def test_the_help_points_at_the_card_summon_word():
    assert CARD_COMMANDS[0] in HELP_TEXT


def test_the_help_is_a_cheat_sheet_not_a_one_liner():
    # 用户反馈「帮助给的内容太少了，不足以使用」—— 钉住行数，别退回一句话。
    assert HELP_TEXT.count("\n") >= 5


def test_the_unclear_hint_stays_one_line():
    # 反向约束：帮助可以长，但「没听懂」不行 —— 它是在用户已经失败之后出现的。
    from app.replies import UNCLEAR_TEXT

    assert "\n" not in UNCLEAR_TEXT
