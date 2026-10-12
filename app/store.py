"""SQLite-backed mapping from an owner to the card message to update in place.

v1 is a single private owner (spec 5.1), so there is effectively one row -- but
keying by owner means a whitelist / group later needs no schema change.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional, Union

_SCHEMA = """
CREATE TABLE IF NOT EXISTS card (
    owner      TEXT PRIMARY KEY,
    message_id TEXT NOT NULL
)
"""


class CardStore:
    """Remembers which card message to keep editing, across restarts."""

    def __init__(self, path: Union[str, Path] = "ac-bot.sqlite3") -> None:
        # The MQTT and Feishu callbacks run on different threads.
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.execute(_SCHEMA)
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

    def close(self) -> None:
        self._conn.close()
