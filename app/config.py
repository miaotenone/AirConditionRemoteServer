"""Service configuration, read from the environment (spec section 7).

Secrets are `SecretStr` so they cannot leak through a log line or a traceback.
"""

from __future__ import annotations

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Everything the service needs, with the documented env variable names."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Feishu app (self-built app + long connection; no public URL needed) ---
    feishu_app_id: str
    feishu_app_secret: SecretStr
    owner_open_id: str

    # --- MQTT broker: Mosquitto on this same Raspberry Pi ---
    mqtt_host: str = "127.0.0.1"
    mqtt_port: int = 1883
    mqtt_user: str = ""
    mqtt_pass: SecretStr = SecretStr("")
    mqtt_prefix: str = "ac"
    mqtt_client_id: str = "ac-bot"

    # --- Behaviour ---
    log_level: str = "INFO"
    #: Verbosity of the lark-oapi long-connection *socket*, independent of
    #: `log_level`. "DEBUG" dumps raw WS traffic, which is how you tell
    #: "Feishu sent nothing" apart from "the SDK received it but did not
    #: route it to our handler". Leave it at INFO unless debugging.
    feishu_log_level: str = "INFO"
    #: Seconds the OpenAPI client waits before giving up on one call. The SDK's
    #: own default is 30 and every call retries once, so a dead internet could
    #: hold the card-patching thread for a minute (design 18.15).
    feishu_timeout_s: float = 5.0
    #: Rapid taps inside this window collapse into one `ac/cmd` (spec 5.5).
    debounce_ms: int = 400
    #: No `ac/state` back within this long -> card shows "⚠️ 未确认" (spec 10).
    confirm_timeout_s: float = 5.0
    #: Online but silent for longer than this -> soft "may be stale" note (spec 10).
    stale_after_s: float = 90.0
    #: Understand plain text ("调低两度") as a command, not just the summon
    #: word "设置卡片".
    #: Design: docs/superpowers/specs/2026-10-13-feishu-text-control-design.md.
    #: Set false to fall back to "only 设置卡片 posts the card".
    text_control_enabled: bool = True
    db_path: str = "ac-bot.sqlite3"

    # --- DHT11 feedback + night loop (design §7/§11) ---
    #: How often the latest `ac/env` reading is appended to the history.
    dht_record_s: int = 300
    #: Default time the daily weather suggestion is pushed (editable on a card).
    weather_default_push: str = "17:00"
    #: Default night target and time window (both editable on a card).
    night_default_target: int = 26
    night_default_start: str = "23:00"
    night_default_end: str = "07:00"
    #: How often the service publishes `ac/hb`, the device's liveness proof.
    hb_interval_s: int = 30
    #: Optional lat/lon override; empty means "geolocate by IP".
    weather_lat: str = ""
    weather_lon: str = ""

    @property
    def cmd_topic(self) -> str:
        return f"{self.mqtt_prefix}/cmd"

    @property
    def state_topic(self) -> str:
        return f"{self.mqtt_prefix}/state"

    @property
    def status_topic(self) -> str:
        return f"{self.mqtt_prefix}/status"

    @property
    def env_topic(self) -> str:
        return f"{self.mqtt_prefix}/env"

    @property
    def hb_topic(self) -> str:
        return f"{self.mqtt_prefix}/hb"

    @property
    def event_topic(self) -> str:
        return f"{self.mqtt_prefix}/event"
