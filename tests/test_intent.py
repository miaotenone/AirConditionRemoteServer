"""Unit tests for app.intent -- text -> Intent (spec section 4)."""

from app.intent import Intent, normalize, parse


def test_normalize_folds_width_case_and_noise():
    assert normalize("２４度") == "24度"
    assert normalize("开🥶机") == "开机"
    assert normalize(" COOL。 ") == "cool"


def test_power_on():
    assert parse("开机") == Intent(kind="actions", actions=(("power", 1),))


def test_power_on_aliases():
    for text in ("开空调", "打开空调", "开一下", "启动"):
        assert parse(text).actions == (("power", 1),), text


def test_power_off_aliases():
    for text in ("关机", "关掉", "关闭", "停了", "不用了"):
        assert parse(text).actions == (("power", 0),), text


def test_modes():
    assert parse("制冷").actions == (("mode", 1),)
    assert parse("冷风").actions == (("mode", 1),)
    assert parse("除湿").actions == (("mode", 2),)
    assert parse("抽湿").actions == (("mode", 2),)
    assert parse("送风").actions == (("mode", 3),)
    assert parse("制热").actions == (("mode", 4),)
    assert parse("暖风").actions == (("mode", 4),)
    assert parse("自动").actions == (("mode", 0),)


def test_fan_levels():
    assert parse("风速最大").actions == (("fan", 3),)
    assert parse("大风").actions == (("fan", 3),)
    assert parse("中风").actions == (("fan", 2),)
    assert parse("低风").actions == (("fan", 1),)
    assert parse("自动风").actions == (("fan", 0),)


def test_auto_alone_is_a_mode_but_auto_wind_is_a_fan_level():
    assert parse("自动").actions == (("mode", 0),)
    assert parse("自动风").actions == (("fan", 0),)


def test_cold_wind_is_a_mode_alias_not_a_fan_level():
    assert parse("冷风").actions == (("mode", 1),)
