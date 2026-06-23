"""非同期メッセージバス(部門間の疎結合な受け渡し).

トピックごとの pub/sub。購読者はそれぞれ独立したキューを持ち、発行は全購読者へ
ファンアウトする。これにより部門同士は互いを直接知らずにデータを流せる。
"""

from __future__ import annotations

import asyncio
from collections import defaultdict


class Bus:
    def __init__(self) -> None:
        self._subs: dict[str, list[asyncio.Queue]] = defaultdict(list)

    def subscribe(self, topic: str) -> asyncio.Queue:
        """トピックを購読し、自分専用のキューを得る。"""
        q: asyncio.Queue = asyncio.Queue()
        self._subs[topic].append(q)
        return q

    async def publish(self, topic: str, message) -> None:
        """購読者全員へメッセージを配る。"""
        for q in self._subs.get(topic, []):
            await q.put(message)

    def subscriber_count(self, topic: str) -> int:
        return len(self._subs.get(topic, []))
