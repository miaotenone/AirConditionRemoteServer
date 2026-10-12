"""Unit tests for app.store -- the owner -> card message_id mapping.

Single private owner in v1 (spec 5.1), so it is one row, but keyed by owner so a
group/whitelist later does not need a schema change.
"""

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
