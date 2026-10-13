"""Unit tests for app.config -- the documented env contract (spec section 7)."""

import pytest

from app.config import Settings

REQUIRED = {
    "FEISHU_APP_ID": "cli_test",
    "FEISHU_APP_SECRET": "topsecret",
    "OWNER_OPEN_ID": "ou_test",
}


def build(**overrides):
    """Settings with the required values supplied, ignoring any local .env file."""
    values = {
        "feishu_app_id": "cli_test",
        "feishu_app_secret": "topsecret",
        "owner_open_id": "ou_test",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_documented_env_names_are_read(monkeypatch):
    for key, value in REQUIRED.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("MQTT_HOST", "10.0.0.5")
    monkeypatch.setenv("MQTT_PORT", "1884")

    settings = Settings(_env_file=None)

    assert settings.feishu_app_id == "cli_test"
    assert settings.owner_open_id == "ou_test"
    assert settings.mqtt_host == "10.0.0.5"
    assert settings.mqtt_port == 1884


def test_defaults_match_the_spec():
    settings = build()
    assert settings.mqtt_host == "127.0.0.1"
    assert settings.mqtt_port == 1883
    assert settings.mqtt_user == ""
    assert settings.mqtt_prefix == "ac"
    assert settings.log_level == "INFO"
    assert settings.feishu_log_level == "INFO"


def test_missing_required_settings_are_rejected(monkeypatch):
    for key in REQUIRED:
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(Exception):
        Settings(_env_file=None)


def test_topic_names_are_derived_from_the_prefix():
    settings = build()
    assert settings.cmd_topic == "ac/cmd"
    assert settings.state_topic == "ac/state"
    assert settings.status_topic == "ac/status"


def test_topics_follow_a_custom_prefix():
    assert build(mqtt_prefix="ac2").cmd_topic == "ac2/cmd"


def test_secrets_are_not_leaked_by_repr():
    settings = build()
    assert "topsecret" not in repr(settings)
    assert settings.feishu_app_secret.get_secret_value() == "topsecret"
