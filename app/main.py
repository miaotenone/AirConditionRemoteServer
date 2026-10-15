"""Assemble settings, MQTT and Feishu into the running service.

Timing (spec 5.5), all of it off the callback's critical path:

    click -> normalise -> optimistic card -> (debounce) -> ac/cmd
          -> wait for ac/state -> confirm, or "⚠️ 未确认" after confirm_timeout_s

On top of that v1.2 adds four card-addressed features (design §9) and a 1 Hz
tick that also records the DHT11 history, feeds the device's heartbeat, runs
one-shot preset timers, pushes the daily weather suggestion and drives the
night closed loop. Every one of those reuses the same action queue, so they
inherit the debounce, the optimistic update and the confirmation timeout
rather than reimplementing them.

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
from dataclasses import replace
from datetime import datetime

from .actions import normalize_action, normalize_ui_action
from .cards import (
    HEADER_TITLE,
    MENU_MOVED_NOTICE_TEXT,
    build_card,
    build_history_card,
    build_moved_notice_card,
    build_preset_card,
    build_preset_form,
    build_settings_card,
    build_weather_card,
)
from .config import Settings
from .control import LoopState, MANUAL_YIELD_S, decide
from .env import aggregate, chart_points, parse_env
from .feishu_client import FeishuClient
from .intent import parse
from .mqtt_client import MqttBridge
from .preset import Preset, actions as preset_actions, normalize_name, resolve
from .replies import (
    render,
    render_history,
    render_night,
    render_preset_applied,
    render_preset_missing,
    render_weather,
)
from .schedule import (
    STATUS_DONE,
    STATUS_DROPPED,
    at_today,
    in_window,
    is_overdue,
    night_bounds,
)
from .state import (
    AC_FAN,
    AcState,
    Presence,
    apply_action,
    from_payload,
    is_stale,
    merge_actions,
    presence_from_state,
    presence_from_status,
)
from .store import (
    CARD_HISTORY,
    CARD_PRESET,
    CARD_REMOTE,
    CARD_SETTINGS,
    CARD_WEATHER,
    CardStore,
)
from .weather import fetch_now, recommend

log = logging.getLogger(__name__)

#: The summon word: posts a fresh card at the bottom of the chat and retires
#: the old one (design D22). The former words (`/ac`, `/start`, `ac`, `空调`)
#: now fall through to the parser as `unclear` like any other unknown sentence.
CARD_COMMANDS = ("设置卡片",)

#: Trailing sentence punctuation users add without thinking ("设置卡片。").
_TRAILING_PUNCTUATION = "。！？!?."

#: Settings-table keys, so the card can change them without a redeploy.
S_PUSH_TIME = "push_time"
S_NIGHT_ENABLED = "night_enabled"
S_NIGHT_START = "night_start"
S_NIGHT_END = "night_end"
S_NIGHT_TARGET = "night_target"
S_CHAT_ID = "chat_id"
S_WEATHER_PUSHED_AT = "weather_pushed_at"

#: How far the entry word / a timer may drift before we call it missed.
_DAILY_WINDOW_S = 60.0

#: A click's card work waits this long before it touches Feishu.
#:
#: Feishu keeps a card update only when it is sent *after* the callback
#: response: sent from inside the handler, the client paints the new card and
#: then snaps back to the old one, which is exactly the "点新建没反应" report
#: (FAQ "Why does the card not update successfully or revert immediately after
#: a successful update when clicking the card?" -- the update "must be executed
#: after responding to the callback request"). lark-oapi writes that response
#: only once the handler returns (``lark_oapi/ws/client.py`` ->
#: ``_handle_data_frame``), so this settle time is the gap that lets the ack
#: leave first.
CALLBACK_SETTLE_S = 0.2


def _is_card_command(text: str) -> bool:
    """True when `text` is the summon word, tolerating stray punctuation.

    Comparison only: the original `text` is still what gets handed to
    `parse()`, so this never changes how any other sentence is read.
    """
    key = text.strip().rstrip(_TRAILING_PUNCTUATION).strip()
    return key.lower() in CARD_COMMANDS


class Service:
    """Wires the two sides together and keeps the cards in sync."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.store = CardStore(settings.db_path)

        #: `state` is the last truth the device reported.
        self.state = AcState()
        #: `view` is what the card shows -- it goes optimistic on a click.
        self.view = AcState()
        self.presence = Presence()

        #: Latest DHT11 reading, and when it arrived (design §9.1).
        self.env = None
        self.env_at = 0.0

        self.pending = False        # a command is in flight -> show the hourglass
        self.unconfirmed = False    # nothing came back in time -> show the warning

        self._state_at = 0.0        # wall clock of the last ac/state
        self._confirm_deadline = 0.0
        self._rendered: dict[str, str] = {}
        self._loop = LoopState()    # the night loop's memory
        self._last_env_record = 0.0
        self._last_hb = 0.0
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
            on_env=self._on_mqtt_env,
            on_event=self._on_mqtt_event,
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

    def _on_mqtt_env(self, payload: dict, retained: bool) -> None:
        """A DHT11 reading from the device (design §9.1).

        A retained message is what the broker replays to a fresh subscriber:
        it is history, not a reading, so it is recorded nowhere and acted on
        never -- otherwise a restart would look like a temperature that jumped
        at 3 a.m. (design 18.6).
        """
        if retained:
            log.info("ignoring retained %s replay", self.mqtt.topics["env"])
            return
        reading = parse_env(payload)
        with self._lock:
            self.env = reading
            self.env_at = time.time()

    def _on_mqtt_event(self, payload: dict, retained: bool) -> None:
        """A device-originated event: currently only the fail-safe trip.

        ``ac/event`` is retained, so after a service restart the broker replays
        the last one. Both the retain flag and the event's own ``ts`` are
        checked before acting: a replay is history, not news (design 18.8).
        """
        kind = payload.get("type")
        ts = payload.get("ts")
        if kind != "failsafe_off":
            log.info("ignoring device event %r", kind)
            return
        if retained:
            log.info("ignoring retained fail-safe replay")
            return
        if not isinstance(ts, (int, float)) or ts < 1_000_000_000:
            # The device withholds events until SNTP has given it a real clock,
            # so a zero/absent ts means this is not something we can age-check.
            log.info("ignoring fail-safe event without a usable timestamp (ts=%r)", ts)
            return
        if time.time() - ts > 600:
            log.info("ignoring stale fail-safe event (ts=%r)", ts)
            return

        previous = payload.get("prev") or {}
        log.warning("device fail-safe turned the AC off; previous state %r", previous)
        with self._lock:
            self.state = replace(self.state, power=0)
            if not self.pending:
                self.view = replace(self.view, power=0)
        self._render()
        self._restore_after_failsafe(previous)

    def _restore_after_failsafe(self, previous: dict) -> bool:
        """Put the AC back the way it was, once we are talking again.

        Only for a unit that was actually *on*: if it was already off, the
        fail-safe changed nothing and re-sending would be a surprise.
        """
        try:
            if int(previous.get("power", 0)) != 1:
                return False
            actions = [(field, int(previous[field]))
                       for field in ("power", "mode", "temp", "fan")
                       if field in previous]
        except (TypeError, ValueError):
            log.warning("fail-safe event carried an unreadable prev state: %r", previous)
            return False
        if not actions:
            return False

        log.warning("restoring the pre-fail-safe state: %s", actions)
        self._queue_actions(actions)
        chat_id = self._chat_id()
        if chat_id:
            # Called from the MQTT thread, so the Feishu round trip is handed
            # off rather than blocking the client loop.
            self._run_async(self.feishu.send_text, chat_id,
                            "🔌 设备失联已自动关机，重新连上后已恢复原先的空调状态")
        return True

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

        if chat_id:
            self.store.set_setting(S_CHAT_ID, chat_id)

        if _is_card_command(text):
            # Summon: a new card at the newest position, no text receipt --
            # the card showing up where the message landed is the feedback.
            self._summon_card(chat_id)
            return

        intent = parse(text)

        if intent.kind in ("history", "night", "weather", "preset"):
            # Data-driven kinds answer from the store/network, not from the
            # AC's action grammar (design 9.4).
            self._handle_context_intent(intent, chat_id, message_id)
            return

        with self._lock:
            # `render` re-applies the actions itself to show "what it will be";
            # hand it the state *before* this message or a relative action is
            # counted twice. On device: ac/cmd {"temp":28} next to a receipt of
            # 26 (30 -> 28 -> 26). Acceptance #11 wants 22/20/18, not 20/18/16.
            before = self.view
            if intent.actions:
                for action, value in intent.actions:
                    self.view = apply_action(self.view, action, value)
                    self._actions.put((action, value))
                self.pending = True
                if intent.actions:
                    self._loop = replace(self._loop, manual_until=time.time() + MANUAL_YIELD_S)

        self._ensure_card(chat_id)
        self._render()

        with self._lock:
            receipt = render(
                intent, before, self.presence,
                stale=is_stale(self.presence, time.time(), self.settings.stale_after_s),
                updated_at=self._updated_at(),
            )
        self.feishu.reply_text(message_id, receipt)

    def _plain_text_fallback(self, chat_id: str, text: str) -> None:
        """The pre-text-control behaviour, kept for `TEXT_CONTROL_ENABLED=false`."""
        if _is_card_command(text):
            self._summon_card(chat_id)
        else:
            log.info("ignoring message %r; send one of %s for the card", text, CARD_COMMANDS)

    def _handle_context_intent(self, intent, chat_id: str, message_id: str) -> None:
        """Answer the four data-driven kinds (design 9.4)."""
        if intent.kind == "history":
            self._history_answer(intent.hint, chat_id, message_id)
        elif intent.kind == "night":
            self.feishu.reply_text(message_id, self._night_switch(intent.hint))
        elif intent.kind == "weather":
            self._weather_now(chat_id, message_id)
        elif intent.kind == "preset":
            self._preset_intent(intent, chat_id, message_id)

    def _history_view(self, window: str):
        """``(summary, chart points, target)`` for one window.

        One reader for text and picture: if these two ever disagree the card
        would contradict the sentence sitting right above it.
        """
        samples = self.store.env_samples()
        now = datetime.now()
        start, end = self._night_start(), self._night_end()
        target = self._night_target() if self._night_enabled() else None
        return (
            aggregate(samples, now=now, window=window,
                      night_start=start, night_end=end),
            chart_points(samples, now=now, window=window,
                         night_start=start, night_end=end),
            target,
        )

    def _history_answer(self, window: str, chat_id: str, message_id: str) -> None:
        """A history query: always the line of text, plus the card below it.

        The card is sent to the bottom (a menu tap comes from wherever the user
        is standing) and only when the window has a shape to draw -- one point
        is not a line, and "还没有记录" already says everything there is to say.
        """
        summary, points, target = self._history_view(window)
        self.feishu.reply_text(message_id, render_history(summary, target=target))
        self._history_card(summary, points, target, chat_id, to_bottom=True)

    def _history_card(self, summary, points, target, chat_id: str, *,
                      prefer: str | None = None, to_bottom: bool = False) -> None:
        if len(points) < 2:
            return
        self._show_card(CARD_HISTORY, chat_id,
                        build_history_card(summary, target=target, points=points),
                        prefer=prefer, to_bottom=to_bottom)

    def _night_switch(self, mode: str) -> str:
        if mode == "on":
            self.store.set_setting(S_NIGHT_ENABLED, "1")
        elif mode == "off":
            self.store.set_setting(S_NIGHT_ENABLED, "0")
        text = render_night(mode, enabled=self._night_enabled(),
                            target=self._night_target(),
                            start=self._night_start(), end=self._night_end())
        if mode != "status":
            self._show_settings_card(self._chat_id(), to_bottom=True)
        return text

    def _run_async(self, fn, *args) -> None:
        """Hand slow work off the Feishu callback thread.

        The SDK calls the handlers on its own receive thread and expects them
        back promptly; a 10 s HTTPS round trip in there would stall every other
        message and button press (design 5.5). Overridden in tests so the
        dispatch stays deterministic.
        """
        threading.Thread(target=fn, args=args, name="ac-async", daemon=True).start()

    def _run_after_callback(self, fn, *args) -> None:
        """Run a click's card work only once the callback response is out.

        Feishu discards a card update that is sent before it has answered the
        callback: the client shows the new content for a moment and then puts
        the old one back ("闪一下又没了"). So every branch of a click -- the
        preset/settings/weather cards and the remote card's optimistic render --
        runs here, after the SDK has had time to write its ack. Overridden in
        tests so the dispatch stays deterministic.
        """
        def follow_up() -> None:
            if self._stop.wait(CALLBACK_SETTLE_S):
                return              # shutting down: don't start new work
            fn(*args)

        threading.Thread(target=follow_up, name="card-followup",
                         daemon=True).start()

    def _weather_now(self, chat_id: str, message_id: str) -> None:
        # The fetch is the one slow thing a text command can trigger, so it
        # never runs inline here.
        self._run_async(self._weather_now_blocking, chat_id, message_id)

    def _weather_now_blocking(self, chat_id: str, message_id: str) -> None:
        now, recommendation = self._weather_snapshot()
        self.feishu.reply_text(message_id, render_weather(now, recommendation))
        if now is not None and recommendation is not None:
            self._show_card(CARD_WEATHER, chat_id,
                            build_weather_card(now, recommendation),
                            to_bottom=True)

    def _preset_intent(self, intent, chat_id: str, message_id: str) -> None:
        hint = intent.hint or ""
        if hint == "open":
            self._show_preset_card(chat_id, status="", to_bottom=True)
            self.feishu.reply_text(message_id, render(
                intent, self.view, self.presence, updated_at=self._updated_at()))
            return
        if hint.startswith("apply:"):
            name = hint.split(":", 1)[1]
            preset = self.store.get_preset_by_name(name)
            if preset is None:
                self.feishu.reply_text(message_id, render_preset_missing(name))
                return
            self._apply_preset(preset, chat_id, message_id)
            return
        log.warning("unhandled preset hint %r", hint)

    # ------------------------------------------------------- card clicks / forms

    def _on_card_action(self, open_id: str, chat_id: str, message_id: str,
                        value, option, form_value=None, card_name=None) -> None:
        """Fast path: authorise, normalise, go optimistic, queue.

        Every *Feishu write* a click causes is handed to a follow-up thread: the
        SDK calls this on the thread that still owes Feishu a callback response,
        and an update sent before that response lands is thrown away by the
        client (it paints the new card, then snaps back to the old one -- the
        "点了没反应" report). State changes stay here: the AC path's optimistic
        view and the action queue feed the debounce, and must not gain latency.
        """
        if not self._is_owner(open_id):
            return
        if chat_id:
            self.store.set_setting(S_CHAT_ID, chat_id)

        ui_action = normalize_ui_action(value=value, form_value=form_value)
        if ui_action is not None:
            # The preset/settings/weather cards: the answer is a card patch, so
            # the whole branch can wait for the ack.
            self._run_after_callback(self._handle_ui_action, ui_action,
                                     chat_id, message_id)
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
                self._loop = replace(self._loop, manual_until=time.time() + MANUAL_YIELD_S)
            # The debounced publisher does the actual work.
            self._actions.put(action)

        log.info("card action %s=%s from %s", name, number, open_id)
        # The hourglass is a card write like any other: after the ack.
        self._run_after_callback(self._render)

    def _handle_ui_action(self, ui_action, chat_id: str, message_id: str) -> None:
        """Presets, settings and the weather card -- never an AC command.

        Every branch ends by patching the card that was clicked, so the answer
        appears where the finger is (design Q3).
        """
        kind = ui_action.kind

        if kind == "preset_open":
            self._show_preset_card(chat_id, status="", prefer=message_id)
        elif kind == "preset_new":
            self._show_card(CARD_PRESET, chat_id, build_preset_form(),
                            prefer=message_id)
        elif kind == "preset_edit":
            preset = self.store.get_preset(ui_action.target)
            if preset is None:
                self._show_preset_card(chat_id, status="❌ 这个预设已经不在了",
                                       prefer=message_id)
            else:
                self._show_card(CARD_PRESET, chat_id, build_preset_form(preset),
                                prefer=message_id)
        elif kind == "preset_delete":
            self.store.delete_preset(ui_action.target)
            self._show_preset_card(chat_id, status="✅ 已删除", prefer=message_id)
        elif kind == "preset_apply":
            preset = self.store.get_preset(ui_action.target)
            if preset is None:
                self._show_preset_card(chat_id, status="❌ 这个预设已经不在了",
                                       prefer=message_id)
            else:
                status = self._apply_preset(preset, chat_id, message_id=None)
                self._show_preset_card(chat_id, status=status, prefer=message_id)
        elif kind == "preset_save":
            self._save_preset_form(ui_action.form, chat_id, message_id)
        elif kind == "settings_open":
            self._show_settings_card(chat_id, prefer=message_id)
        elif kind == "settings_save":
            self._save_settings_form(ui_action.form, chat_id, message_id)
        elif kind == "night_toggle":
            self.store.set_setting(S_NIGHT_ENABLED, "1" if ui_action.target else "0")
            self._show_settings_card(chat_id, status="✅ 已更新", prefer=message_id)
        elif kind == "history_switch":
            summary, points, target = self._history_view(ui_action.window)
            self._history_card(summary, points, target, chat_id, prefer=message_id)
        elif kind == "weather_exec":
            self._execute_weather(chat_id, message_id)
        elif kind == "weather_ignore":
            self._show_card(CARD_WEATHER, chat_id,
                            build_weather_card(self._last_weather[0],
                                               self._last_weather[1],
                                               status="已忽略今天的建议"),
                            prefer=message_id)
        else:  # pragma: no cover - the vocabulary is closed
            log.warning("unhandled ui action %r", kind)

    # ------------------------------------------------------------ card plumbing

    def _show_card(self, kind: str, chat_id: str, card: dict,
                   *, prefer: str | None = None, to_bottom: bool = False) -> str | None:
        """Patch the card this kind already owns; post one if there is none.

        Each kind owns its own message (design 18.1), so patching the remote
        card can no longer clobber the preset list, and clicking a card makes
        that card the one we keep updating.

        ``to_bottom`` is for the *menu taps*. The button paths are answered in
        place because the user is looking right at that card, but a menu tap
        comes from wherever they are standing in the chat: patching a card that
        is screens above them looks like nothing happened (the receipt even
        promises "👇"). So send a fresh card there, move the pointer, then
        retire the old one -- send first, so a failed send never costs us the
        card we already had (design D22).
        """
        owner = self.settings.owner_open_id
        serialised = json.dumps(card, sort_keys=True)
        if not to_bottom:
            message_id = prefer or self.store.get_card(owner, kind)
            if message_id and self.feishu.patch_card(message_id, card):
                self.store.set_card(owner, kind, message_id)
                self._rendered[kind] = serialised
                return message_id
        if not chat_id:
            return None
        old = self.store.get_card(owner, kind)
        new_id = self.feishu.send_card(chat_id, card)
        if new_id:
            self.store.set_card(owner, kind, new_id)
            self._rendered[kind] = serialised
            if to_bottom and old and old != new_id:
                self._retire_card(old, kind, card)
        return new_id

    def _retire_card(self, old: str, kind: str, card: dict) -> None:
        """Recall the card the user has walked away from; notice it if too old."""
        if self.feishu.recall_card(old):
            return
        title = (card.get("header", {}).get("title") or {}).get("content") \
            or HEADER_TITLE
        if not self.feishu.patch_card(
                old, build_moved_notice_card(title, text=MENU_MOVED_NOTICE_TEXT)):
            log.warning("could not recall or downgrade the old %s card %s", kind, old)

    def _show_preset_card(self, chat_id: str, *, status: str = "",
                          prefer: str | None = None, to_bottom: bool = False) -> None:
        self._show_card(CARD_PRESET, chat_id,
                        build_preset_card(self.store.list_presets(), status=status),
                        prefer=prefer, to_bottom=to_bottom)

    def _show_settings_card(self, chat_id: str, *, status: str = "",
                            prefer: str | None = None, to_bottom: bool = False) -> None:
        self._show_card(CARD_SETTINGS, chat_id,
                        build_settings_card(self._setting_map(),
                                            night_enabled=self._night_enabled(),
                                            status=status),
                        prefer=prefer, to_bottom=to_bottom)

    # --------------------------------------------------------------- presets

    def _save_preset_form(self, form, chat_id: str, message_id: str) -> None:
        preset_id = 0
        raw_id = form.get("preset_id")
        try:
            preset_id = int(raw_id) if raw_id else 0
        except (TypeError, ValueError):
            preset_id = 0

        preset = self._preset_from_form(form, preset_id=preset_id)
        if not preset.name:
            self._show_card(CARD_PRESET, chat_id,
                            build_preset_form(preset, status="❌ 先起个名字"),
                            prefer=message_id)
            return
        self.store.save_preset(preset)
        self._show_preset_card(chat_id, status=f"✅ 已保存「{preset.name}」",
                               prefer=message_id)

    @staticmethod
    def _preset_from_form(form, *, preset_id: int = 0) -> Preset:
        def number(key: str, default: int) -> int:
            try:
                return int(form.get(key))
            except (TypeError, ValueError):
                return default

        timer_at = str(form.get("at") or "").strip()
        return Preset(
            id=preset_id,
            name=normalize_name(str(form.get("name") or "")),
            power=number("power", 1),
            mode=number("mode", 1),
            temp=number("temp", 24),
            fan=number("fan", AC_FAN["auto"]),
            timer_enabled=bool(timer_at),
            timer_at=timer_at,
        )

    def _apply_preset(self, preset: Preset, chat_id: str,
                      message_id: str | None = None) -> str:
        """Clicking a preset: run it now, or book it once for later today."""
        when, at = resolve(preset, datetime.now())
        if when == "at" and at is not None:
            self.store.add_schedule("preset", {"preset_id": preset.id}, at)
            text = render_preset_applied(preset, "at", at)
        else:
            self._queue_actions(preset_actions(preset))
            text = render_preset_applied(preset, "immediate", None)
        if message_id:
            self.feishu.reply_text(message_id, text)
        log.info("preset %r -> %s", preset.name, text)
        return text

    # -------------------------------------------------------------- settings

    def _setting_map(self) -> dict:
        return {
            "push_time": self._push_time(),
            "night_start": self._night_start(),
            "night_end": self._night_end(),
            "night_target": str(self._night_target()),
        }

    def _save_settings_form(self, form, chat_id: str, message_id: str) -> None:
        if form.get("push_time"):
            self.store.set_setting(S_PUSH_TIME, str(form["push_time"]).strip())
        if form.get("night_start"):
            self.store.set_setting(S_NIGHT_START, str(form["night_start"]).strip())
        if form.get("night_end"):
            self.store.set_setting(S_NIGHT_END, str(form["night_end"]).strip())
        if form.get("night_target"):
            self.store.set_setting(S_NIGHT_TARGET, str(form["night_target"]).strip())
        self._show_settings_card(chat_id, status="✅ 设置已保存", prefer=message_id)

    # ------------------------------------------------------------------ weather

    #: (WeatherNow, Recommendation) from the last fetch, for the "忽略" patch.
    _last_weather: tuple = (None, None)

    def _weather_snapshot(self) -> tuple:
        """Fetch and judge the weather once. ``(None, None)`` when it fails."""
        now = fetch_now(lat=self.settings.weather_lat, lon=self.settings.weather_lon)
        if now is None:
            return (None, None)
        return (now, recommend(now.outdoor_c, now.next2h_delta_c))

    def _execute_weather(self, chat_id: str, message_id: str) -> None:
        now, recommendation = self._last_weather
        if now is None or recommendation is None:
            now, recommendation = self._weather_snapshot()
        if now is None or recommendation is None:
            self._show_card(CARD_WEATHER, chat_id,
                            build_weather_card(None, None, status="天气没拿到"),
                            prefer=message_id)
            return
        actions: list[tuple[str, int]] = [("mode", recommendation.mode)]
        if recommendation.temp_used:
            actions.append(("temp", recommendation.temp))
        self._queue_actions(actions)
        self._show_card(CARD_WEATHER, chat_id,
                        build_weather_card(now, recommendation,
                                           status="✅ 已按建议执行"),
                        prefer=message_id)

    def _push_weather(self, chat_id: str) -> None:
        now, recommendation = self._weather_snapshot()
        self._last_weather = (now, recommendation)
        if now is None or recommendation is None:
            log.info("weather push skipped: no data")
            return
        self._show_card(CARD_WEATHER, chat_id,
                        build_weather_card(now, recommendation))

    # -------------------------------------------------------------------- tick

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
        """One second of housekeeping: timers, history, heartbeat, night loop."""
        while not self._stop.wait(1.0):
            now = time.time()
            self._expire_pending()
            self._record_env(now)
            self._heartbeat(now)
            self._run_due_schedules(now)
            self._maybe_push_weather(now)
            self._night_step(now)
            self._render()          # no-op unless something actually changed

    def _expire_pending(self) -> None:
        changed = False
        with self._lock:
            if self.pending and time.monotonic() > self._confirm_deadline:
                self.pending = False
                self.unconfirmed = True
                changed = True
        if changed:
            log.warning("no ac/state came back in %.1fs; marking unconfirmed",
                        self.settings.confirm_timeout_s)

    def _record_env(self, now: float) -> None:
        """Append the latest good reading every `dht_record_s` (design §9.1)."""
        if now - self._last_env_record < self.settings.dht_record_s:
            return
        with self._lock:
            reading = self.env
            stale = self.env_at and (now - self.env_at) > 600
        if reading is None or not reading.ok or stale:
            return
        self._last_env_record = now
        self.store.add_env_sample(now, reading.temp, reading.humidity, True)

    def _heartbeat(self, now: float) -> None:
        """Prove to the device that this service is alive (design 18.8)."""
        if now - self._last_hb < self.settings.hb_interval_s:
            return
        self._last_hb = now
        self.mqtt.publish_heartbeat()

    def _run_due_schedules(self, now: float) -> None:
        """Fire the one-shot preset timers, dropping the badly-missed ones."""
        moment = datetime.now()
        for job in self.store.list_schedules(status="pending"):
            if job.run_at > now:
                break
            if is_overdue(job.run_at, moment):
                self.store.set_schedule_status(job.id, STATUS_DROPPED)
                log.info("dropping overdue schedule %s (%s)", job.id, job.kind)
                continue
            self._run_schedule(job)
            self.store.set_schedule_status(job.id, STATUS_DONE)

    def _run_schedule(self, job) -> None:
        if job.kind != "preset":
            log.warning("unknown schedule kind %r", job.kind)
            return
        preset = self.store.get_preset(int(job.payload.get("preset_id", 0)))
        if preset is None:
            log.info("scheduled preset %r no longer exists", job.payload)
            return
        self._queue_actions(preset_actions(preset))
        chat_id = self._chat_id()
        if chat_id:
            self.feishu.send_text(chat_id, f"⏰ 已按预约执行「{preset.name}」")

    def _maybe_push_weather(self, now: float) -> None:
        """Push the daily suggestion once, at the configured time (design §9.3)."""
        push = self._push_time()
        at = at_today(push, datetime.now())
        if at is None or not (0 <= now - at < _DAILY_WINDOW_S):
            return
        last = self._setting_float(S_WEATHER_PUSHED_AT)
        if now - last < 6 * 3600:          # already pushed for this occurrence
            return
        self.store.set_setting(S_WEATHER_PUSHED_AT, str(now))
        chat_id = self._chat_id()
        if not chat_id:
            log.info("weather push skipped: no chat to send to yet")
            return
        log.info("pushing the daily weather suggestion")
        self._push_weather(chat_id)

    def _night_step(self, now: float) -> None:
        """One pass of the night closed loop (design §7.3)."""
        if not self._night_enabled():
            return
        moment = datetime.now()
        start, end = self._night_start(), self._night_end()
        if not in_window(moment, start, end):
            return

        with self._lock:
            reading = self.env
            stale = (not self.env_at) or (now - self.env_at) > 600
        room = reading.temp if (reading is not None and reading.ok and not stale) else None

        bounds = night_bounds(moment, start, end)
        history = []
        if bounds is not None:
            samples = self.store.env_samples(since=bounds[0])
            history = [row.temp for row in samples][-6:]

        with self._lock:
            decision = decide(room, target=self._night_target(), ac=self.view,
                              now=now, state=self._loop, history=history)
        if not decision.actions:
            return
        log.info("night loop: %s (%s)", decision.actions, decision.reason)
        self._queue_actions(list(decision.actions))
        with self._lock:
            self._loop = replace(self._loop, last_action_at=now)

    # ------------------------------------------------------------------ helpers

    def _queue_actions(self, actions) -> None:
        """Send actions down the one shared path: optimistic view + queue."""
        if not actions:
            return
        with self._lock:
            for action, value in actions:
                self.view = apply_action(self.view, action, value)
                self._actions.put((action, value))
            self.pending = True

    def _chat_id(self) -> str:
        return self.store.get_setting(S_CHAT_ID, "")

    def _setting_float(self, key: str, default: float = 0.0) -> float:
        try:
            return float(self.store.get_setting(key, str(default)) or default)
        except ValueError:
            return default

    def _push_time(self) -> str:
        return self.store.get_setting(S_PUSH_TIME, self.settings.weather_default_push)

    def _night_enabled(self) -> bool:
        return self.store.get_setting(S_NIGHT_ENABLED, "0") == "1"

    def _night_start(self) -> str:
        return self.store.get_setting(S_NIGHT_START, self.settings.night_default_start)

    def _night_end(self) -> str:
        return self.store.get_setting(S_NIGHT_END, self.settings.night_default_end)

    def _night_target(self) -> int:
        try:
            return int(self.store.get_setting(S_NIGHT_TARGET,
                                              str(self.settings.night_default_target)))
        except ValueError:
            return self.settings.night_default_target

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
        """Patch the remote card, but only when its content actually changed."""
        with self._lock:
            card = self._card()
            serialised = json.dumps(card, sort_keys=True)
            if serialised == self._rendered.get(CARD_REMOTE):
                return
            message_id = self.store.get_card(self.settings.owner_open_id, CARD_REMOTE)

        if not message_id:
            return              # nothing posted yet; the next "设置卡片" creates it
        if self.feishu.patch_card(message_id, card):
            with self._lock:
                self._rendered[CARD_REMOTE] = serialised

    def _ensure_card(self, chat_id: str) -> None:
        """Post the remote once, so a text-only user still has something to fix.

        Only ever creates: once the message_id is remembered, every later update
        is an in-place patch (design 5.2).
        """
        if self.store.get_message_id(self.settings.owner_open_id):
            return
        self._send_or_refresh_card(chat_id)

    def _summon_card(self, chat_id: str) -> None:
        """Summon the card to the newest position in the chat (design D22).

        Send first, switch the pointer, then retire the old card -- in that
        order, so a failed send never costs us the card we already had. If the
        recall fails (Feishu only allows ~24 h), the old card is downgraded to
        a one-line notice; if even that patch fails, give up with a warning --
        the old card is still harmless, because clicking it re-points the store
        at itself (`_on_card_action`). No text receipt either way.
        """
        owner = self.settings.owner_open_id
        old = self.store.get_message_id(owner)
        with self._lock:
            card = self._card()
            serialised = json.dumps(card, sort_keys=True)

        message_id = self.feishu.send_card(chat_id, card)
        if not message_id:
            return                      # send failed: keep the old card as-is
        self.store.set_message_id(owner, message_id)
        with self._lock:
            self._rendered[CARD_REMOTE] = serialised
        log.info("card summoned (message_id=%s, previous=%s)", message_id, old)

        if not old or old == message_id:
            return
        if self.feishu.recall_card(old):
            return
        if not self.feishu.patch_card(old, build_moved_notice_card()):
            log.warning("could not recall or downgrade the old card %s", old)

    def _send_or_refresh_card(self, chat_id: str) -> None:
        with self._lock:
            existing = self.store.get_message_id(self.settings.owner_open_id)
            card = self._card()
            serialised = json.dumps(card, sort_keys=True)

        if existing:
            if self.feishu.patch_card(existing, card):
                with self._lock:
                    self._rendered[CARD_REMOTE] = serialised
            return

        message_id = self.feishu.send_card(chat_id, card)
        if message_id:
            self.store.set_message_id(self.settings.owner_open_id, message_id)
            with self._lock:
                self._rendered[CARD_REMOTE] = serialised
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
