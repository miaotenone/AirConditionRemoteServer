"""Assemble settings, MQTT and Feishu into the running service.

Timing (spec 5.5), all of it off the callback's critical path:

    click -> normalise -> optimistic card -> (debounce) -> ac/cmd
          -> wait for ac/state -> confirm, or "⚠️ 未确认" after confirm_timeout_s

Run it with ``python -m app.main``, or ``python -m app.main --print-card`` to
just dump the card JSON (no config needed) while iterating on the layout.
"""

from __future__ import annotations

import json
import logging
import queue
import sys
import threading
import time
from datetime import datetime

from .actions import normalize_action
from .cards import build_card
from .config import Settings
from .feishu_client import FeishuClient
from .intent import parse
from .mqtt_client import MqttBridge
from .replies import render
from .state import (
    AcState,
    Presence,
    apply_action,
    from_payload,
    is_stale,
    merge_actions,
    presence_from_state,
    presence_from_status,
)
from .store import CardStore

log = logging.getLogger(__name__)

#: Text that asks the bot to (re)send the remote-control card.
CARD_COMMANDS = ("/ac", "/start", "ac", "空调")


class Service:
    """Wires the two sides together and keeps the card in sync."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.store = CardStore(settings.db_path)

        #: `state` is the last truth the device reported.
        self.state = AcState()
        #: `view` is what the card shows -- it goes optimistic on a click.
        self.view = AcState()
        self.presence = Presence()

        self.pending = False        # a command is in flight -> show the hourglass
        self.unconfirmed = False    # nothing came back in time -> show the warning

        self._state_at = 0.0        # wall clock of the last ac/state
        self._confirm_deadline = 0.0
        self._rendered: str | None = None
        self._lock = threading.RLock()
        self._actions: queue.Queue = queue.Queue()
        self._stop = threading.Event()

        self.mqtt = MqttBridge(
            host=settings.mqtt_host,
            port=settings.mqtt_port,
            prefix=settings.mqtt_prefix,
            client_id=settings.mqtt_client_id,
            username=settings.mqtt_user,
            password=settings.mqtt_pass.get_secret_value(),
            on_state=self._on_mqtt_state,
            on_status=self._on_mqtt_status,
        )
        self.feishu = FeishuClient(
            app_id=settings.feishu_app_id,
            app_secret=settings.feishu_app_secret.get_secret_value(),
            on_action=self._on_card_action,
            on_text=self._on_text_message,
            log_level=settings.feishu_log_level,
        )

    # ------------------------------------------------------------------ startup

    def start(self) -> None:
        self.mqtt.start()
        self.feishu.start()
        threading.Thread(target=self._publish_worker, name="publisher", daemon=True).start()
        threading.Thread(target=self._tick, name="ticker", daemon=True).start()
        log.info("service up (owner=%s, prefix=%s)", self.settings.owner_open_id,
                 self.settings.mqtt_prefix)

    def stop(self) -> None:
        self._stop.set()
        self.mqtt.stop()
        self.store.close()

    # ------------------------------------------------------------- MQTT inbound

    def _on_mqtt_state(self, payload: dict) -> None:
        state = from_payload(payload)
        with self._lock:
            self.state = state
            self.presence = presence_from_state(self.presence, time.time())
            self._state_at = time.time()
            if self.pending and state.fingerprint() == self.view.fingerprint():
                self.pending = False            # our command landed
            if not self.pending:
                self.unconfirmed = False
                self.view = state               # nothing outstanding: truth wins
        self._render()

    def _on_mqtt_status(self, payload: dict) -> None:
        with self._lock:
            self.presence = presence_from_status(self.presence, payload)
        self._render()

    # ----------------------------------------------------------- Feishu inbound

    def _on_text_message(self, open_id: str, chat_id: str,
                         message_id: str, text: str) -> None:
        """Text is just another way to press a button.

        The parsed actions go into the *same* queue the card uses, so the
        debounce window, the optimistic update and the confirmation timeout are
        inherited rather than reimplemented (design 2).
        """
        if not self._is_owner(open_id):
            return

        if not self.settings.text_control_enabled:
            self._plain_text_fallback(chat_id, text)
            return

        if not self.store.mark_seen(message_id):
            log.info("ignoring redelivered message %s", message_id)
            return

        if text.lower() in CARD_COMMANDS:
            self._send_or_refresh_card(chat_id)     # "/ac" literally means "the card"
            return

        intent = parse(text)

        if intent.actions:
            with self._lock:
                for action, value in intent.actions:
                    self.view = apply_action(self.view, action, value)
                    self._actions.put((action, value))
                self.pending = True

        self._ensure_card(chat_id)
        self._render()

        with self._lock:
            receipt = render(
                intent, self.view, self.presence,
                stale=is_stale(self.presence, time.time(), self.settings.stale_after_s),
                updated_at=self._updated_at(),
            )
        self.feishu.reply_text(message_id, receipt)

    def _plain_text_fallback(self, chat_id: str, text: str) -> None:
        """The pre-text-control behaviour, kept for `TEXT_CONTROL_ENABLED=false`."""
        if text.lower() in CARD_COMMANDS:
            self._send_or_refresh_card(chat_id)
        else:
            log.info("ignoring message %r; send one of %s for the card", text, CARD_COMMANDS)

    def _ensure_card(self, chat_id: str) -> None:
        """Post the remote once, so a text-only user still has something to fix.

        Only ever creates: once the message_id is remembered, every later update
        is an in-place patch (design 5.2).
        """
        if self.store.get_message_id(self.settings.owner_open_id):
            return
        self._send_or_refresh_card(chat_id)

    def _on_card_action(self, open_id: str, chat_id: str, message_id: str,
                        value, option) -> None:
        """Fast path: authorise, normalise, go optimistic, queue. Never blocks."""
        if not self._is_owner(open_id):
            return
        action = normalize_action(value=value, option=option)
        if action is None:
            log.warning("ignoring unrecognised card action (value=%r option=%r)", value, option)
            return
        name, number = action

        with self._lock:
            if message_id and self.store.get_message_id(self.settings.owner_open_id) != message_id:
                # The card was re-posted elsewhere; remember the newest one.
                self.store.set_message_id(self.settings.owner_open_id, message_id)
            if name != "refresh":
                self.view = apply_action(self.view, name, number)
                self.pending = True
            # The debounced publisher does the actual work.
            self._actions.put(action)

        log.info("card action %s=%s from %s", name, number, open_id)
        self._render()

    # -------------------------------------------------------------- outbound work

    def _publish_worker(self) -> None:
        """Drain queued actions, coalescing a burst into one ac/cmd (spec 5.5)."""
        debounce = self.settings.debounce_ms / 1000.0
        while not self._stop.is_set():
            try:
                burst = [self._actions.get(timeout=0.5)]
            except queue.Empty:
                continue

            deadline = time.monotonic() + debounce
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    burst.append(self._actions.get(timeout=remaining))
                    deadline = time.monotonic() + debounce
                except queue.Empty:
                    break

            with self._lock:
                commands = merge_actions(self.state, burst)
            if not commands:
                continue            # e.g. a bare "refresh"
            if self.mqtt.publish_command(commands):
                with self._lock:
                    self._confirm_deadline = time.monotonic() + self.settings.confirm_timeout_s

    def _tick(self) -> None:
        """Timeout unconfirmed commands and refresh the staleness note."""
        while not self._stop.wait(1.0):
            changed = False
            with self._lock:
                if self.pending and time.monotonic() > self._confirm_deadline:
                    self.pending = False
                    self.unconfirmed = True
                    changed = True
            if changed:
                log.warning("no ac/state came back in %.1fs; marking unconfirmed",
                            self.settings.confirm_timeout_s)
                self._render()
            else:
                self._render()      # no-op unless staleness flipped

    # ------------------------------------------------------------------- rendering

    def _card(self) -> dict:
        return build_card(
            self.view,
            self.presence,
            pending=self.pending,
            unconfirmed=self.unconfirmed,
            stale=is_stale(self.presence, time.time(), self.settings.stale_after_s),
            updated_at=self._updated_at(),
        )

    def _updated_at(self) -> str:
        if not self._state_at:
            return "--:--:--"
        return datetime.fromtimestamp(self._state_at).strftime("%H:%M:%S")

    def _render(self) -> None:
        """Patch the card, but only when its content actually changed (spec 6)."""
        with self._lock:
            card = self._card()
            serialised = json.dumps(card, sort_keys=True)
            if serialised == self._rendered:
                return
            message_id = self.store.get_message_id(self.settings.owner_open_id)

        if not message_id:
            return              # nothing posted yet; the next "/ac" will create it
        if self.feishu.patch_card(message_id, card):
            with self._lock:
                self._rendered = serialised

    def _send_or_refresh_card(self, chat_id: str) -> None:
        with self._lock:
            existing = self.store.get_message_id(self.settings.owner_open_id)
            card = self._card()
            serialised = json.dumps(card, sort_keys=True)

        if existing:
            if self.feishu.patch_card(existing, card):
                with self._lock:
                    self._rendered = serialised
            return

        message_id = self.feishu.send_card(chat_id, card)
        if message_id:
            self.store.set_message_id(self.settings.owner_open_id, message_id)
            with self._lock:
                self._rendered = serialised
            log.info("card posted (message_id=%s)", message_id)

    def _is_owner(self, open_id: str) -> bool:
        if open_id == self.settings.owner_open_id:
            return True
        log.warning("ignoring event from non-owner %r", open_id)
        return False


# ------------------------------------------------------------------ entry point


def _print_card() -> None:
    demo = build_card(
        AcState(power=1, temp=24),
        Presence(online=True),
        updated_at="12:03:41",
    )
    text = json.dumps(demo, ensure_ascii=False, indent=2)
    try:
        # Windows consoles default to a legacy codepage (GBK here) that cannot
        # represent the emoji in the card; ask for UTF-8 where we can.
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError, OSError):
        pass
    try:
        print(text)
    except UnicodeEncodeError:
        print(json.dumps(demo, ensure_ascii=True, indent=2))


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--print-card" in argv:
        _print_card()
        return 0

    settings = Settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    service = Service(settings)
    service.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("shutting down")
        service.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
