"""通知部門.

signals.approved を購読し、Discord へ送る。送信(同期)は別スレッドに逃がして
イベントループを塞がない。
"""

from __future__ import annotations

import asyncio
import logging

from yosoku.agents.base import Agent
from yosoku.agents.bus import Bus
from yosoku.agents.messages import APPROVED
from yosoku.models import Signal

logger = logging.getLogger(__name__)


class NotifierAgent(Agent):
    name = "notifier"

    def __init__(self, bus: Bus, notifier) -> None:
        super().__init__(bus)
        self.notifier = notifier  # notify(signal)->bool を持つ

    async def run(self) -> None:
        logger.info("[notifier] 起動")
        q = self.bus.subscribe(APPROVED)
        while True:
            signal: Signal = await q.get()
            try:
                ok = await asyncio.to_thread(self.notifier.notify, signal)
                logger.info(
                    "[notifier] %s %s -> %s",
                    signal.display_name,
                    signal.display_ticker,
                    "送信OK" if ok else "送信失敗",
                )
            except Exception:
                logger.exception("[notifier] 送信で例外")
