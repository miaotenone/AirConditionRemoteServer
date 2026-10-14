"""Unit tests for the Service-level text dispatch -- no network, no broker.

The MQTT bridge and the Feishu client both build their objects without opening a
connection, so the whole dispatch path (owner check, dedupe, optimistic update,
card bookkeeping, receipt) is pinned here instead of against the real thing.
"""

from app.config import Settings
from app.main import Service
from app.state import Presence, merge_actions


class FakeFeishu:
    def __init__(self):
        self.replies = []
        self.sent = []
        self.patched = []

    def reply_text(self, message_id, text):
        self.replies.append((message_id, text))
        return True

    def send_card(self, chat_id, card):
        self.sent.append(chat_id)
        return "om_card"

    def patch_card(self, message_id, card):
        self.patched.append(message_id)
        return True


def build_service(tmp_path, **overrides):
    values = {"feishu_app_id": "cli_test", "feishu_app_secret": "secret",
              "owner_open_id": "ou_test", "db_path": str(tmp_path / "t.sqlite3")}
    values.update(overrides)
    service = Service(Settings(_env_file=None, **values))
    service.feishu = FakeFeishu()
    # 这些测试考的是文本分派，不是在线判定 —— 假装设备在线，免得多出「离线」后缀
    service.presence = Presence(online=True)
    return service


def test_text_command_goes_optimistic_and_queues_exactly_one_action(tmp_path):
    service = build_service(tmp_path)
    service._on_text_message("ou_test", "oc_1", "om_1", "开机")
    assert service.view.power == 1
    assert service.pending is True
    assert service._actions.qsize() == 1
    assert service.feishu.replies == [("om_1", "✅ 已开机 · 制冷 · 24℃ · 自动风")]


def test_a_compound_command_queues_one_action_per_field(tmp_path):
    service = build_service(tmp_path)
    service._on_text_message("ou_test", "oc_1", "om_1", "开机制冷24度")
    assert service.view.power == 1
    assert service.view.mode == 1
    assert service.view.temp == 24
    assert service._actions.qsize() == 3


def test_a_compound_command_becomes_one_mqtt_payload(tmp_path):
    # 反目标「刷屏」：一条消息 = 一条 ac/cmd，三个字段合并发送
    service = build_service(tmp_path)
    service._on_text_message("ou_test", "oc_1", "om_1", "开机制冷24度")
    burst = []
    while not service._actions.empty():
        burst.append(service._actions.get_nowait())
    assert merge_actions(service.state, burst) == {"power": 1, "mode": 1, "temp": 24}


def test_suggest_never_touches_the_aircon(tmp_path):
    service = build_service(tmp_path)
    service._on_text_message("ou_test", "oc_1", "om_1", "太热了")
    assert service._actions.empty()
    assert service.view.power == 0
    assert service.pending is False
    assert service.feishu.replies == [
        ("om_1", "看着有点热？说「制冷 22 度」，或点卡片上的「制冷」。")]


def test_a_redelivered_message_is_ignored(tmp_path):
    service = build_service(tmp_path)
    service._on_text_message("ou_test", "oc_1", "om_1", "调低两度")
    service._on_text_message("ou_test", "oc_1", "om_1", "调低两度")
    assert service._actions.qsize() == 1
    assert len(service.feishu.replies) == 1
    assert service.view.temp == 22


def test_three_different_messages_are_all_queued(tmp_path):
    # 设计 5.3 / 验收 #11：去重只管重投，不管重复发送
    service = build_service(tmp_path)
    for index in range(3):
        service._on_text_message("ou_test", "oc_1", f"om_{index}", "调低两度")
    assert service._actions.qsize() == 3
    assert service.view.temp == 18


def test_non_owner_is_ignored_completely(tmp_path):
    service = build_service(tmp_path)
    service._on_text_message("ou_other", "oc_1", "om_1", "开机")
    assert service.feishu.replies == []
    assert service._actions.empty()
    assert service.view.power == 0


def test_the_card_is_posted_once_and_patched_afterwards(tmp_path):
    service = build_service(tmp_path)
    service._on_text_message("ou_test", "oc_1", "om_1", "开机")   # 没卡片 -> 补一张
    service._on_text_message("ou_test", "oc_1", "om_2", "关机")   # 有卡片 -> 只 patch
    assert service.feishu.sent == ["oc_1"]
    assert service.feishu.patched == ["om_card"]
    assert len(service.feishu.replies) == 2


def test_a_query_gets_a_receipt_without_moving_anything(tmp_path):
    service = build_service(tmp_path)
    service._on_text_message("ou_test", "oc_1", "om_1", "状态")
    assert service._actions.empty()
    assert service.feishu.replies == [
        ("om_1", "现在 关机 · 制冷 · 24℃ · 自动风 · 🟢 在线（更新于 --:--:--）")]


def test_card_commands_still_post_the_card_without_a_receipt(tmp_path):
    service = build_service(tmp_path)
    service._on_text_message("ou_test", "oc_1", "om_1", "/ac")
    assert service.feishu.sent == ["oc_1"]
    assert service.feishu.replies == []
    assert service._actions.empty()


def test_disabling_text_control_restores_the_old_behaviour(tmp_path):
    service = build_service(tmp_path, text_control_enabled=False)
    service._on_text_message("ou_test", "oc_1", "om_1", "开机")
    assert service.feishu.replies == []
    assert service._actions.empty()
    assert service.view.power == 0


def test_relative_steps_are_reported_once_each(tmp_path):
    # 验收 #11 的回执部分：22/20/18。曾出现过 ac/cmd {"temp":28} 配一条说 26℃
    # 的回执 —— 回执在动作已应用的视图上又算了一遍 temp_step。
    service = build_service(tmp_path)
    for index in range(3):
        service._on_text_message("ou_test", "oc_1", f"om_{index}", "调低两度")
    assert [text for _, text in service.feishu.replies] == [
        "✅ 已关机 · 制冷 · 22℃ · 自动风（当前关机，开机后生效）",
        "✅ 已关机 · 制冷 · 20℃ · 自动风（当前关机，开机后生效）",
        "✅ 已关机 · 制冷 · 18℃ · 自动风（当前关机，开机后生效）",
    ]
