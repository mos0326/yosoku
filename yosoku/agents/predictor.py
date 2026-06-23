"""予測部門(投機的・スタブ).

現状の本体は「開示への反応(リアクティブ)」。この部門は将来、**カタリストが
出る前**に上がりそうな候補を先回りでスキャンするためのもの(投機的・参考用)。

実装アイデア(未実装):
- 直近の決算予定/IRイベント前の銘柄を抽出
- 出来高急増・モメンタム・需給(信用残)・セクター連想
- ニュース/SNSのセンチメント
これらを Claude で評価し Prediction を predictions トピックに流す。

※「公開情報が出る前に当てる」のは本質的に不確実。あくまで補助的な仮説提示。
"""

from __future__ import annotations

import asyncio
import logging

from yosoku.agents.base import Agent
from yosoku.agents.bus import Bus

logger = logging.getLogger(__name__)


class PredictorAgent(Agent):
    name = "predictor"

    def __init__(self, bus: Bus, interval: int = 3600, enabled: bool = False) -> None:
        super().__init__(bus)
        self.interval = interval
        self.enabled = enabled

    async def run(self) -> None:
        if not self.enabled:
            logger.info("[predictor] 無効(土台のみ)。enabled=True で有効化。")
            # 何もしないが、部門としては常駐しておく。
            while True:
                await asyncio.sleep(self.interval)
        logger.info("[predictor] 起動 (interval=%ds)", self.interval)
        while True:
            # TODO: ここで候補スキャン → self.bus.publish(PREDICTIONS, Prediction(...))
            await asyncio.sleep(self.interval)
