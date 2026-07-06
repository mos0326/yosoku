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
-- alerts: 実際に通知が飛んだ瞬間の「エントリー価格」を不変で凍結する。
--   signals は再分析で INSERT OR REPLACE され created_at/値が変わりうるため、
--   答え合わせ(outcome)の起点はこちらに固定する(INSERT OR IGNORE で初回のみ)。
CREATE TABLE IF NOT EXISTS alerts (
    event_id          TEXT PRIMARY KEY,
    ticker            TEXT,
    entry_price       REAL,
    currency          TEXT,
    entry_venue       TEXT,           -- 'regular' | 'pts'
    score             INTEGER,
    expected_move_pct INTEGER,
    alerted_at        TEXT DEFAULT (datetime('now'))   -- UTC
);
-- outcomes: 後刻の価格と突き合わせた答え合わせ結果(event_id ごとに一度だけ)。
CREATE TABLE IF NOT EXISTS outcomes (
    event_id       TEXT PRIMARY KEY,
    eval_price     REAL,
    return_pct     REAL,            -- 実現リターン(小数, 例 0.08)。算出不能なら NULL。
    status         TEXT,            -- 'scored'|'anomaly'|'window_missed'|'eval_unavailable'
    target_hours   REAL,            -- 想定保有(min_age_hours)
    realized_hours REAL,            -- 実経過(エントリー→評価)
    eval_at        TEXT,            -- 評価値の as-of(ISO, 監査用)。無ければ NULL。
    evaluated_at   TEXT DEFAULT (datetime('now'))
);
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
        # watch と score を別プロセスで同時に走らせても落ちないよう待つ。
        self._conn.execute("PRAGMA busy_timeout = 5000")
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

    # ---- デイリーピック用 -------------------------------------------------

    def count_notified_since(self, created_utc: str) -> int:
        """created_utc('YYYY-MM-DD HH:MM:SS', UTC)以降に通知済みのシグナル数。"""
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM signals WHERE notified = 1 AND created_at >= ?",
            (created_utc,),
        ).fetchone()
        return int(row["n"])

    def top_unnotified_since(
        self, created_utc: str, limit: int = 5, min_score: int = 0
    ) -> list[sqlite3.Row]:
        """未通知の強気シグナルをスコア順に返す(補欠候補の選出用)。"""
        return list(
            self._conn.execute(
                """
                SELECT * FROM signals
                WHERE notified = 0
                  AND created_at >= ?
                  AND direction = 'bullish'
                  AND ticker IS NOT NULL
                  AND score >= ?
                ORDER BY score DESC, confidence DESC
                LIMIT ?
                """,
                (created_utc, min_score, limit),
            ).fetchall()
        )

    def set_signal_notified(self, event_id: str) -> None:
        """補欠通知の送信後に通知済みへ更新する(重複選出の防止)。"""
        self._conn.execute(
            "UPDATE signals SET notified = 1 WHERE event_id = ?", (event_id,)
        )
        self._conn.commit()

    # ---- alerts(エントリー価格の凍結) / outcomes(答え合わせ) -----------

    def freeze_alert(
        self,
        event_id: str,
        ticker: str | None,
        entry_price: float | None,
        currency: str | None,
        entry_venue: str | None,
        score: int | None,
        expected_move_pct: int | None,
    ) -> None:
        """通知が飛んだ瞬間のエントリーを不変で記録する(初回のみ・冪等)。"""
        self._conn.execute(
            """
            INSERT OR IGNORE INTO alerts
              (event_id, ticker, entry_price, currency, entry_venue, score,
               expected_move_pct)
            VALUES (?,?,?,?,?,?,?)
            """,
            (event_id, ticker, entry_price, currency, entry_venue, score,
             expected_move_pct),
        )
        self._conn.commit()

    def pending_alerts(self, limit: int = 500) -> list[sqlite3.Row]:
        """まだ答え合わせしていない、エントリー価格付きの通知を古い順に返す。"""
        return list(
            self._conn.execute(
                """
                SELECT a.event_id, a.ticker, a.entry_price, a.currency,
                       a.score, a.expected_move_pct, a.alerted_at
                FROM alerts a
                LEFT JOIN outcomes o ON o.event_id = a.event_id
                WHERE o.event_id IS NULL
                  AND a.entry_price IS NOT NULL
                  AND a.ticker IS NOT NULL
                ORDER BY a.alerted_at ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        )

    def record_outcome(
        self,
        event_id: str,
        eval_price: float | None,
        return_pct: float | None,
        status: str,
        target_hours: float | None,
        realized_hours: float | None,
        eval_at: str | None,
    ) -> None:
        """答え合わせ結果を記録する(event_id ごとに一度だけ・冪等)。"""
        self._conn.execute(
            """
            INSERT OR IGNORE INTO outcomes
              (event_id, eval_price, return_pct, status, target_hours,
               realized_hours, eval_at)
            VALUES (?,?,?,?,?,?,?)
            """,
            (event_id, eval_price, return_pct, status, target_hours,
             realized_hours, eval_at),
        )
        self._conn.commit()

    def outcome_rows(self) -> list[sqlite3.Row]:
        """精度集計用に alerts×outcomes を結合した全行を返す(未採点は outcome 側 NULL)。"""
        return list(
            self._conn.execute(
                """
                SELECT a.event_id, a.ticker, a.entry_price, a.currency,
                       a.entry_venue, a.score, a.expected_move_pct,
                       o.eval_price, o.return_pct, o.status,
                       o.realized_hours, o.eval_at
                FROM alerts a
                LEFT JOIN outcomes o ON o.event_id = a.event_id
                ORDER BY a.alerted_at DESC
                """
            ).fetchall()
        )

    def close(self) -> None:
        self._conn.close()


@contextmanager
def open_store(path: str = "yosoku_state.db") -> Iterator[Store]:
    store = Store(path)
    try:
        yield store
    finally:
        store.close()
