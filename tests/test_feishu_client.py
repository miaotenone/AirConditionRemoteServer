"""Unit tests for app.feishu_client -- no network, no credentials.

The Feishu SDK builds its client object without touching the network, so the
event routing and the reply request shape are pinned here rather than on a
phone. The API surface is swapped for a stub, which is the only way to test
"what did we send" without credentials.
"""

import types

from app.feishu_client import FeishuClient


def _ok(message_id: str = "om_reply"):
    return types.SimpleNamespace(
        success=lambda: True, code=0, msg="ok",
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
        im=types.SimpleNamespace(v1=types.SimpleNamespace(
            message=types.SimpleNamespace(reply=reply)))
    )
    return client, calls


def _message(message_type="text", content='{"text":"开机"}', message_id="om_9"):
    return types.SimpleNamespace(event=types.SimpleNamespace(
        message=types.SimpleNamespace(
            message_type=message_type, message_id=message_id,
            chat_id="oc_9", content=content),
        sender=types.SimpleNamespace(
            sender_id=types.SimpleNamespace(open_id="ou_9")),
    ))


def test_reply_text_calls_the_reply_api_and_reports_success():
    client, calls = make_client()
    assert client.reply_text("om_1", "✅ 已开机") is True
    assert calls["reply"].message_id == "om_1"


def test_reply_text_reports_failure_without_raising():
    # 长连接不能在回调里被一次失败的回复打断
    client, _ = make_client()
    client._api.im.v1.message.reply = lambda request: types.SimpleNamespace(
        success=lambda: False, code=99991663, msg="denied", data=None)
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
