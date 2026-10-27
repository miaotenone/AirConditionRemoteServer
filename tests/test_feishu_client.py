"""Unit tests for app.feishu_client -- no network, no credentials.

The Feishu SDK builds its client object without touching the network, so the
event routing and the reply request shape are pinned here rather than on a
phone. The API surface is swapped for a stub, which is the only way to test
"what did we send" without credentials.
"""

import json
import types

from app.feishu_client import FeishuClient


def _ok(message_id: str = "om_reply"):
    return types.SimpleNamespace(
        success=lambda: True,
        code=0,
        msg="ok",
        data=types.SimpleNamespace(message_id=message_id),
    )


def make_client(on_text=None):
    """A client whose OpenAPI surface is stubbed out."""
    client = FeishuClient(app_id="cli_test", app_secret="secret", on_text=on_text)
    calls = {}

    def reply(request):
        calls["reply"] = request
        return _ok()

    client._api = types.SimpleNamespace(
        im=types.SimpleNamespace(
            v1=types.SimpleNamespace(message=types.SimpleNamespace(reply=reply))
        )
    )
    return client, calls


def _message(message_type="text", content='{"text":"开机"}', message_id="om_9"):
    return types.SimpleNamespace(
        event=types.SimpleNamespace(
            message=types.SimpleNamespace(
                message_type=message_type, message_id=message_id, chat_id="oc_9", content=content
            ),
            sender=types.SimpleNamespace(sender_id=types.SimpleNamespace(open_id="ou_9")),
        )
    )


def test_reply_text_calls_the_reply_api_and_reports_success():
    client, calls = make_client()
    assert client.reply_text("om_1", "✅ 已开机") is True
    assert calls["reply"].message_id == "om_1"


def test_reply_text_reports_failure_without_raising():
    # 长连接不能在回调里被一次失败的回复打断
    client, _ = make_client()
    client._api.im.v1.message.reply = lambda request: types.SimpleNamespace(
        success=lambda: False, code=99991663, msg="denied", data=None
    )
    assert client.reply_text("om_1", "hi") is False


def test_text_message_is_handed_over_with_its_message_id():
    seen = []
    client, _ = make_client(on_text=lambda *args: seen.append(args))
    client._on_message(_message())
    assert seen == [("ou_9", "oc_9", "om_9", "开机")]


def test_non_text_and_malformed_messages_are_ignored_silently():
    seen = []
    client, _ = make_client(on_text=lambda *args: seen.append(args))
    client._on_message(_message(message_type="image", content='{"image_key":"k"}'))
    client._on_message(_message(content="not json"))
    # 图片整条丢掉；畸形 JSON 退化成空串，交给 intent 判 unclear
    assert seen == [("ou_9", "oc_9", "om_9", "")]


# --------------------------------------------------------------- card clicks


def _card_action(
    value=None,
    option=None,
    operator="ou_9",
    chat_id="oc_9",
    message_id="om_9",
    form_value=None,
    name=None,
):
    """The shape lark-oapi really hands `_on_card_action`: nested under `event`."""
    return types.SimpleNamespace(
        event=types.SimpleNamespace(
            operator=types.SimpleNamespace(open_id=operator),
            action=types.SimpleNamespace(
                value=value, option=option, form_value=form_value, name=name
            ),
            context=types.SimpleNamespace(open_chat_id=chat_id, open_message_id=message_id),
        )
    )


def _action_client():
    seen = []
    client = FeishuClient(
        app_id="cli_test", app_secret="secret", on_action=lambda *args: seen.append(args)
    )
    return client, seen


def test_card_click_is_read_from_the_nested_event_payload():
    # The SDK wraps the click in `event`. Reading the top level instead made
    # every click arrive as operator="" / value=None, which the service then
    # dropped as a non-owner event.
    client, seen = _action_client()
    client._on_card_action(_card_action(value={"a": "power", "v": 1}))
    assert seen == [("ou_9", "oc_9", "om_9", {"a": "power", "v": 1}, None, None, None)]


def test_temperature_select_reports_through_action_option():
    client, seen = _action_client()
    client._on_card_action(_card_action(option="26"))
    assert seen == [("ou_9", "oc_9", "om_9", None, "26", None, None)]


def test_a_form_submit_carries_form_value_and_the_element_name():
    # 设计 18.3：表单提交的字段在 action.form_value 里，键是元素 name。
    client, seen = _action_client()
    client._on_card_action(
        _card_action(
            value={"a": "preset_save"}, form_value={"name": "夏天", "temp": "26"}, name="submit"
        )
    )
    assert seen == [
        (
            "ou_9",
            "oc_9",
            "om_9",
            {"a": "preset_save"},
            None,
            {"name": "夏天", "temp": "26"},
            "submit",
        )
    ]


def test_unnested_card_payload_still_works():
    # Fallback: an SDK build that hands the click over flat must not regress.
    client, seen = _action_client()
    client._on_card_action(
        types.SimpleNamespace(
            operator=types.SimpleNamespace(open_id="ou_9"),
            action=types.SimpleNamespace(value={"a": "temp", "v": 20}, option=None),
            context=types.SimpleNamespace(open_chat_id="oc_9", open_message_id="om_9"),
        )
    )
    assert seen == [("ou_9", "oc_9", "om_9", {"a": "temp", "v": 20}, None, None, None)]


def test_fully_flat_card_payload_is_read_too():
    # 2026-10-14 上机：真实点击事件既没有 `event`，也没有 operator/action 包装，
    # 只有裸字段 —— 少了这条路径，按钮点了就像没点，而且日志里看不出原因。
    client, seen = _action_client()
    client._on_card_action(
        types.SimpleNamespace(
            open_id="ou_9",
            open_chat_id="oc_9",
            open_message_id="om_9",
            value={"a": "power", "v": 0},
            option=None,
        )
    )
    assert seen == [("ou_9", "oc_9", "om_9", {"a": "power", "v": 0}, None, None, None)]


def test_a_dict_payload_is_read_too():
    # SDK 把回调当纯字典交出来时 getattr 只能拿到默认值 —— 字典也得认。
    client, seen = _action_client()
    client._on_card_action(
        {
            "event": {
                "operator": {"open_id": "ou_9"},
                "action": {"value": {"a": "mode", "v": 1}, "option": None},
                "context": {"open_chat_id": "oc_9", "open_message_id": "om_9"},
            }
        }
    )
    assert seen == [("ou_9", "oc_9", "om_9", {"a": "mode", "v": 1}, None, None, None)]


def test_an_unreadable_click_dumps_its_raw_payload(caplog):
    # 上次事故就是「全空」却什么线索都没留；解不出来必须把原始报文写进日志。
    client, seen = _action_client()
    with caplog.at_level("WARNING", logger="app.feishu_client"):
        client._on_card_action(types.SimpleNamespace(event=None, note="mystery"))
    assert "card action payload matched no known shape" in caplog.text
    assert "mystery" in caplog.text
    assert seen == [("", "", "", None, None, None, None)]


def test_a_broken_click_payload_never_kills_the_socket():
    client, seen = _action_client()
    assert client._on_card_action(types.SimpleNamespace(event=None)) is not None
    assert seen == [("", "", "", None, None, None, None)]


# ------------------------------------------------------- an API call that throws
#
# 设计 18.14。SDK 可以在 response.success() *之前* 就抛：2026-10-17 一个空/非 JSON
# 的响应体死在它自己的 unmarshal 里（json.JSONDecodeError），异常一路穿过 MQTT
# 回调线程，把整条链路打死 4.8 天。所以每一次调用都只能是「没更新成功」。


def test_a_body_the_sdk_cannot_parse_is_only_a_failed_call(caplog):
    client, _ = make_client()
    attempts = []

    def boom(request):
        attempts.append(request)
        raise json.JSONDecodeError("Expecting value", "", 0)

    client._api.im.v1.message.patch = boom
    with caplog.at_level("WARNING", logger="app.feishu_client"):
        assert client.patch_card("om_1", {"a": 1}) is False
    assert len(attempts) == 2  # 规范 §10：重试一次
    assert "patch_card raised on attempt 1/2" in caplog.text


def test_send_card_survives_the_same_way(caplog):
    client, _ = make_client()

    def boom(request):
        raise RuntimeError("connection reset")

    client._api.im.v1.message.create = boom
    with caplog.at_level("WARNING", logger="app.feishu_client"):
        assert client.send_card("oc_1", {"a": 1}) is None


def test_recall_and_send_text_never_raise_either(caplog):
    client, _ = make_client()

    def boom(request):
        raise RuntimeError("boom")

    client._api.im.v1.message.delete = boom
    client._api.im.v1.message.create = boom
    with caplog.at_level("WARNING", logger="app.feishu_client"):
        assert client.recall_card("om_1") is False
        assert client.send_text("oc_1", "hi") is False


def test_a_plain_rejection_is_not_retried(caplog):
    # 被拒（success()=False）多半重试也没用，别白花配额 —— 只有抛异常才重试。
    client, _ = make_client()
    calls = []

    def denied(request):
        calls.append(request)
        return types.SimpleNamespace(success=lambda: False, code=99991663, msg="denied", data=None)

    client._api.im.v1.message.patch = denied
    with caplog.at_level("ERROR", logger="app.feishu_client"):
        assert client.patch_card("om_1", {"a": 1}) is False
    assert len(calls) == 1
    assert "patch_card failed: code=99991663" in caplog.text


def test_a_successful_send_still_reports_its_message_id():
    client, _ = make_client()
    client._api.im.v1.message.create = lambda request: _ok("om_new")
    assert client.send_card("oc_1", {"a": 1}) == "om_new"


def test_a_success_without_a_body_reports_no_message_id():
    # 契约之外的响应不该在调用线程上炸成 AttributeError。
    client, _ = make_client()
    client._api.im.v1.message.create = lambda request: types.SimpleNamespace(
        success=lambda: True, code=0, msg="ok", data=None
    )
    assert client.send_card("oc_1", {"a": 1}) is None


# ------------------------------------------------------------------ timeouts
# Design 18.15: the SDK defaults to 30 s per call, and every call here retries
# once -- so an unreachable Feishu could hold a thread for a minute. That is the
# thread the card patch runs on, and (before 18.15) the one that also owes the
# device its heartbeat. Seconds, not minutes.


def test_the_openapi_client_times_out_in_seconds():
    client = FeishuClient(app_id="cli_test", app_secret="secret")
    assert client._api.config.timeout == 5.0


def test_the_configured_timeout_reaches_the_sdk():
    client = FeishuClient(app_id="cli_test", app_secret="secret", timeout_s=2.5)
    assert client._api.config.timeout == 2.5
