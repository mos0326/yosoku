"""分析部門.

events.raw を購読し、Claude(TieredAnalyzer)で判定して Signal を
signals.analyzed に流す。セマフォで並列度を制御する。
"""

from __future__ import annotations

import asyncio
import logging

from yosoku.agents.base import Agent
from yosoku.agents.bus import Bus
from yosoku.agents.messages import RAW_EVENTS, SIGNALS
from yosoku.analyzer import TieredAnalyzer
from yosoku.models import RawEvent, Signal

logger = logging.getLogger(__name__)


class AnalystAgent(Agent):
    name = "analyst"

    def __init__(self, bus: Bus, analyzer: TieredAnalyzer, concurrency: int = 6) -> None:
        super().__init__(bus)
        self.analyzer = analyzer
        self.sem = asyncio.Semaphore(max(1, concurrency))

    async def run(self) -> None:
        logger.info("[analyst] 起動")
        q = self.bus.subscribe(RAW_EVENTS)
        while True:
            event: RawEvent = await q.get()
            asyncio.create_task(self._handle(event))  # 並行処理

    async def _handle(self, event: RawEvent) -> None:
        try:
            async with self.sem:
                outcome = await self.analyzer.analyze(event)
        except Exception:
            logger.exception("[analyst] 分析で例外: %s", event.event_id)
            return
        if outcome is None:
            return
        await self.bus.publish(
            SIGNALS, Signal(event=event, analysis=outcome.analysis, stage=outcome.stage)
        )
