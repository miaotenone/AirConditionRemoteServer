"""Feishu side of the bridge: the long connection, and sending/patching cards.

Verified against lark-oapi 1.7.2:

* ``lark.ws.Client(app_id, app_secret, event_handler=..., ...)`` runs the long
  connection (no public URL, no tunnel).
* ``EventDispatcherHandler.builder("", "").register_p2_card_action_trigger(...)``
  is the callback for card clicks; ``register_p2_im_message_receive_v1`` for
  plain messages. Both are on the *builder*, not the handler class.
* ``register_p2_card_action_trigger`` hands the handler a
  ``P2CardActionTrigger`` whose payload is nested under ``event`` (a
  ``P2CardActionTriggerData``), exactly like a plain message. So the click
  fields are ``data.event.operator.open_id``, ``data.event.action.value``,
  ``data.event.action.option``, and ``data.event.context.open_chat_id`` /
  ``data.event.context.open_message_id``. Reading the top level instead
  yields an all-empty no-op that looks like a dead button (on device, 2026-10-14).

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
            # lark-oapi nests the click under `event`; older builds hand it over
            # flat, and one real payload matched neither -- reading a single
            # shape turned a tap into an all-empty no-op that the service then
            # dropped as a non-owner event. Try every known shape, and say so
            # loudly when none of them fits.
            roots = [root for root in (_get(data, "event", None), data)
                     if root is not None]
            operator = _pick(roots, ("operator", "open_id"), ("open_id",)) or ""
            chat_id = _pick(roots, ("context", "open_chat_id"),
                            ("open_chat_id",), ("chat_id",)) or ""
            message_id = _pick(roots, ("context", "open_message_id"),
                               ("open_message_id",), ("message_id",)) or ""
            raw_value = _pick(roots, ("action", "value"), ("value",))
            raw_option = _pick(roots, ("action", "option"), ("option",))
            log.info("card action from %s: value=%r option=%r", operator, raw_value, raw_option)
            if not operator and not chat_id and not message_id \
                    and raw_value is None and raw_option is None:
                log.warning("card action payload matched no known shape: %s",
                            _payload(data))
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
    # SDK models *and* raw dicts both turn up -- one build handed a callback
    # over as a plain dict, where getattr() can only ever return the default.
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _pick(roots: list, *paths: tuple) -> Any:
    """First non-empty value found by walking each dotted path through `roots`.

    `roots` is ordered by preference (the nested payload first, the top level
    second), so the documented shape keeps winning when both are present.
    """
    for path in paths:
        for root in roots:
            cur = root
            for part in path:
                cur = _get(cur, part, None)
                if cur is None:
                    break
            if cur not in (None, ""):
                return cur
    return None


def _payload(data: Any) -> str:
    """Dump a payload we could not read; writing the dump must never raise."""
    marshal = getattr(getattr(lark, "JSON", None), "marshal", None)
    if marshal is not None:
        try:
            text = str(marshal(data))
        except Exception:  # noqa: BLE001 - diagnostics stay best-effort
            text = ""
        if text and text != "None":
            return text
    return repr(data)


def _extract_text(content: Any) -> str:
    """Feishu sends text messages as a JSON string: ``{"text": "..."}``."""
    if not isinstance(content, str):
        return ""
    try:
        return str(json.loads(content).get("text", "")).strip()
    except (ValueError, AttributeError):
        return ""
