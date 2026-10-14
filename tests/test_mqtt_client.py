"""Unit tests for app.mqtt_client -- the thin paho adapter.

No broker here: the client object is built offline and the paho callbacks are
called straight, so routing and topic derivation are pinned without a network.
"""

from types import SimpleNamespace

from app.mqtt_client import MqttBridge


class FakeMessage:
    def __init__(self, topic, payload, retain=False):
        self.topic = topic
        self.payload = payload
        self.retain = retain


class FakeClient:
    """Captures publishes so the QoS and topic can be asserted."""

    def __init__(self):
        self.calls = []

    def publish(self, topic, payload, qos=0):
        self.calls.append({"topic": topic, "payload": payload, "qos": qos})
        return SimpleNamespace(rc=0)


def test_topics_are_derived_from_the_prefix():
    topics = MqttBridge(host="x", port=1, prefix="ac2").topics
    assert topics == {"cmd": "ac2/cmd", "state": "ac2/state", "status": "ac2/status",
                      "env": "ac2/env", "hb": "ac2/hb", "event": "ac2/event"}


def test_state_and_status_keep_their_one_argument_handler():
    seen = {}
    bridge = MqttBridge(host="x", port=1, prefix="ac",
                        on_state=lambda p: seen.update(state=p),
                        on_status=lambda p: seen.update(status=p))
    bridge._on_message(None, None, FakeMessage("ac/state", b'{"power":1}'))
    bridge._on_message(None, None, FakeMessage("ac/status", b'{"online":false}'))
    assert seen["state"] == {"power": 1}
    assert seen["status"] == {"online": False}


def test_env_arrives_with_its_retained_flag():
    # 设计 §18.6：服务必须能分辨「刚上报」和「开机时重放的 retained」。
    seen = []
    bridge = MqttBridge(host="x", port=1, prefix="ac",
                        on_env=lambda payload, retained: seen.append((payload, retained)))
    bridge._on_message(None, None, FakeMessage("ac/env", b'{"temp":26,"ok":true}', retain=True))
    bridge._on_message(None, None, FakeMessage("ac/env", b'{"temp":27,"ok":true}'))
    assert seen == [({"temp": 26, "ok": True}, True), ({"temp": 27, "ok": True}, False)]


def test_events_arrive_with_their_retained_flag():
    seen = []
    bridge = MqttBridge(host="x", port=1, prefix="ac",
                        on_event=lambda payload, retained: seen.append((payload, retained)))
    bridge._on_message(None, None, FakeMessage("ac/event", b'{"type":"failsafe_off"}'))
    assert seen == [({"type": "failsafe_off"}, False)]


def test_unparseable_and_unknown_topics_are_ignored():
    seen = []
    bridge = MqttBridge(host="x", port=1, prefix="ac",
                        on_env=lambda payload, retained: seen.append(payload))
    bridge._on_message(None, None, FakeMessage("ac/env", b"not json"))
    bridge._on_message(None, None, FakeMessage("ac/other", b'{"a":1}'))
    assert seen == []


def test_the_heartbeat_is_published_plain_and_non_retained():
    bridge = MqttBridge(host="x", port=1, prefix="ac")
    fake = FakeClient()
    bridge._client = fake
    bridge.publish_heartbeat()
    (call,) = fake.calls
    assert call["topic"] == "ac/hb"
    assert call["qos"] == 0
