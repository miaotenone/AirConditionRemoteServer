"""Unit tests for app.store -- the owner -> card message_id mapping.

Single private owner in v1 (spec 5.1), so it is one row, but keyed by owner so a
group/whitelist later does not need a schema change.
"""

import sqlite3

from app.preset import Preset
from app.store import CardStore


def test_store_has_no_card_before_anything_is_sent():
    assert CardStore(":memory:").get_message_id("ou_x") is None


def test_store_roundtrips_the_card_message_id():
    store = CardStore(":memory:")
    store.set_message_id("ou_x", "om_1")
    assert store.get_message_id("ou_x") == "om_1"


def test_store_replaces_the_previous_card_for_the_same_owner():
    store = CardStore(":memory:")
    store.set_message_id("ou_x", "om_1")
    store.set_message_id("ou_x", "om_2")
    assert store.get_message_id("ou_x") == "om_2"


def test_store_keeps_owners_separate():
    store = CardStore(":memory:")
    store.set_message_id("ou_a", "om_a")
    store.set_message_id("ou_b", "om_b")
    assert store.get_message_id("ou_a") == "om_a"
    assert store.get_message_id("ou_b") == "om_b"


def test_store_survives_reopening_the_database_file(tmp_path):
    path = tmp_path / "ac-bot.sqlite3"
    CardStore(path).set_message_id("ou_x", "om_1")
    assert CardStore(path).get_message_id("ou_x") == "om_1"


def test_store_clears_a_record():
    store = CardStore(":memory:")
    store.set_message_id("ou_x", "om_1")
    store.clear_message_id("ou_x")
    assert store.get_message_id("ou_x") is None


def test_mark_seen_is_true_only_the_first_time():
    store = CardStore(":memory:")
    assert store.mark_seen("om_1") is True
    assert store.mark_seen("om_1") is False


def test_mark_seen_keeps_ids_separate():
    store = CardStore(":memory:")
    assert store.mark_seen("om_1") is True
    assert store.mark_seen("om_2") is True


def test_mark_seen_survives_reopening_the_database_file(tmp_path):
    path = tmp_path / "ac-bot.sqlite3"
    CardStore(path).mark_seen("om_1")
    assert CardStore(path).mark_seen("om_1") is False


def test_mark_seen_ignores_an_empty_id():
    # 空 id 说明上游没给 message_id，不能把它当成「已经见过」
    store = CardStore(":memory:")
    assert store.mark_seen("") is True
    assert store.mark_seen("") is True


# --------------------------------------------------------------- cards by kind
# Design 18.1: preset / settings / weather cards each get their own message,
# so the remote pointer can no longer be the only thing we remember.


def test_cards_are_addressed_by_kind():
    store = CardStore(":memory:")
    store.set_card("ou_x", "preset", "om_p")
    store.set_card("ou_x", "remote", "om_r")
    assert store.get_card("ou_x", "preset") == "om_p"
    assert store.get_card("ou_x", "remote") == "om_r"


def test_get_message_id_is_the_remote_card():
    store = CardStore(":memory:")
    store.set_card("ou_x", "remote", "om_r")
    assert store.get_message_id("ou_x") == "om_r"
    store.set_message_id("ou_x", "om_r2")
    assert store.get_card("ou_x", "remote") == "om_r2"


def test_clearing_one_kind_leaves_the_others():
    store = CardStore(":memory:")
    store.set_card("ou_x", "preset", "om_p")
    store.set_card("ou_x", "remote", "om_r")
    store.clear_card("ou_x", "preset")
    assert store.get_card("ou_x", "preset") is None
    assert store.get_card("ou_x", "remote") == "om_r"


def test_a_legacy_single_row_card_table_is_migrated(tmp_path):
    # 旧库只有 card(owner, message_id)；升级后那一行必须变成 remote 卡片，
    # 否则重启会以为「还没有遥控器卡片」，把聊天刷出一堆新卡。
    path = tmp_path / "old.sqlite3"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE card (owner TEXT PRIMARY KEY, message_id TEXT NOT NULL)")
    con.execute("INSERT INTO card VALUES ('ou_x', 'om_old')")
    con.commit()
    con.close()

    store = CardStore(path)
    assert store.get_card("ou_x", "remote") == "om_old"
    assert store.get_card("ou_x", "preset") is None


# -------------------------------------------------------------------- presets


def test_presets_roundtrip():
    store = CardStore(":memory:")
    preset_id = store.save_preset(Preset(name="夏天", power=1, mode=1, temp=26, fan=0))
    got = store.get_preset(preset_id)
    assert got is not None
    assert (got.name, got.power, got.mode, got.temp, got.fan) == ("夏天", 1, 1, 26, 0)
    assert got.id == preset_id


def test_preset_names_are_unique_and_overwrite():
    store = CardStore(":memory:")
    store.save_preset(Preset(name="夏天", temp=26))
    store.save_preset(Preset(name="夏天", temp=20))
    assert len(store.list_presets()) == 1
    assert store.get_preset_by_name("夏天").temp == 20


def test_a_preset_is_updated_by_id():
    store = CardStore(":memory:")
    preset_id = store.save_preset(Preset(name="夏天"))
    store.save_preset(Preset(id=preset_id, name="冬天", mode=4, temp=22))
    assert store.get_preset(preset_id).name == "冬天"
    assert len(store.list_presets()) == 1


def test_a_preset_keeps_its_timer():
    store = CardStore(":memory:")
    preset_id = store.save_preset(Preset(name="睡觉", timer_enabled=True, timer_at="22:30"))
    got = store.get_preset(preset_id)
    assert got.timer_enabled is True
    assert got.timer_at == "22:30"


def test_deleting_a_preset_removes_it():
    store = CardStore(":memory:")
    preset_id = store.save_preset(Preset(name="夏天"))
    store.delete_preset(preset_id)
    assert store.get_preset(preset_id) is None
    assert store.list_presets() == []


# ------------------------------------------------------------------ schedules


def test_a_schedule_roundtrips_with_its_payload():
    store = CardStore(":memory:")
    store.add_schedule("preset", {"preset_id": 1}, 1000.0)
    (job,) = store.list_schedules()
    assert job.kind == "preset"
    assert job.payload == {"preset_id": 1}
    assert job.run_at == 1000.0
    assert job.status == "pending"


def test_schedule_status_can_be_advanced():
    store = CardStore(":memory:")
    schedule_id = store.add_schedule("preset", {"preset_id": 1}, 1000.0)
    store.set_schedule_status(schedule_id, "done")
    assert store.list_schedules(status="pending") == []
    assert store.list_schedules()[0].status == "done"


# ------------------------------------------------------------------- env rows


def test_env_samples_roundtrip_newest_last():
    store = CardStore(":memory:")
    store.add_env_sample(100.0, 25, 50, True)
    store.add_env_sample(200.0, 26, 51, True)
    assert [row.temp for row in store.env_samples()] == [25, 26]
    assert [row.temp for row in store.env_samples(since=150.0)] == [26]


# -------------------------------------------------------------------- settings


def test_settings_return_the_default_until_set():
    store = CardStore(":memory:")
    assert store.get_setting("push_time", "17:00") == "17:00"
    store.set_setting("push_time", "08:00")
    assert store.get_setting("push_time") == "08:00"
    store.set_setting("push_time", "09:00")
    assert store.all_settings() == {"push_time": "09:00"}
