"""リスク管理部門.

signals.analyzed を購読し、(1)重複排除、(2)通知しきい値、(3)1日あたりの
通知上限(過剰通知の抑制)を適用して、合格したものだけ signals.approved に流す。
全シグナルは履歴(store)に記録する。
"""

from __future__ import annotations

import logging

from yosoku.agents.base import Agent
from yosoku.agents.bus import Bus
from yosoku.agents.messages import APPROVED, SIGNALS
from yosoku.clock import now_jst
from yosoku.config import Config
from yosoku.models import Signal
from yosoku.pipeline import should_notify
from yosoku.store import Store

logger = logging.getLogger(__name__)


class RiskAgent(Agent):
    name = "risk"

    def __init__(self, bus: Bus, store: Store, config: Config, daily_cap: int = 50) -> None:
        super().__init__(bus)
        self.store = store
        self.config = config
        self.daily_cap = daily_cap
        self._day = now_jst().date()
        self._count = 0

    def _cap_reached(self) -> bool:
        today = now_jst().date()
        if today != self._day:  # 日付が変わったらリセット
            self._day = today
            self._count = 0
        return self._count >= self.daily_cap

    def evaluate(self, signal: Signal) -> bool:
        """通知してよいか(重複排除済み前提の純粋判定)。テスト用に分離。"""
        return should_notify(signal.analysis, self.config) and not self._cap_reached()

    async def run(self) -> None:
        logger.info("[risk] 起動 (daily_cap=%d)", self.daily_cap)
        q = self.bus.subscribe(SIGNALS)
        while True:
            signal: Signal = await q.get()
            try:
                if self.store.is_seen(signal.event.event_id):
                    continue
                approved = self.evaluate(signal)
                self.store.record_signal(signal, notified=approved)
                self.store.mark_seen(
                    signal.event.event_id, signal.event.source, notified=approved
                )
                if approved:
                    self._count += 1
                    await self.bus.publish(APPROVED, signal)
            except Exception:
                logger.exception("[risk] 評価で例外: %s", signal.event.event_id)
