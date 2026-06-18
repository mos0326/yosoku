"""永続化ストア(SQLite).

- `seen`    : 一度処理したイベントID(再通知・再分析の防止)。
- `signals` : 分析したシグナルの履歴(レビュー・バックテスト用)。

ephemeral なコンテナでも、ファイルをコミット/キャッシュすれば状態を引き継げる。
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from yosoku.models import Signal

_SCHEMA = """
CREATE TABLE IF NOT EXISTS seen (
    event_id   TEXT PRIMARY KEY,
    source     TEXT,
    notified   INTEGER DEFAULT 0,
    seen_at    TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS signals (
    event_id     TEXT PRIMARY KEY,
    source       TEXT,
    ticker       TEXT,
    company_name TEXT,
    direction    TEXT,
    score        INTEGER,
    confidence   INTEGER,
    horizon      TEXT,
    stage        TEXT,
    title        TEXT,
    url          TEXT,
    rationale    TEXT,
    published_at TEXT,
    notified     INTEGER DEFAULT 0,
    created_at   TEXT DEFAULT (datetime('now')),
    raw          TEXT
);
CREATE INDEX IF NOT EXISTS idx_signals_score ON signals(score);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


class Store:
    def __init__(self, path: str = "yosoku_state.db") -> None:
        self.path = path
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # ---- seen(重複排除) -----------------------------------------------

    def is_seen(self, event_id: str) -> bool:
        cur = self._conn.execute("SELECT 1 FROM seen WHERE event_id = ?", (event_id,))
        return cur.fetchone() is not None

    def mark_seen(self, event_id: str, source: str, notified: bool = False) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO seen (event_id, source, notified) VALUES (?, ?, ?)",
            (event_id, source, 1 if notified else 0),
        )
        self._conn.commit()

    # ---- meta(状態フラグ) ---------------------------------------------

    def get_meta(self, key: str) -> str | None:
        row = self._conn.execute(
            "SELECT value FROM meta WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value)
        )
        self._conn.commit()

    # ---- signals(履歴) ------------------------------------------------

    def record_signal(self, signal: Signal, notified: bool) -> None:
        e, a = signal.event, signal.analysis
        self._conn.execute(
            """
            INSERT OR REPLACE INTO signals
              (event_id, source, ticker, company_name, direction, score,
               confidence, horizon, stage, title, url, rationale,
               published_at, notified, raw)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                e.event_id,
                e.source,
                signal.display_ticker,
                signal.display_name,
                a.direction,
                a.score,
                a.confidence,
                a.horizon,
                signal.stage,
                e.title,
                e.url,
                a.rationale,
                e.published_at.isoformat() if e.published_at else None,
                1 if notified else 0,
                json.dumps(a.model_dump(), ensure_ascii=False),
            ),
        )
        self._conn.commit()

    def recent_signals(
        self, limit: int = 20, notified_only: bool = False
    ) -> list[sqlite3.Row]:
        sql = "SELECT * FROM signals"
        if notified_only:
            sql += " WHERE notified = 1"
        sql += " ORDER BY created_at DESC LIMIT ?"
        return list(self._conn.execute(sql, (limit,)).fetchall())

    def close(self) -> None:
        self._conn.close()


@contextmanager
def open_store(path: str = "yosoku_state.db") -> Iterator[Store]:
    store = Store(path)
    try:
        yield store
    finally:
        store.close()
