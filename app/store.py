"""SQLite-backed mapping from an owner to the card message to update in place.

v1 is a single private owner (spec 5.1), so there is effectively one row -- but
keying by owner means a whitelist / group later needs no schema change.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Optional, Union

_SCHEMA = """
CREATE TABLE IF NOT EXISTS card (
    owner      TEXT PRIMARY KEY,
    message_id TEXT NOT NULL
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


class CardStore:
    """Remembers which card message to keep editing, across restarts."""

    def __init__(self, path: Union[str, Path] = "ac-bot.sqlite3") -> None:
        # The MQTT and Feishu callbacks run on different threads.
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        for statement in (_SCHEMA, _SEEN_SCHEMA):
            self._conn.execute(statement)
        self._conn.commit()

    def get_message_id(self, owner: str) -> Optional[str]:
        row = self._conn.execute(
            "SELECT message_id FROM card WHERE owner = ?", (owner,)
        ).fetchone()
        return row[0] if row else None

    def set_message_id(self, owner: str, message_id: str) -> None:
        self._conn.execute(
            "INSERT INTO card (owner, message_id) VALUES (?, ?) "
            "ON CONFLICT(owner) DO UPDATE SET message_id = excluded.message_id",
            (owner, message_id),
        )
        self._conn.commit()

    def clear_message_id(self, owner: str) -> None:
        self._conn.execute("DELETE FROM card WHERE owner = ?", (owner,))
        self._conn.commit()

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

    def close(self) -> None:
        self._conn.close()
