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


def test_absolute_temperature():
    assert parse("调到24度").actions == (("temp", 24),)
    assert parse("设成26℃").actions == (("temp", 26),)
    assert parse("温度24").actions == (("temp", 24),)
    assert parse("24度").actions == (("temp", 24),)


def test_out_of_range_passes_through_for_downstream_clamping():
    # 夹取是 state.py 的职责（设计 5.4「发生夹取」）；intent 不越权改值
    assert parse("调到99度").actions == (("temp", 99),)
    assert parse("调到0度").actions == (("temp", 0),)


def test_relative_temperature_aliases():
    assert parse("调低一点").actions == (("temp_step", -1),)
    assert parse("凉快一点").actions == (("temp_step", -1),)
    assert parse("高一点").actions == (("temp_step", 1),)


def test_relative_temperature_with_chinese_numbers():
    assert parse("调低两度").actions == (("temp_step", -2),)
    assert parse("调低二度").actions == (("temp_step", -2),)
    assert parse("升三度").actions == (("temp_step", 3),)


def test_chinese_number_needs_a_measure_word():
    # 「一直」里的「一」不能被当成数字（设计 4.1 第 3 条）——只剩「开」触发的 power
    assert parse("一直开着").actions == (("power", 1),)


def test_kaidao_carries_power_and_temperature_exactly_once():
    assert parse("开到26") == Intent(kind="actions", actions=(("power", 1), ("temp", 26)))
    assert parse("开到26度").actions == (("power", 1), ("temp", 26))


def test_kaidao_without_a_number_does_not_invent_temperature():
    assert parse("开到最大风").actions == (("power", 1), ("fan", 3))


def test_last_temperature_wins():
    assert parse("调到24，算了26").actions == (("temp", 26),)
    assert parse("调到24再低两度").actions == (("temp_step", -2),)
