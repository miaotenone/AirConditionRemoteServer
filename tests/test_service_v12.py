"""Service-level tests for the v1.2 wiring -- no network, no broker.

`FeishuClient` and `MqttBridge` both build their objects without connecting, so
the whole dispatch path (card-by-kind bookkeeping, form submits, the history /
night / weather / preset branches, and the tick helpers) is pinned here.
"""

import time
from datetime import datetime, timedelta

import pytest

from app.cards import SETTINGS_TITLE
from app.config import Settings
from app.env import EnvReading
from app.main import Service
from app.preset import Preset
from app.state import AC_FAN, AC_MODE, AcState, Presence
from app.weather import WeatherNow, recommend


class FakeFeishu:
    def __init__(self):
        self.replies = []
        self.sent = []
        self.sent_text = []
        self.patched = []
        self.cards = {}

    def reply_text(self, message_id, text):
        self.replies.append((message_id, text))
        return True

    def send_text(self, chat_id, text):
        self.sent_text.append((chat_id, text))
        return True

    def send_card(self, chat_id, card):
        self.sent.append(chat_id)
        message_id = f"om_card_{len(self.sent)}"
        self.cards[message_id] = card
        return message_id

    def patch_card(self, message_id, card):
        self.patched.append(message_id)
        self.cards[message_id] = card
        return True

    def recall_card(self, message_id):
        # 菜单入口的卡片要「移动到最后」：先发新的，再撤回旧的（设计 D22）。
        # 置 recall_succeeds=False 模拟撤回窗口过了。
        if getattr(self, "recalled", None) is None:
            self.recalled = []
        self.recalled.append(message_id)
        return getattr(self, "recall_succeeds", True)


def build_service(tmp_path, **overrides):
    values = {"feishu_app_id": "cli_test", "feishu_app_secret": "secret",
              "owner_open_id": "ou_test", "db_path": str(tmp_path / "t.sqlite3")}
    values.update(overrides)
    service = Service(Settings(_env_file=None, **values))
    service.feishu = FakeFeishu()
    service.presence = Presence(online=True)
    # Run the "slow work" seam inline so dispatch stays deterministic; the
    # threading itself is pinned by its own test below.
    service._run_async = lambda fn, *args: fn(*args)
    return service


def visible_text(card):
    """Every string a user can read on the card (markdown + button labels)."""
    out = []

    def walk(elements):
        for element in elements:
            if element.get("tag") == "markdown":
                out.append(element.get("content", ""))
            if element.get("tag") == "button":
                out.append((element.get("text") or {}).get("content", ""))
            for column in element.get("columns") or []:
                walk(column.get("elements") or [])
            if element.get("tag") == "form":
                walk(element.get("elements") or [])

    walk(card["body"]["elements"])
    return "\n".join(out)


# ------------------------------------------------------------------ ac/env

def test_a_retained_env_replay_is_ignored(tmp_path):
    # 设计 18.6：重启后 broker 会重放 retained，那不是「刚测到」，不能采信。
    service = build_service(tmp_path)
    service._on_mqtt_env({"temp": 26, "humidity": 50, "ok": True}, True)
    assert service.env is None


def test_a_fresh_env_reading_is_kept(tmp_path):
    service = build_service(tmp_path)
    service._on_mqtt_env({"temp": 26, "humidity": 50, "ok": True}, False)
    assert service.env == EnvReading(temp=26, humidity=50, ok=True)
    assert service.env_at > 0


def test_a_failed_read_is_not_recorded_as_history(tmp_path):
    service = build_service(tmp_path)
    service.env = EnvReading(ok=False)
    service.env_at = time.time()
    service._record_env(time.time() + 10_000)
    assert service.store.env_samples() == []


def test_a_good_read_is_recorded_after_the_interval(tmp_path):
    service = build_service(tmp_path, dht_record_s=300)
    service.env = EnvReading(temp=26, humidity=50, ok=True)
    service.env_at = time.time()
    service._record_env(time.time() + 301)
    assert [row.temp for row in service.store.env_samples()] == [26]


# --------------------------------------------------------------- text branches

def test_history_says_so_when_nothing_has_been_recorded(tmp_path):
    service = build_service(tmp_path)
    service._on_text_message("ou_test", "oc_1", "om_1", "一周")
    assert "还没有" in service.feishu.replies[0][1]


def test_history_reports_the_recorded_room(tmp_path):
    service = build_service(tmp_path)
    service.store.add_env_sample(time.time(), 26, 50, True)
    service._on_text_message("ou_test", "oc_1", "om_1", "一天")
    assert "室温 26℃" in service.feishu.replies[0][1]


def test_history_never_moves_the_aircon(tmp_path):
    service = build_service(tmp_path)
    service._on_text_message("ou_test", "oc_1", "om_1", "全部")
    assert service._actions.empty()


def test_the_night_switch_persists_and_replies(tmp_path):
    service = build_service(tmp_path)
    service._on_text_message("ou_test", "oc_1", "om_1", "开夜间")
    assert service._night_enabled() is True
    assert "已打开" in service.feishu.replies[0][1]

    service._on_text_message("ou_test", "oc_1", "om_2", "夜间状态")
    assert "已开" in service.feishu.replies[1][1]

    service._on_text_message("ou_test", "oc_1", "om_3", "关夜间")
    assert service._night_enabled() is False


def test_a_status_query_does_not_flip_the_night_switch(tmp_path):
    service = build_service(tmp_path)
    service._on_text_message("ou_test", "oc_1", "om_1", "夜间状态")
    assert service._night_enabled() is False
    assert service.feishu.sent == []          # nothing was posted either


def test_the_night_menu_entry_opens_the_settings_card(tmp_path):
    # 设计 §7.3：菜单「其他 ▸ 夜间」= 夜间设置卡入口（不发命令、不改开关）。
    service = build_service(tmp_path)
    service._on_text_message("ou_test", "oc_1", "om_1", "夜间")
    assert service._night_enabled() is False
    assert service.store.get_card("ou_test", "settings")
    assert "夜间" in service.feishu.replies[0][1]


def test_the_history_menu_entry_answers_like_a_day_query(tmp_path):
    service = build_service(tmp_path)
    service.store.add_env_sample(time.time(), 25, 50, True)
    service._on_text_message("ou_test", "oc_1", "om_1", "历史")
    assert "室温 25℃" in service.feishu.replies[0][1]


# ------------------------------------------------- menu taps answer at the bottom
# 2026-10-15: 点菜单「预设」时服务端把老卡「原地 patch」。卡在聊天上方，用户站在
# 最下面，回执却写着「预设卡片 👇」——看起来就是「什么都没发生」。菜单入口一律
# 重新发一张到最新位置（跟 D22 召唤遥控卡同一套动作）。

def test_a_preset_menu_tap_posts_a_fresh_card_at_the_bottom(tmp_path):
    service = build_service(tmp_path)
    service.store.save_preset(Preset(id=1, name="夏天", temp=26))

    service._on_text_message("ou_test", "oc_1", "om_1", "预设")
    first = service.store.get_card("ou_test", "preset")
    assert first, "第一次点也要出卡"

    service._on_text_message("ou_test", "oc_1", "om_2", "预设")
    assert service.store.get_card("ou_test", "preset") != first
    assert len(service.feishu.sent) == 2, "第二次是新发一张，不是原地 patch"
    assert service.feishu.patched == []


def test_a_menu_tap_retires_the_previous_card(tmp_path):
    service = build_service(tmp_path)
    service._on_text_message("ou_test", "oc_1", "om_1", "夜间")
    first = service.store.get_card("ou_test", "settings")

    service._on_text_message("ou_test", "oc_1", "om_2", "夜间")
    assert service.feishu.recalled == [first], "旧卡撤回了，聊天里只留最新那张"


def test_a_menu_tap_leaves_a_notice_when_the_old_card_cannot_be_recalled(tmp_path):
    service = build_service(tmp_path)
    service._on_text_message("ou_test", "oc_1", "om_1", "夜间")
    first = service.store.get_card("ou_test", "settings")
    service.feishu.recall_succeeds = False            # 超过撤回窗口

    service._on_text_message("ou_test", "oc_1", "om_2", "夜间")
    assert first in service.feishu.patched
    notice = service.feishu.cards[first]
    assert "最新位置" in notice["body"]["elements"][0]["content"]
    assert notice["header"]["title"]["content"] == SETTINGS_TITLE


def test_the_weather_menu_tap_posts_a_fresh_card_too(tmp_path):
    service = build_service(tmp_path)
    service._weather_snapshot = lambda: (WeatherNow(36, 50, -1), recommend(36))

    service._on_text_message("ou_test", "oc_1", "om_1", "天气")
    first = service.store.get_card("ou_test", "weather")
    service._on_text_message("ou_test", "oc_1", "om_2", "天气")
    assert service.store.get_card("ou_test", "weather") != first


def test_a_card_button_still_answers_in_place(tmp_path):
    """卡片按钮是「手指正指着这张卡」，必须原地更新，不能又发一张。"""
    service = build_service(tmp_path)
    service._on_text_message("ou_test", "oc_1", "om_1", "预设")
    card_id = service.store.get_card("ou_test", "preset")
    sent_before = len(service.feishu.sent)

    service._on_card_action("ou_test", "oc_1", card_id,
                            {"a": "preset_new", "v": 0}, None)
    assert card_id in service.feishu.patched
    assert len(service.feishu.sent) == sent_before, "没有多发消息"


def test_the_weather_branch_replies_and_shows_a_card(tmp_path):
    service = build_service(tmp_path)
    service._weather_snapshot = lambda: (WeatherNow(36, 50, -1), recommend(36))
    service._on_text_message("ou_test", "oc_1", "om_1", "天气")
    assert "制冷 26℃" in service.feishu.replies[0][1]
    assert service.store.get_card("ou_test", "weather")


def test_the_weather_branch_says_so_when_the_fetch_fails(tmp_path):
    service = build_service(tmp_path)
    service._weather_snapshot = lambda: (None, None)
    service._on_text_message("ou_test", "oc_1", "om_1", "天气")
    assert "没拿到" in service.feishu.replies[0][1]


def test_the_weather_fetch_never_blocks_the_feishu_callback(tmp_path):
    """Slow work must not run on the SDK's receive thread.

    build_service() replaces the async seam for determinism, so this test puts
    the real one back and proves _on_text_message returns before the fetch
    finishes -- otherwise one "天气" would stall every other message and button.
    """
    service = build_service(tmp_path)
    service._run_async = Service._run_async.__get__(service, Service)

    def slow_snapshot():
        time.sleep(0.4)
        return (WeatherNow(36, 50, -1), recommend(36))

    service._weather_snapshot = slow_snapshot

    started = time.monotonic()
    service._on_text_message("ou_test", "oc_1", "om_1", "天气")
    elapsed = time.monotonic() - started
    assert elapsed < 0.2, f"the callback blocked for {elapsed:.2f}s"

    for _ in range(100):
        if service.feishu.replies:
            break
        time.sleep(0.02)
    assert "制冷 26℃" in service.feishu.replies[0][1]


def test_a_preset_applies_immediately_by_text(tmp_path):
    service = build_service(tmp_path)
    service.store.save_preset(_preset(name="夏天", temp=26))
    service._on_text_message("ou_test", "oc_1", "om_1", "用夏天")
    assert service._actions.qsize() == 4
    assert "已套用「夏天」" in service.feishu.replies[0][1]


def test_an_unknown_preset_name_is_reported_not_guessed(tmp_path):
    service = build_service(tmp_path)
    service._on_text_message("ou_test", "oc_1", "om_1", "用冬天")
    assert service._actions.empty()
    assert "没有叫「冬天」的预设" in service.feishu.replies[0][1]


def test_a_future_timer_books_a_single_run(tmp_path):
    service = build_service(tmp_path)
    preset = _preset(name="睡觉", timer_enabled=True, timer_at=_soon())
    service.store.save_preset(preset)
    service._on_text_message("ou_test", "oc_1", "om_1", "用睡觉")
    assert service._actions.empty()                 # nothing runs now ...
    (job,) = service.store.list_schedules()
    assert job.payload["preset_id"] == service.store.get_preset_by_name("睡觉").id
    assert "已预约" in service.feishu.replies[0][1]


def test_a_past_timer_runs_now(tmp_path):
    service = build_service(tmp_path)
    service.store.save_preset(_preset(name="晚安", timer_enabled=True, timer_at=_past()))
    service._on_text_message("ou_test", "oc_1", "om_1", "用晚安")
    assert service._actions.qsize() == 4


# --------------------------------------------------------------- card kinds

def test_each_card_kind_owns_its_own_message(tmp_path):
    # 设计 18.1：预设卡不能把遥控器卡顶掉。
    service = build_service(tmp_path)
    service._on_text_message("ou_test", "oc_1", "om_1", "设置卡片")   # remote
    remote_id = service.store.get_card("ou_test", "remote")
    service._on_text_message("ou_test", "oc_1", "om_2", "预设")       # preset card
    assert service.store.get_card("ou_test", "preset")
    assert service.store.get_card("ou_test", "remote") == remote_id


def test_the_preset_entry_posts_the_list_card(tmp_path):
    service = build_service(tmp_path)
    service.store.save_preset(_preset(name="夏天"))
    service._on_text_message("ou_test", "oc_1", "om_1", "预设")
    preset_card = service.feishu.cards[service.store.get_card("ou_test", "preset")]
    assert "夏天" in visible_text(preset_card)


# ------------------------------------------------------------- ui actions

def test_a_form_submit_creates_a_preset(tmp_path):
    service = build_service(tmp_path)
    service._on_card_action("ou_test", "oc_1", "om_1",
                            {"a": "preset_save"}, None,
                            {"name": "夏天", "power": "1", "mode": "1",
                             "temp": "26", "fan": "0"}, "submit")
    (saved,) = service.store.list_presets()
    assert (saved.name, saved.temp, saved.power) == ("夏天", 26, 1)


def test_a_form_submit_without_a_name_asks_again(tmp_path):
    service = build_service(tmp_path)
    service._on_card_action("ou_test", "oc_1", "om_1",
                            {"a": "preset_save"}, None,
                            {"name": "", "temp": "26"}, "submit")
    assert service.store.list_presets() == []
    assert "先起个名字" in visible_text(service.feishu.cards["om_1"])


def test_a_form_submit_keeps_a_picked_time(tmp_path):
    service = build_service(tmp_path)
    service._on_card_action("ou_test", "oc_1", "om_1",
                            {"a": "preset_save"}, None,
                            {"name": "睡觉", "temp": "26", "at": "22:30 +0800"}, "submit")
    (saved,) = service.store.list_presets()
    assert saved.timer_enabled is True
    assert saved.timer_at.startswith("22:30")


def test_applying_from_the_card_enqueues_and_reports_on_the_card(tmp_path):
    service = build_service(tmp_path)
    preset_id = service.store.save_preset(_preset(name="夏天", temp=26))
    service._on_card_action("ou_test", "oc_1", "om_1",
                            {"a": "preset_apply", "v": preset_id}, None)
    assert service._actions.qsize() == 4
    assert "已套用" in visible_text(service.feishu.cards["om_1"])


def test_deleting_from_the_card_removes_the_preset(tmp_path):
    service = build_service(tmp_path)
    preset_id = service.store.save_preset(_preset(name="夏天"))
    service._on_card_action("ou_test", "oc_1", "om_1",
                            {"a": "preset_delete", "v": preset_id}, None)
    assert service.store.list_presets() == []


def test_the_night_toggle_button_flips_the_setting(tmp_path):
    service = build_service(tmp_path)
    service._on_card_action("ou_test", "oc_1", "om_1",
                            {"a": "night_toggle", "v": 1}, None)
    assert service._night_enabled() is True


def test_saving_settings_persists_them(tmp_path):
    service = build_service(tmp_path)
    service._on_card_action("ou_test", "oc_1", "om_1",
                            {"a": "settings_save"}, None,
                            {"push_time": "08:00", "night_start": "22:30",
                             "night_end": "06:30", "night_target": "25"}, "submit")
    assert (service._push_time(), service._night_start(), service._night_end(),
            service._night_target()) == ("08:00", "22:30", "06:30", 25)


def test_the_weather_exec_button_sends_the_suggestion(tmp_path):
    service = build_service(tmp_path)
    service._last_weather = (WeatherNow(36, 50, -1), recommend(36))
    service._on_card_action("ou_test", "oc_1", "om_1",
                            {"a": "weather_exec", "v": 0}, None)
    queued = _queued(service)
    assert ("mode", AC_MODE["cool"]) in queued
    assert ("temp", 26) in queued
    assert "已按建议执行" in visible_text(service.feishu.cards["om_1"])


def test_an_ac_button_still_works_through_the_same_callback(tmp_path):
    service = build_service(tmp_path)
    service._on_card_action("ou_test", "oc_1", "om_1", {"a": "power", "v": 1}, None)
    assert service.view.power == 1
    assert service._actions.qsize() == 1


def test_an_ac_button_yields_the_night_loop(tmp_path):
    service = build_service(tmp_path)
    service._on_card_action("ou_test", "oc_1", "om_1", {"a": "power", "v": 1}, None)
    assert service._loop.manual_until > time.time()


# ---------------------------------------------------------------- the tick

def test_the_night_loop_acts_when_the_room_is_hot(tmp_path):
    service = build_service(tmp_path)
    service.store.set_setting("night_enabled", "1")
    service.store.set_setting("night_start", "00:00")
    service.store.set_setting("night_end", "23:59")
    service.view = AcState(power=1, mode=AC_MODE["auto"], temp=22, fan=AC_FAN["high"])
    service.env = EnvReading(temp=29, humidity=50, ok=True)
    service.env_at = time.time()
    service._night_step(time.time())
    assert ("temp", 26) in _queued(service)
    assert service._loop.last_action_at > 0


def test_the_night_loop_stays_out_when_it_is_off(tmp_path):
    service = build_service(tmp_path)
    service.env = EnvReading(temp=35, humidity=50, ok=True)
    service.env_at = time.time()
    service._night_step(time.time())
    assert service._actions.empty()


def test_the_heartbeat_goes_out_on_schedule(tmp_path):
    service = build_service(tmp_path, hb_interval_s=30)
    calls = []
    service.mqtt.publish_heartbeat = lambda: calls.append(1) or True
    service._heartbeat(1000.0)
    service._heartbeat(1010.0)
    service._heartbeat(1031.0)
    assert len(calls) == 2


def test_a_due_schedule_fires_once_and_is_marked_done(tmp_path):
    service = build_service(tmp_path)
    preset_id = service.store.save_preset(_preset(name="夏天"))
    service.store.add_schedule("preset", {"preset_id": preset_id}, time.time())
    service._run_due_schedules(time.time())
    assert service._actions.qsize() == 4
    assert service.store.list_schedules(status="pending") == []
    assert service.store.list_schedules()[0].status == "done"


def test_a_badly_missed_schedule_is_dropped_not_fired(tmp_path):
    service = build_service(tmp_path)
    preset_id = service.store.save_preset(_preset(name="睡觉"))
    service.store.add_schedule("preset", {"preset_id": preset_id},
                               time.time() - 3600)
    service._run_due_schedules(time.time())
    assert service._actions.empty()
    assert service.store.list_schedules()[0].status == "dropped"


def test_the_daily_weather_push_fires_within_the_minute_window(tmp_path):
    service = build_service(tmp_path)
    service.store.set_setting("chat_id", "oc_1")
    pushed = []
    service._push_weather = lambda chat_id: pushed.append(chat_id)
    now = datetime.now().replace(second=0, microsecond=0)
    service.store.set_setting("push_time", now.strftime("%H:%M"))
    service._maybe_push_weather(now.timestamp() + 5)
    assert pushed == ["oc_1"]
    # ... and only once for that occurrence
    service._maybe_push_weather(now.timestamp() + 20)
    assert pushed == ["oc_1"]


# --------------------------------------------------------- device events

def test_a_retained_failsafe_replay_is_ignored(tmp_path):
    # 设计 18.8：重启后 broker 会重放 retained 的 ac/event，那是历史不是新闻。
    service = build_service(tmp_path)
    service._on_mqtt_event({"type": "failsafe_off", "ts": time.time(),
                            "prev": {"power": 1, "mode": 1, "temp": 24, "fan": 0}},
                           True)
    assert service._actions.empty()
    assert service.view.power == 0          # untouched: still the default


def test_a_failsafe_event_without_a_timestamp_is_ignored(tmp_path):
    # 固件在 NTP 同步之前不会发事件；没有 ts 就没法判断新旧，一律不信。
    service = build_service(tmp_path)
    service._on_mqtt_event({"type": "failsafe_off",
                            "prev": {"power": 1, "mode": 1, "temp": 24, "fan": 0}},
                           False)
    assert service._actions.empty()


def test_a_stale_failsafe_event_is_ignored(tmp_path):
    service = build_service(tmp_path)
    service._on_mqtt_event({"type": "failsafe_off", "ts": time.time() - 3600,
                            "prev": {"power": 1, "mode": 1, "temp": 24, "fan": 0}},
                           False)
    assert service._actions.empty()


def test_a_fresh_failsafe_event_restores_the_previous_state(tmp_path):
    service = build_service(tmp_path)
    service.store.set_setting("chat_id", "oc_1")
    service.view = AcState(power=1, mode=AC_MODE["cool"], temp=24, fan=AC_FAN["auto"])
    service._on_mqtt_event({"type": "failsafe_off", "ts": time.time(),
                            "prev": {"power": 1, "mode": 1, "temp": 22, "fan": 1}},
                           False)
    queued = _queued(service)
    assert ("power", 1) in queued
    assert ("temp", 22) in queued
    assert ("fan", 1) in queued
    assert service.feishu.sent_text


def test_a_failsafe_event_for_an_ac_that_was_off_does_not_reopen_it(tmp_path):
    # 本来就关机，失联自动关机什么都没改变，不该"恢复"成开机。
    service = build_service(tmp_path)
    service._on_mqtt_event({"type": "failsafe_off", "ts": time.time(),
                            "prev": {"power": 0, "mode": 1, "temp": 24, "fan": 0}},
                           False)
    assert service._actions.empty()


def test_an_unknown_device_event_is_ignored(tmp_path):
    service = build_service(tmp_path)
    service._on_mqtt_event({"type": "something_else", "ts": time.time()}, False)
    assert service._actions.empty()


# ------------------------------------------------------------------ helpers


def _preset(**overrides):
    from app.preset import Preset

    values = dict(name="x", power=1, mode=AC_MODE["cool"], temp=24,
                  fan=AC_FAN["auto"])
    values.update(overrides)
    return Preset(**values)


def _queued(service):
    items = []
    while not service._actions.empty():
        items.append(service._actions.get_nowait())
    return items


def _soon():
    """A preset time a few minutes ahead, today (skipped near midnight)."""
    now = datetime.now()
    ahead = now + timedelta(minutes=5)
    if ahead.day != now.day:
        pytest.skip("the time would wrap past midnight")
    return ahead.strftime("%H:%M")


def _past():
    """A preset time that has already passed today (skipped just after midnight)."""
    if datetime.now().hour == 0:
        pytest.skip("no time earlier today")
    return "00:01"
