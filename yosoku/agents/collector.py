"""収集部門.

各データソースを定期的に取得し、新規イベントを events.raw に流す。
(永続的な重複排除はリスク管理部門が担当。ここは同一ポーリング内の
 二重発行を防ぐ軽量な in-memory dedup のみ。)
"""

from __future__ import annotations

import asyncio
import logging

from yosoku.agents.base import Agent
from yosoku.agents.bus import Bus
from yosoku.agents.messages import RAW_EVENTS
from yosoku.sources.base import Source

logger = logging.getLogger(__name__)


class CollectorAgent(Agent):
    name = "collector"

    def __init__(self, bus: Bus, sources: list[Source], interval: int = 60) -> None:
        super().__init__(bus)
        self.sources = sources
        self.interval = interval
        self._seen: set[str] = set()

    async def run(self) -> None:
        logger.info("[collector] 起動 (interval=%ds, sources=%d)", self.interval, len(self.sources))
        while True:
            for src in self.sources:
                try:
                    events = await src.fetch_async()
                except Exception:
                    logger.exception("[collector] %s の取得で例外", src.name)
                    continue
                for e in events:
                    if e.event_id in self._seen:
                        continue
                    self._seen.add(e.event_id)
                    await self.bus.publish(RAW_EVENTS, e)
            await asyncio.sleep(self.interval)
