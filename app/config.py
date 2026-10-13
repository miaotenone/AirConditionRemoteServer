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
    #: Rapid taps inside this window collapse into one `ac/cmd` (spec 5.5).
    debounce_ms: int = 400
    #: No `ac/state` back within this long -> card shows "⚠️ 未确认" (spec 10).
    confirm_timeout_s: float = 5.0
    #: Online but silent for longer than this -> soft "may be stale" note (spec 10).
    stale_after_s: float = 90.0
    db_path: str = "ac-bot.sqlite3"

    @property
    def cmd_topic(self) -> str:
        return f"{self.mqtt_prefix}/cmd"

    @property
    def state_topic(self) -> str:
        return f"{self.mqtt_prefix}/state"

    @property
    def status_topic(self) -> str:
        return f"{self.mqtt_prefix}/status"
