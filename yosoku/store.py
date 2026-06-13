"""重複管理ストア(SQLite).

一度処理(分析)したイベントを記録し、再通知・再分析を防ぐ。
ephemeral なコンテナでも、ファイルをコミット/永続化すれば状態を引き継げる。
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Iterator

_SCHEMA = """
CREATE TABLE IF NOT EXISTS seen (
    event_id   TEXT PRIMARY KEY,
    source     TEXT,
    notified   INTEGER DEFAULT 0,
    seen_at    TEXT DEFAULT (datetime('now'))
);
"""


class Store:
    def __init__(self, path: str = "yosoku_state.db") -> None:
        self.path = path
        self._conn = sqlite3.connect(path)
        self._conn.execute(_SCHEMA)
        self._conn.commit()

    def is_seen(self, event_id: str) -> bool:
        cur = self._conn.execute(
            "SELECT 1 FROM seen WHERE event_id = ?", (event_id,)
        )
        return cur.fetchone() is not None

    def mark_seen(self, event_id: str, source: str, notified: bool = False) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO seen (event_id, source, notified) VALUES (?, ?, ?)",
            (event_id, source, 1 if notified else 0),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()


@contextmanager
def open_store(path: str = "yosoku_state.db") -> Iterator[Store]:
    store = Store(path)
    try:
        yield store
    finally:
        store.close()
