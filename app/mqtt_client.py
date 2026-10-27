"""MQTT link to the ESP32 gateway (paho-mqtt).

A thin adapter: it owns the connection, reconnects, and hands decoded payloads
to callbacks. All decisions live in `state.py` and the service wiring, so this
module stays glue that is easy to reason about.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Callable, Optional

import paho.mqtt.client as mqtt

log = logging.getLogger(__name__)

PayloadHandler = Callable[[dict], None]
#: `on_env` / `on_event` also need to know whether the broker replayed a
#: retained message, so the service can ignore a stale replay after a restart
#: (design 18.6).
RetainedHandler = Callable[[dict, bool], None]

#: Keepalive in seconds. The device uses the same 15s, so the broker notices a
#: dead board (and publishes its LWT) on roughly the same timescale.
KEEPALIVE_S = 15


class MqttBridge:
    """Subscribe to the gateway's state/status, publish commands to it."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        prefix: str,
        client_id: str = "ac-bot",
        username: str = "",
        password: str = "",
        on_state: Optional[PayloadHandler] = None,
        on_status: Optional[PayloadHandler] = None,
        on_env: Optional[RetainedHandler] = None,
        on_event: Optional[RetainedHandler] = None,
    ) -> None:
        self._host = host
        self._port = port
        self._client_id = client_id
        self._username = username
        self._password = password
        self.on_state = on_state
        self.on_status = on_status
        self.on_env = on_env
        self.on_event = on_event

        self._cmd_topic = f"{prefix}/cmd"
        self._state_topic = f"{prefix}/state"
        self._status_topic = f"{prefix}/status"
        self._env_topic = f"{prefix}/env"
        self._hb_topic = f"{prefix}/hb"
        self._event_topic = f"{prefix}/event"

        self._client = self._build_client()

    def _build_client(self):
        """A configured paho client, not yet dialled. Also used by `restart`."""
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=self._client_id)
        if self._username:
            client.username_pw_set(self._username, self._password or None)
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        return client

    # ------------------------------------------------------------------ lifecycle

    @property
    def topics(self) -> dict:
        """Every topic the bridge uses, derived from one prefix (spec 9.1)."""
        return {
            "cmd": self._cmd_topic,
            "state": self._state_topic,
            "status": self._status_topic,
            "env": self._env_topic,
            "hb": self._hb_topic,
            "event": self._event_topic,
        }

    @property
    def connected(self) -> bool:
        return self._client.is_connected()

    def start(self) -> None:
        """Connect in the background; paho retries on its own if the broker blinks."""
        self._dial(self._client)

    def _dial(self, client) -> None:
        log.info("connecting to mqtt://%s:%d", self._host, self._port)
        client.connect_async(self._host, self._port, keepalive=KEEPALIVE_S)
        client.loop_start()

    def restart(self) -> None:
        """Throw the client away and dial again -- never on the calling thread.

        `start()` is paho's own retry, and it covers a blip. It does *not* cover
        a client whose network thread has died: that one is not connected, fails
        every publish, and never dials again by itself (2026-10-17, design
        18.14). Rebuilding the object is what clears that state, and the work
        runs on a daemon thread on purpose -- `loop_stop()` joins the old
        thread, so a wedged one would block whoever called us.
        """
        old = self._client
        log.warning("mqtt: re-dialling with a fresh client")
        threading.Thread(
            target=self._reconnect, args=(old,), name="mqtt-restart", daemon=True
        ).start()

    def _reconnect(self, old) -> None:
        try:
            old.loop_stop()
        except Exception:  # noqa: BLE001 - a dead client must not block the retry
            log.exception("mqtt: the old client did not stop cleanly")
        client = self._build_client()
        self._client = client
        self._dial(client)

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

    def publish_heartbeat(self) -> bool:
        """Tell the device the *service* is alive (design §9.1 / 18.8).

        Mosquitto runs as its own unit on the Pi, so \"MQTT is connected\" says
        nothing about this process: the heartbeat is the device's only proof
        that the thing making the decisions is still there. Sent QoS0 and not
        retained -- a heartbeat that outlives the process is a lie.
        """
        body = json.dumps({"ts": int(time.time())}, separators=(",", ":"))
        info = self._client.publish(self._hb_topic, body, qos=0)
        return info.rc == mqtt.MQTT_ERR_SUCCESS

    # ------------------------------------------------------- paho callbacks (v2)

    def _on_connect(self, client, userdata, flags, reason_code, properties=None) -> None:
        try:
            if getattr(reason_code, "is_failure", False) or reason_code != 0:
                log.warning("mqtt connect failed: %s", reason_code)
                return
            log.info(
                "mqtt connected; subscribing to %s, %s, %s and %s",
                self._state_topic,
                self._status_topic,
                self._env_topic,
                self._event_topic,
            )
            client.subscribe(
                [
                    (self._state_topic, 1),
                    (self._status_topic, 1),
                    (self._env_topic, 1),
                    (self._event_topic, 1),
                ]
            )
        except Exception:  # noqa: BLE001 - never let a callback kill paho's thread
            log.exception("mqtt: the connect callback failed; the link stays up")

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

        retained = bool(getattr(message, "retain", False))
        if message.topic == self._state_topic:
            self._deliver(self.on_state, payload)
        elif message.topic == self._status_topic:
            self._deliver(self.on_status, payload)
        elif message.topic == self._env_topic:
            self._deliver(self.on_env, payload, retained)
        elif message.topic == self._event_topic:
            self._deliver(self.on_event, payload, retained)

    def _deliver(self, handler, *args) -> None:
        """Run one payload handler; a failure here is data, not a dead link.

        paho calls these on the thread that owns the socket, and an exception
        escaping one ends that thread for good: `loop_start()` never comes back,
        `connect_async` never re-dials, and the client sits there looking
        configured while every publish fails. That is what happened on
        2026-10-17 -- a Feishu call raised inside the `ac/state` handler and the
        service stayed deaf and mute for 4.8 days (design 18.14). The Feishu
        callbacks guard themselves the same way, for the same reason.
        """
        if handler is None:
            return
        try:
            handler(*args)
        except Exception:  # noqa: BLE001 - never let a handler kill the socket
            log.exception(
                "mqtt: the %s handler failed; the link stays up",
                getattr(handler, "__name__", "payload"),
            )
