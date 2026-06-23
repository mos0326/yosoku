"""オーケストレータ.

全部門(Agent)を生成し、Bus で繋いで並行起動する。既存コンポーネント
(sources / TieredAnalyzer / DiscordNotifier / Store)を再利用する。

これは本番パイプライン(pipeline.py / watch)とは別の、マルチエージェント版の
実行系。`python -m yosoku agents` で起動できる(実験的)。
"""

from __future__ import annotations

import asyncio
import logging

from yosoku.agents.analyst import AnalystAgent
from yosoku.agents.base import Agent
from yosoku.agents.bus import Bus
from yosoku.agents.collector import CollectorAgent
from yosoku.agents.notifier import NotifierAgent
from yosoku.agents.predictor import PredictorAgent
from yosoku.agents.risk import RiskAgent
from yosoku.analyzer import TieredAnalyzer
from yosoku.config import Config
from yosoku.metrics import UsageTracker
from yosoku.notifier import DiscordNotifier
from yosoku.pipeline import build_sources
from yosoku.store import Store

logger = logging.getLogger(__name__)


def build_agents(config: Config, store: Store, dry_run: bool = False) -> tuple[list[Agent], TieredAnalyzer]:
    bus = Bus()
    usage = UsageTracker()
    analyzer = TieredAnalyzer(config, usage=usage)
    notifier = None
    if not dry_run and config.discord_webhook_url:
        notifier = DiscordNotifier(config.discord_webhook_url)

    agents: list[Agent] = [
        CollectorAgent(bus, build_sources(config), interval=config.poll_interval),
        AnalystAgent(bus, analyzer, concurrency=config.analysis.concurrency),
        RiskAgent(bus, store, config),
        PredictorAgent(bus, enabled=False),
    ]
    if notifier is not None:
        agents.append(NotifierAgent(bus, notifier))
    return agents, analyzer


async def run_agents(config: Config, store: Store, dry_run: bool = False) -> None:
    agents, analyzer = build_agents(config, store, dry_run=dry_run)
    logger.info("マルチエージェント起動: %s", ", ".join(a.name for a in agents))
    try:
        await asyncio.gather(*(a.run() for a in agents))
    finally:
        await analyzer.aclose()
