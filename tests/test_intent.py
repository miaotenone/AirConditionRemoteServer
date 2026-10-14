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
    # 「一直」里的「一」不能被当成数字（设计 4.1 第 3 条）。整句既没有温度也没有动作：
    # 单字「开」被夹在句子中间时不算命令（防误触发，见
    # test_chat_that_merely_contains_a_power_character_is_not_a_command）。
    assert parse("一直开着").kind == "unclear"
    assert parse("一直开着").actions == ()
    assert parse("降低一度").actions == (("temp_step", -1),)


def test_kaidao_carries_power_and_temperature_exactly_once():
    assert parse("开到26") == Intent(kind="actions", actions=(("power", 1), ("temp", 26)))
    assert parse("开到26度").actions == (("power", 1), ("temp", 26))


def test_kaidao_without_a_number_does_not_invent_temperature():
    assert parse("开到最大风").actions == (("power", 1), ("fan", 3))


def test_last_temperature_wins():
    assert parse("调到24，算了26").actions == (("temp", 26),)
    assert parse("调到24再低两度").actions == (("temp_step", -2),)


def test_query():
    for text in ("状态", "现在多少度", "开着吗", "几度"):
        assert parse(text) == Intent(kind="query"), text


def test_questions_are_queries_not_commands():
    # 「开着吗」里有个「开」——不能当成开机，这是最直接的一类误触发
    for text in ("开着吗", "开机了吗", "制冷了吗"):
        assert parse(text).kind == "query", text
        assert parse(text).actions == ()


def test_help():
    for text in ("帮助", "怎么用", "能做什么"):
        assert parse(text) == Intent(kind="help"), text


def test_unclear_inputs_never_crash():
    for text in ("", "   ", "你好", "2", "空调🥶", "😀"):
        assert parse(text).kind == "unclear", text


def test_suggest_is_direction_aware():
    assert parse("太热了") == Intent(kind="suggest", hint="hot")
    assert parse("有点热") == Intent(kind="suggest", hint="hot")
    assert parse("有点冷") == Intent(kind="suggest", hint="cold")
    assert parse("好冷") == Intent(kind="suggest", hint="cold")
    assert parse("有点闷") == Intent(kind="suggest", hint="")


def test_suggest_never_carries_an_action():
    # 设计 D12 —— 反目标「误触发」的守护测试
    for text in ("太热了", "有点冷", "有点闷"):
        assert parse(text).actions == (), text


def test_an_explicit_action_beats_the_adjective():
    # 形容词本身不执行，但用户把动作说清楚了就照做
    assert parse("太热了，制冷24度").actions == (("mode", 1), ("temp", 24))


def test_bare_number_is_not_a_temperature():
    assert parse("2").kind == "unclear"
    assert parse("风速2档").actions == (("fan", 2),)


def test_negation_drops_the_action_and_records_it():
    assert parse("不要制冷") == Intent(kind="actions", hint="negated:mode")
    assert parse("不要制冷，调到24度").actions == (("temp", 24),)
    assert parse("不要制冷，调到24度").hint == "negated:mode"


def test_bu_yong_le_is_power_off_not_a_negation():
    # 「不用了」整体是关机词，不能被拆成「不用」+「了」
    assert parse("不用了") == Intent(kind="actions", actions=(("power", 0),))


def test_multiple_actions_are_canonically_ordered():
    assert parse("开机制冷24度") == Intent(
        kind="actions", actions=(("power", 1), ("mode", 1), ("temp", 24))
    )


def test_english_mode_words_are_recognised_the_same_way():
    # 设计 4.1 第 4 条：英文词小写化后同样识别（空调面板上的 COOL/HEAT/…）
    assert parse("COOL").actions == (("mode", 1),)
    assert parse("cool").actions == (("mode", 1),)
    assert parse("HEAT").actions == (("mode", 4),)
    assert parse("DRY").actions == (("mode", 2),)
    assert parse("FAN").actions == (("mode", 3),)
    assert parse("AUTO").actions == (("mode", 0),)


def test_liang_yidian_is_a_cooler_step():
    # 设计 4.2 写的是「凉一点」，不是只有「凉快一点」
    assert parse("凉一点").actions == (("temp_step", -1),)
    assert parse("凉快一点").actions == (("temp_step", -1),)


def test_a_number_bound_by_a_wind_word_is_a_fan_step():
    # 设计 4.3：档 / 档位 / 风速 / 风 都把数字绑到风速，而不是温度
    assert parse("风速2").actions == (("fan", 2),)
    assert parse("2档位").actions == (("fan", 2),)
    assert parse("3风").actions == (("fan", 3),)


def test_full_width_input_is_parsed_like_half_width():
    # 设计 7「全角/大小写」：２４ 与 24 同义（归一化折叠全角）
    assert parse("调到２４度").actions == (("temp", 24),)
    assert parse("风速２档").actions == (("fan", 2),)


def test_low_two_degrees_without_the_diao_prefix():
    # 设计 7 逐字点名的例子
    assert parse("低两度").actions == (("temp_step", -2),)


def test_chat_that_merely_contains_a_power_character_is_not_a_command():
    # 反目标 1.4（随便说句话就把空调动了）。裸「开/关/停」只有当整句以它收尾
    # （可带语气词），或后面紧跟一个真动作时才算命令。
    for text in ("没关系", "开心", "开始吧", "开门", "关灯", "停车",
                 "开关灯", "关注一下", "开个玩笑", "关灯吧啦"):
        assert parse(text).kind == "unclear", text
        assert parse(text).actions == (), text


def test_words_that_also_mean_something_else_need_an_ac_context():
    # 这些词在普通话里到处都是，只有对着空调说才算命令
    for text in ("打开心扉", "打开窗户", "打开电视", "关闭窗口", "雨停了", "风停了"):
        assert parse(text).kind == "unclear", text
        assert parse(text).actions == (), text
    # 加上空调语境（称呼 / 设备名 / 后面紧跟真动作）就恢复成命令
    assert parse("打开空调").actions == (("power", 1),)
    assert parse("关闭空调").actions == (("power", 0),)
    assert parse("把空调打开").actions == (("power", 1),)
    assert parse("空调停了").actions == (("power", 0),)


def test_bare_power_words_still_work_on_their_own():
    assert parse("开").actions == (("power", 1),)
    assert parse("关").actions == (("power", 0),)
    assert parse("停").actions == (("power", 0),)
    assert parse("开！").actions == (("power", 1),)
    assert parse("冷了").kind == "unclear"


def test_bare_power_word_with_trailing_particles_still_works():
    assert parse("关了").actions == (("power", 0),)
    assert parse("开吧").actions == (("power", 1),)
    assert parse("开着").actions == (("power", 1),)
    assert parse("空调关了").actions == (("power", 0),)


def test_bare_power_word_before_a_real_action_still_works():
    assert parse("开制冷24度").actions == (("power", 1), ("mode", 1), ("temp", 24))
    assert parse("关空调").actions == (("power", 0),)
    assert parse("开到最大风").actions == (("power", 1), ("fan", 3))


def test_a_relative_step_needs_a_measure_word():
    # 「身高180」「血压高140」不能被读成升/降温
    for text in ("身高180", "血压高140", "热18", "冷3", "高140"):
        assert parse(text).kind == "unclear", text
        assert parse(text).actions == (), text


def test_a_relative_step_with_a_measure_word_still_works():
    assert parse("冷3度").actions == (("temp_step", -3),)
    assert parse("高2度").actions == (("temp_step", 2),)
    assert parse("调低").actions == (("temp_step", -1),)
    assert parse("调高").actions == (("temp_step", 1),)
    assert parse("调低一点").actions == (("temp_step", -1),)


def test_adjacent_chinese_digits_do_not_crash():
    # _NUMBER 贪婪匹配连续的汉字数字，「两三度」是一整块 —— 不能抛异常
    assert parse("调低两三度").actions == (("temp_step", -2),)
    assert parse("调到十度").actions == (("temp", 10),)


def test_a_temp_verb_does_not_swallow_a_fan_step():
    # 设计 4.3：档 / 档位 / 风 把数字绑到风速，即使前面有温度动词
    assert parse("调到2档").actions == (("fan", 2),)
    assert parse("设成2档").actions == (("fan", 2),)
    assert parse("风速调到2档").actions == (("fan", 2),)
    assert parse("开到2档").actions == (("power", 1), ("fan", 2),)


def test_a_polite_command_is_still_a_command():
    # 别把「开空调好吗」吞成一次状态查询 —— 只有「…了吗/了呢」才是问状态
    assert parse("开空调好吗").actions == (("power", 1),)
    assert parse("调到24度好吗").actions == (("temp", 24),)
    assert parse("帮我开一下好吗").actions == (("power", 1),)
    assert parse("开机了吗").kind == "query"
    assert parse("关机了吗").kind == "query"


# ---------------------------------------------------------- new v1.2 categories
# 设计 §9.4：历史 / 夜间 / 天气 / 预设这四类**只认整句精确匹配**。
# 「夜间状态」里含「状态」，所以它们必须排在 query 前面；但一句只是提到它们
# 的话，仍然按 unclear 处理，绝不动空调。


def test_history_windows_are_recognised():
    for word, window in (("当前", "current"), ("今夜", "tonight"), ("一天", "day"),
                         ("一周", "week"), ("全部", "all")):
        intent = parse(word)
        assert (intent.kind, intent.hint) == ("history", window), word


def test_night_switch_and_status():
    assert (parse("开夜间").kind, parse("开夜间").hint) == ("night", "on")
    assert (parse("关夜间").kind, parse("关夜间").hint) == ("night", "off")
    assert (parse("夜间状态").kind, parse("夜间状态").hint) == ("night", "status")


def test_a_bare_night_word_opens_the_settings_card_instead_of_toggling():
    # 设计 §7.3：菜单「其他 ▸ 夜间」发的是「夜间」，它要弹出夜间设置卡。
    # 但它说不出开还是关 —— 所以只开卡，绝不动开关。
    assert (parse("夜间").kind, parse("夜间").hint) == ("night", "open")
    assert parse("夜间").actions == ()


def test_the_menu_history_word_shows_the_daily_summary():
    # 设计 §7.3 把「历史」也放进菜单，而菜单是「显示名 = 发送文字」，
    # 所以「历史」必须是个能认出来的词（默认看「一天」）。
    assert (parse("历史").kind, parse("历史").hint) == ("history", "day")


def test_night_status_is_not_swallowed_by_the_query_branch():
    # 「夜间状态」里有个「状态」，但它问的是夜间，不是空调当前状态。
    assert parse("夜间状态").kind == "night"


def test_weather_words_are_recognised():
    for word in ("天气", "今天天气", "建议", "今天开多少度"):
        assert parse(word).kind == "weather", word


def test_the_preset_entry_word_opens_the_card():
    assert (parse("预设").kind, parse("预设").hint) == ("preset", "open")
    assert parse("设置预设").hint == "open"


def test_a_preset_is_applied_only_with_a_verb_prefix():
    assert (parse("用夏天").kind, parse("用夏天").hint) == ("preset", "apply:夏天")
    assert parse("套用睡觉").hint == "apply:睡觉"


def test_a_bare_preset_name_is_never_an_action():
    # 纯名字（「夏天」）不认 —— 预知名字就执行太容易误触发。
    assert parse("夏天").kind == "unclear"


def test_the_new_categories_never_move_the_ac():
    for word in ("当前", "今夜", "开夜间", "天气", "预设", "用夏天"):
        assert parse(word).actions == (), word
