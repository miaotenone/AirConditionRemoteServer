"""Unit tests for app.mqtt_client -- the thin paho adapter.

No broker here: the client object is built offline and the paho callbacks are
called straight, so routing and topic derivation are pinned without a network.
"""

import time
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
    assert topics == {
        "cmd": "ac2/cmd",
        "state": "ac2/state",
        "status": "ac2/status",
        "env": "ac2/env",
        "hb": "ac2/hb",
        "event": "ac2/event",
    }


def test_state_and_status_keep_their_one_argument_handler():
    seen = {}
    bridge = MqttBridge(
        host="x",
        port=1,
        prefix="ac",
        on_state=lambda p: seen.update(state=p),
        on_status=lambda p: seen.update(status=p),
    )
    bridge._on_message(None, None, FakeMessage("ac/state", b'{"power":1}'))
    bridge._on_message(None, None, FakeMessage("ac/status", b'{"online":false}'))
    assert seen["state"] == {"power": 1}
    assert seen["status"] == {"online": False}


def test_env_arrives_with_its_retained_flag():
    # 设计 §18.6：服务必须能分辨「刚上报」和「开机时重放的 retained」。
    seen = []
    bridge = MqttBridge(
        host="x",
        port=1,
        prefix="ac",
        on_env=lambda payload, retained: seen.append((payload, retained)),
    )
    bridge._on_message(None, None, FakeMessage("ac/env", b'{"temp":26,"ok":true}', retain=True))
    bridge._on_message(None, None, FakeMessage("ac/env", b'{"temp":27,"ok":true}'))
    assert seen == [({"temp": 26, "ok": True}, True), ({"temp": 27, "ok": True}, False)]


def test_events_arrive_with_their_retained_flag():
    seen = []
    bridge = MqttBridge(
        host="x",
        port=1,
        prefix="ac",
        on_event=lambda payload, retained: seen.append((payload, retained)),
    )
    bridge._on_message(None, None, FakeMessage("ac/event", b'{"type":"failsafe_off"}'))
    assert seen == [({"type": "failsafe_off"}, False)]


def test_unparseable_and_unknown_topics_are_ignored():
    seen = []
    bridge = MqttBridge(
        host="x", port=1, prefix="ac", on_env=lambda payload, retained: seen.append(payload)
    )
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


# ------------------------------------------------- robustness of the callbacks
#
# 设计 18.14。paho 的所有回调都跑在持有 socket 的那条线程上：只要有一个异常
# 逃出去，loop_start() 就再也回不来，客户端从此「看着还配着、发什么都发不出去」。
# 2026-10-17 就是这样聋哑了 4.8 天 —— 回调里抛异常只能是数据，不能是断链。


class FakeDialledClient:
    """Records what a dial does to one client object."""

    def __init__(self):
        self.stopped = False
        self.dialled = None
        self.looping = False

    def loop_stop(self):
        self.stopped = True

    def connect_async(self, host, port, keepalive=0):
        self.dialled = (host, port, keepalive)

    def loop_start(self):
        self.looping = True


def test_a_raising_state_handler_never_escapes_into_pahos_thread(caplog):
    def boom(payload):
        raise RuntimeError("the Feishu call blew up")

    bridge = MqttBridge(host="x", port=1, prefix="ac", on_state=boom)
    with caplog.at_level("ERROR", logger="app.mqtt_client"):
        bridge._on_message(None, None, FakeMessage("ac/state", b'{"power":1}'))
    assert "handler failed" in caplog.text
    assert "the Feishu call blew up" in caplog.text


def test_a_raising_retained_handler_never_escapes_either(caplog):
    def boom(payload, retained):
        raise RuntimeError("boom")

    bridge = MqttBridge(host="x", port=1, prefix="ac", on_env=boom, on_event=boom)
    with caplog.at_level("ERROR", logger="app.mqtt_client"):
        bridge._on_message(None, None, FakeMessage("ac/env", b'{"ok":true}', retain=True))
        bridge._on_message(None, None, FakeMessage("ac/event", b'{"type":"x"}'))
    assert caplog.text.count("handler failed") == 2


def test_a_failing_subscribe_does_not_kill_the_connect_callback(caplog):
    # on_connect 也在那条线程上跑，subscribe 抛出去一样是断链。
    class Client:
        def subscribe(self, topics):
            raise RuntimeError("no")

    bridge = MqttBridge(host="x", port=1, prefix="ac")
    with caplog.at_level("ERROR", logger="app.mqtt_client"):
        bridge._on_connect(Client(), None, None, 0)
    assert "connect callback failed" in caplog.text


def test_a_rejected_connect_reports_and_does_not_subscribe(caplog):
    class Client:
        def __init__(self):
            self.subscribed = False

        def subscribe(self, topics):
            self.subscribed = True

    client = Client()
    bridge = MqttBridge(host="x", port=1, prefix="ac")
    with caplog.at_level("WARNING", logger="app.mqtt_client"):
        bridge._on_connect(client, None, None, 5)
    assert client.subscribed is False
    assert "mqtt connect failed" in caplog.text


# ------------------------------------------------------------------ re-dialling


def test_start_dials_the_client_it_built():
    bridge = MqttBridge(host="h", port=1884, prefix="ac")
    fresh = FakeDialledClient()
    bridge._client = fresh
    bridge.start()
    assert fresh.dialled == ("h", 1884, 15)
    assert fresh.looping is True


def test_restart_swaps_in_a_fresh_client_and_stops_the_old_one():
    # 线程死了以后 connect_async 不会自己再拨：只能换一个客户端对象。
    bridge = MqttBridge(host="h", port=1884, prefix="ac")
    old, fresh = FakeDialledClient(), FakeDialledClient()
    bridge._client = old
    bridge._build_client = lambda: fresh
    bridge.restart()

    deadline = time.monotonic() + 2
    while bridge._client is not fresh and time.monotonic() < deadline:
        time.sleep(0.01)
    assert bridge._client is fresh
    assert old.stopped is True
    assert fresh.dialled == ("h", 1884, 15)
    assert fresh.looping is True
