"""Feishu side of the bridge: the long connection, and sending/patching cards.

Verified against lark-oapi 1.7.2:

* ``lark.ws.Client(app_id, app_secret, event_handler=..., ...)`` runs the long
  connection (no public URL, no tunnel).
* ``EventDispatcherHandler.builder("", "").register_p2_card_action_trigger(...)``
  is the callback for card clicks; ``register_p2_im_message_receive_v1`` for
  plain messages. Both are on the *builder*, not the handler class.
* The click payload exposes ``data.operator.open_id``, ``data.action.value``,
  ``data.action.option`` and ``data.context.open_chat_id`` /
  ``data.context.open_message_id``.

The callback must be answered inside ~3 s (spec 5.5), so the handler only
normalises the click, hands it to the service, and returns immediately. The card
itself is updated with ``patch_card`` (PATCH /im/v1/messages/:id), which is also
how MQTT-driven updates are pushed.
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any, Callable, Optional

import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    CreateMessageRequest,
    CreateMessageRequestBody,
    PatchMessageRequest,
    PatchMessageRequestBody,
    ReplyMessageRequest,
    ReplyMessageRequestBody,
)
from lark_oapi.event.callback.model.p2_card_action_trigger import (
    P2CardActionTriggerResponse,
)

log = logging.getLogger(__name__)

#: (open_id, chat_id, message_id, action.value, action.option) -> None
ActionHandler = Callable[[str, str, str, Any, Any], None]
#: (open_id, chat_id, message_id, text) -> None
TextHandler = Callable[[str, str, str, str], None]


class FeishuClient:
    """Sends/patches the card over OpenAPI and receives events over the socket."""

    def __init__(self, *, app_id: str, app_secret: str,
                 on_action: Optional[ActionHandler] = None,
                 on_text: Optional[TextHandler] = None,
                 log_level: str = "INFO") -> None:
        self._app_id = app_id
        self._app_secret = app_secret
        self.on_action = on_action
        self.on_text = on_text
        self._log_level = log_level
        self._api = lark.Client.builder().app_id(app_id).app_secret(app_secret).build()
        self._thread: Optional[threading.Thread] = None

    # ---------------------------------------------------------------- outbound

    def send_card(self, chat_id: str, card: dict) -> Optional[str]:
        """Post a new card message; returns its message_id (needed to patch it)."""
        request = (
            CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(chat_id)
                .msg_type("interactive")
                .content(_dump(card))
                .build()
            )
            .build()
        )
        response = self._api.im.v1.message.create(request)
        if not response.success():
            log.error("send_card failed: code=%s msg=%s", response.code, response.msg)
            return None
        return response.data.message_id

    def patch_card(self, message_id: str, card: dict) -> bool:
        """Replace the content of an existing card, in place."""
        request = (
            PatchMessageRequest.builder()
            .message_id(message_id)
            .request_body(PatchMessageRequestBody.builder().content(_dump(card)).build())
            .build()
        )
        response = self._api.im.v1.message.patch(request)
        if not response.success():
            log.error("patch_card failed: code=%s msg=%s", response.code, response.msg)
            return False
        return True

    def reply_text(self, message_id: str, text: str) -> bool:
        """Answer one message in its own thread -- a plain text receipt.

        Receipts go out as replies rather than new top-level messages so a
        conversation stays readable; the card, not the chat log, is the
        interface that gets updated in place (design 5.1/5.4).
        """
        request = (
            ReplyMessageRequest.builder()
            .message_id(message_id)
            .request_body(
                ReplyMessageRequestBody.builder()
                .msg_type("text")
                .content(_dump_text(text))
                .build()
            )
            .build()
        )
        response = self._api.im.v1.message.reply(request)
        if not response.success():
            log.error("reply_text failed: code=%s msg=%s", response.code, response.msg)
            return False
        return True

    # ------------------------------------------------- long connection (inbound)

    def start(self) -> None:
        """Run the event socket on a background thread (it reconnects itself)."""
        handler = (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_card_action_trigger(self._on_card_action)
            .register_p2_im_message_receive_v1(self._on_message)
            .build()
        )
        client = lark.ws.Client(
            self._app_id,
            self._app_secret,
            event_handler=handler,
            log_level=_lark_log_level(self._log_level),
        )
        self._thread = threading.Thread(
            target=client.start, name="feishu-long-connection", daemon=True
        )
        self._thread.start()
        log.info("feishu long connection started")

    # ------------------------------------------------------------ event bridging

    def _on_card_action(self, data) -> None:
        """Answer a card click fast; the service does the real work."""
        try:
            operator = _get(getattr(data, "operator", None), "open_id", "")
            context = getattr(data, "context", None)
            chat_id = _get(context, "open_chat_id", "")
            message_id = _get(context, "open_message_id", "")
            action = getattr(data, "action", None)
            raw_value = _get(action, "value", None)
            raw_option = _get(action, "option", None)
            log.info("card action from %s: value=%r option=%r", operator, raw_value, raw_option)
            if self.on_action:
                self.on_action(operator, chat_id, message_id, raw_value, raw_option)
        except Exception:  # noqa: BLE001 - never let a handler kill the socket
            log.exception("card action handler failed")
        # An empty response simply acknowledges the click well inside the 3 s
        # budget; the card is refreshed by the service as soon as it can.
        return P2CardActionTriggerResponse({})

    def _on_message(self, data) -> None:
        """A plain text message is a command (or a "/ac" asking for the card)."""
        try:
            event = getattr(data, "event", None)
            message = _get(event, "message", None)
            if _get(message, "message_type", "") != "text":
                return
            sender = _get(event, "sender", None)
            open_id = _get(_get(sender, "sender_id", None), "open_id", "")
            chat_id = _get(message, "chat_id", "")
            message_id = _get(message, "message_id", "")
            text = _extract_text(_get(message, "content", ""))
            log.info("text message from %s: %r", open_id, text)
            if self.on_text:
                self.on_text(open_id, chat_id, message_id, text)
        except Exception:  # noqa: BLE001
            log.exception("message handler failed")
        return None


def _lark_log_level(name: str):
    """Map a plain level name onto the lark enum; anything odd means INFO."""
    return lark.LogLevel.DEBUG if str(name).upper() == "DEBUG" else lark.LogLevel.INFO


def _dump(card: dict) -> str:
    return json.dumps(card, ensure_ascii=False, separators=(",", ":"))


def _dump_text(text: str) -> str:
    """Feishu wants text message bodies as ``{"text": "..."}`` JSON."""
    return json.dumps({"text": text}, ensure_ascii=False, separators=(",", ":"))


def _get(obj: Any, name: str, default: Any = None) -> Any:
    return getattr(obj, name, default) if obj is not None else default


def _extract_text(content: Any) -> str:
    """Feishu sends text messages as a JSON string: ``{"text": "..."}``."""
    if not isinstance(content, str):
        return ""
    try:
        return str(json.loads(content).get("text", "")).strip()
    except (ValueError, AttributeError):
        return ""
