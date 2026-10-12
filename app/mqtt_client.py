"""MQTT link to the ESP32 gateway (paho-mqtt).

A thin adapter: it owns the connection, reconnects, and hands decoded payloads
to callbacks. All decisions live in `state.py` and the service wiring, so this
module stays glue that is easy to reason about.
"""

from __future__ import annotations

import json
import logging
from typing import Callable, Optional

import paho.mqtt.client as mqtt

log = logging.getLogger(__name__)

PayloadHandler = Callable[[dict], None]

#: Keepalive in seconds. The device uses the same 15s, so the broker notices a
#: dead board (and publishes its LWT) on roughly the same timescale.
KEEPALIVE_S = 15


class MqttBridge:
    """Subscribe to the gateway's state/status, publish commands to it."""

    def __init__(self, *, host: str, port: int, prefix: str,
                 client_id: str = "ac-bot",
                 username: str = "", password: str = "",
                 on_state: Optional[PayloadHandler] = None,
                 on_status: Optional[PayloadHandler] = None) -> None:
        self._host = host
        self._port = port
        self.on_state = on_state
        self.on_status = on_status

        self._cmd_topic = f"{prefix}/cmd"
        self._state_topic = f"{prefix}/state"
        self._status_topic = f"{prefix}/status"

        self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
        if username:
            self._client.username_pw_set(username, password or None)
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message

    # ------------------------------------------------------------------ lifecycle

    @property
    def connected(self) -> bool:
        return self._client.is_connected()

    def start(self) -> None:
        """Connect in the background; paho retries on its own if the broker blinks."""
        log.info("connecting to mqtt://%s:%d", self._host, self._port)
        self._client.connect_async(self._host, self._port, keepalive=KEEPALIVE_S)
        self._client.loop_start()

    def stop(self) -> None:
        self._client.loop_stop()
        self._client.disconnect()

    # ------------------------------------------------------------------- outbound

    def publish_command(self, payload: dict) -> bool:
        """Publish one command. An empty payload means "nothing to send"."""
        if not payload:
            return False
        body = json.dumps(payload, separators=(",", ":"))
        info = self._client.publish(self._cmd_topic, body, qos=1)
        log.info("%s -> %s", self._cmd_topic, body)
        return info.rc == mqtt.MQTT_ERR_SUCCESS

    # ------------------------------------------------------- paho callbacks (v2)

    def _on_connect(self, client, userdata, flags, reason_code, properties=None) -> None:
        if getattr(reason_code, "is_failure", False) or reason_code != 0:
            log.warning("mqtt connect failed: %s", reason_code)
            return
        log.info("mqtt connected; subscribing to %s and %s", self._state_topic, self._status_topic)
        client.subscribe([(self._state_topic, 1), (self._status_topic, 1)])

    def _on_disconnect(self, client, userdata, flags, reason_code, properties=None) -> None:
        log.warning("mqtt disconnected (%s)", reason_code)

    def _on_message(self, client, userdata, message) -> None:
        try:
            payload = json.loads(message.payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            log.warning("ignoring unparseable payload on %s", message.topic)
            return
        if not isinstance(payload, dict):
            log.warning("ignoring non-object payload on %s", message.topic)
            return

        if message.topic == self._state_topic:
            if self.on_state:
                self.on_state(payload)
        elif message.topic == self._status_topic:
            if self.on_status:
                self.on_status(payload)
