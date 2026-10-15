"""SQLite persistence for the bridge.

Three kinds of thing live here, all of them "what we must still know after a
restart":

* **which message to keep editing**, now keyed by *card kind* -- the remote, the
  preset list, the settings card and the weather push each own a message
  (design 18.1), so one stale pointer cannot clobber another card;
* **presets / one-shot schedules / environment history / settings** -- the
  durable state behind presets, the DHT11 feedback and the night loop
  (design §10);
* **message ids already acted on**, so a Feishu redelivery cannot run a
  relative command twice (design 5.3).

Pure models (`preset`, `schedule`, `env`) stay importable without SQLite; this
module is the only place that touches the database.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Iterable, Optional, Union

from .env import EnvSample
from .preset import Preset
from .schedule import STATUS_PENDING, Schedule

#: The card kinds that each keep their own message (design 18.1).
CARD_REMOTE = "remote"
CARD_PRESET = "preset"
CARD_SETTINGS = "settings"
CARD_WEATHER = "weather"
CARD_HISTORY = "history"

_CARD_SCHEMA = """
CREATE TABLE IF NOT EXISTS card (
    owner      TEXT NOT NULL,
    kind       TEXT NOT NULL,
    message_id TEXT NOT NULL,
    PRIMARY KEY (owner, kind)
)
"""

#: Messages already acted on. Feishu may redeliver an event, and a relative
#: command ("调低两度") is not idempotent -- running it twice moves the setpoint
#: twice (design 5.3).
_SEEN_SCHEMA = """
CREATE TABLE IF NOT EXISTS seen (
    message_id TEXT PRIMARY KEY,
    at         REAL NOT NULL
)
"""

_PRESET_SCHEMA = """
CREATE TABLE IF NOT EXISTS preset (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL UNIQUE,
    power         INTEGER NOT NULL,
    mode          INTEGER NOT NULL,
    temp          INTEGER NOT NULL,
    fan           INTEGER NOT NULL,
    timer_enabled INTEGER NOT NULL DEFAULT 0,
    timer_at      TEXT NOT NULL DEFAULT '',
    updated_at    REAL NOT NULL
)
"""

_SCHEDULE_SCHEMA = """
CREATE TABLE IF NOT EXISTS schedule (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    kind       TEXT NOT NULL,
    payload    TEXT NOT NULL,
    run_at     REAL NOT NULL,
    status     TEXT NOT NULL DEFAULT 'pending',
    created_at REAL NOT NULL
)
"""

_ENV_SCHEMA = """
CREATE TABLE IF NOT EXISTS env_history (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    at       REAL NOT NULL,
    temp     INTEGER NOT NULL,
    humidity INTEGER,
    ok       INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS env_history_at ON env_history (at)
"""

_SETTINGS_SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
)
"""

_SCHEMAS = (_CARD_SCHEMA, _SEEN_SCHEMA, _PRESET_SCHEMA, _SCHEDULE_SCHEMA,
            _ENV_SCHEMA, _SETTINGS_SCHEMA)

_PRESET_COLUMNS = "id, name, power, mode, temp, fan, timer_enabled, timer_at"


class CardStore:
    """Remembers cards, presets, schedules, readings and settings."""

    def __init__(self, path: Union[str, Path] = "ac-bot.sqlite3") -> None:
        # The MQTT and Feishu callbacks run on different threads.
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._migrate_card_table()
        for statement in _SCHEMAS:
            self._conn.executescript(statement)
        self._conn.commit()

    def _migrate_card_table(self) -> None:
        """Upgrade a v1 ``card(owner, message_id)`` table to the by-kind one.

        Without this, a restart on an existing database would find "no remote
        card" and post a brand new one -- exactly the chat spam design 2.3
        forbids. The old row becomes the remote card.
        """
        columns = [row[1] for row in
                   self._conn.execute("PRAGMA table_info(card)").fetchall()]
        if not columns or "kind" in columns:
            return
        self._conn.execute("ALTER TABLE card RENAME TO card_legacy")
        self._conn.execute(_CARD_SCHEMA)
        self._conn.execute(
            "INSERT INTO card (owner, kind, message_id) "
            "SELECT owner, ?, message_id FROM card_legacy", (CARD_REMOTE,))
        self._conn.execute("DROP TABLE card_legacy")
        self._conn.commit()

    # ------------------------------------------------------------------ cards

    def get_card(self, owner: str, kind: str) -> Optional[str]:
        row = self._conn.execute(
            "SELECT message_id FROM card WHERE owner = ? AND kind = ?", (owner, kind)
        ).fetchone()
        return row[0] if row else None

    def set_card(self, owner: str, kind: str, message_id: str) -> None:
        self._conn.execute(
            "INSERT INTO card (owner, kind, message_id) VALUES (?, ?, ?) "
            "ON CONFLICT(owner, kind) DO UPDATE SET message_id = excluded.message_id",
            (owner, kind, message_id),
        )
        self._conn.commit()

    def clear_card(self, owner: str, kind: str) -> None:
        self._conn.execute(
            "DELETE FROM card WHERE owner = ? AND kind = ?", (owner, kind))
        self._conn.commit()

    # Backwards-compatible aliases: the remote card is the default one.
    def get_message_id(self, owner: str) -> Optional[str]:
        return self.get_card(owner, CARD_REMOTE)

    def set_message_id(self, owner: str, message_id: str) -> None:
        self.set_card(owner, CARD_REMOTE, message_id)

    def clear_message_id(self, owner: str) -> None:
        self.clear_card(owner, CARD_REMOTE)

    # ------------------------------------------------------------------ seen

    def mark_seen(self, message_id: str) -> bool:
        """True the first time we see a message, False if it is a redelivery.

        An empty id means the event carried nothing to dedupe on, so it is
        treated as new every time rather than silently swallowing messages.
        """
        if not message_id:
            return True
        cursor = self._conn.execute(
            "INSERT INTO seen (message_id, at) VALUES (?, ?) "
            "ON CONFLICT(message_id) DO NOTHING",
            (message_id, time.time()),
        )
        self._conn.commit()
        return cursor.rowcount > 0

    # --------------------------------------------------------------- presets

    def list_presets(self) -> list[Preset]:
        rows = self._conn.execute(
            f"SELECT {_PRESET_COLUMNS} FROM preset ORDER BY id").fetchall()
        return [_preset_from_row(row) for row in rows]

    def get_preset(self, preset_id: int) -> Optional[Preset]:
        row = self._conn.execute(
            f"SELECT {_PRESET_COLUMNS} FROM preset WHERE id = ?", (preset_id,)
        ).fetchone()
        return _preset_from_row(row) if row else None

    def get_preset_by_name(self, name: str) -> Optional[Preset]:
        row = self._conn.execute(
            f"SELECT {_PRESET_COLUMNS} FROM preset WHERE name = ?", (name,)
        ).fetchone()
        return _preset_from_row(row) if row else None

    def save_preset(self, preset: Preset) -> int:
        """Insert (or update) a preset and return its id.

        A name collision overwrites the existing preset rather than raising --
        two presets called "夏天" would be a bug the user cannot see, and the
        card asks again before saving.
        """
        now = time.time()
        values = (preset.name, preset.power, preset.mode, preset.temp, preset.fan,
                  1 if preset.timer_enabled else 0, preset.timer_at or "")
        if preset.id:
            self._conn.execute(
                "UPDATE preset SET name = ?, power = ?, mode = ?, temp = ?, fan = ?, "
                "timer_enabled = ?, timer_at = ?, updated_at = ? WHERE id = ?",
                (*values, now, preset.id))
            self._conn.commit()
            return preset.id

        self._conn.execute(
            "INSERT INTO preset (name, power, mode, temp, fan, timer_enabled, "
            "timer_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(name) DO UPDATE SET power = excluded.power, "
            "mode = excluded.mode, temp = excluded.temp, fan = excluded.fan, "
            "timer_enabled = excluded.timer_enabled, timer_at = excluded.timer_at, "
            "updated_at = excluded.updated_at",
            (*values, now))
        self._conn.commit()
        row = self._conn.execute(
            "SELECT id FROM preset WHERE name = ?", (preset.name,)).fetchone()
        return int(row[0])

    def delete_preset(self, preset_id: int) -> None:
        self._conn.execute("DELETE FROM preset WHERE id = ?", (preset_id,))
        self._conn.commit()

    # ------------------------------------------------------------- schedules

    def add_schedule(self, kind: str, payload: dict, run_at: float) -> int:
        cursor = self._conn.execute(
            "INSERT INTO schedule (kind, payload, run_at, status, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (kind, json.dumps(payload, ensure_ascii=False), run_at,
             STATUS_PENDING, time.time()))
        self._conn.commit()
        return int(cursor.lastrowid)

    def list_schedules(self, status: Optional[str] = None) -> list[Schedule]:
        if status is None:
            rows = self._conn.execute(
                "SELECT id, kind, payload, run_at, status FROM schedule "
                "ORDER BY run_at, id").fetchall()
        else:
            rows = self._conn.execute(
                "SELECT id, kind, payload, run_at, status FROM schedule "
                "WHERE status = ? ORDER BY run_at, id", (status,)).fetchall()
        return [_schedule_from_row(row) for row in rows]

    def set_schedule_status(self, schedule_id: int, status: str) -> None:
        self._conn.execute(
            "UPDATE schedule SET status = ? WHERE id = ?", (status, schedule_id))
        self._conn.commit()

    # ----------------------------------------------------------- env history

    def add_env_sample(self, at: float, temp: int, humidity: Optional[int],
                       ok: bool = True) -> None:
        self._conn.execute(
            "INSERT INTO env_history (at, temp, humidity, ok) VALUES (?, ?, ?, ?)",
            (at, temp, humidity, 1 if ok else 0))
        self._conn.commit()

    def env_samples(self, since: Optional[float] = None) -> list[EnvSample]:
        if since is None:
            rows = self._conn.execute(
                "SELECT at, temp, humidity, ok FROM env_history ORDER BY at").fetchall()
        else:
            rows = self._conn.execute(
                "SELECT at, temp, humidity, ok FROM env_history WHERE at >= ? "
                "ORDER BY at", (since,)).fetchall()
        return [EnvSample(at=row[0], temp=row[1],
                          humidity=row[2] if row[2] is not None else 0,
                          ok=bool(row[3])) for row in rows]

    # -------------------------------------------------------------- settings

    def get_setting(self, key: str, default: str = "") -> str:
        row = self._conn.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row[0] if row else default

    def set_setting(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value))
        self._conn.commit()

    def all_settings(self) -> dict[str, str]:
        rows = self._conn.execute("SELECT key, value FROM settings").fetchall()
        return {row[0]: row[1] for row in rows}

    def close(self) -> None:
        self._conn.close()


# --------------------------------------------------------------- row mapping


def _preset_from_row(row: Iterable) -> Preset:
    preset_id, name, power, mode, temp, fan, timer_enabled, timer_at = row
    return Preset(id=int(preset_id), name=name, power=int(power), mode=int(mode),
                  temp=int(temp), fan=int(fan),
                  timer_enabled=bool(timer_enabled), timer_at=timer_at or "")


def _schedule_from_row(row: Iterable) -> Schedule:
    schedule_id, kind, payload, run_at, status = row
    try:
        decoded = json.loads(payload)
    except (TypeError, ValueError):
        decoded = {}
    return Schedule(id=int(schedule_id), kind=kind, payload=decoded,
                    run_at=float(run_at), status=status)
