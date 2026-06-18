"""パイプライン(非同期).

収集(並列) → 一次フィルタ → 重複排除 → 分析(並列・二段階) → 通知判定 → 通知。

- データソースは並行取得。
- 分析はセマフォで並列度を制御しつつ同時実行(I/O 待ちを潰す)。
- 通知判定など中核ロジックは純関数として切り出してテスト可能にしている。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Protocol

from yosoku.analyzer import AnalysisOutcome, TieredAnalyzer
from yosoku.config import Config
from yosoku.metrics import UsageTracker
from yosoku.models import Analysis, RawEvent, Signal
from yosoku.notifier import DiscordNotifier
from yosoku.sources.base import Source
from yosoku.sources.news_rss import NewsRssSource
from yosoku.sources.tdnet import TdnetSource
from yosoku.store import Store

logger = logging.getLogger(__name__)


# ---- 中核の純関数(テスト対象) -------------------------------------------


def passes_prefilter(event: RawEvent, config: Config) -> bool:
    """LLM 分析に回す前の一次フィルタ。"""
    if event.source != "tdnet":
        return True
    keywords = config.analysis.relevance_keywords
    if not keywords:
        return True
    return any(kw in event.title for kw in keywords)


def should_notify(analysis: Analysis, config: Config) -> bool:
    """分析結果が通知に値するか。"""
    a = config.analysis
    if not analysis.is_relevant:
        return False
    if analysis.direction != "bullish":
        return False
    if analysis.score < a.score_threshold:
        return False
    if analysis.confidence < a.confidence_threshold:
        return False
    if analysis.ticker is None and not a.notify_tickerless:
        return False
    return True


def dedup_events(events: list[RawEvent]) -> list[RawEvent]:
    """同一バッチ内の event_id 重複を取り除く(順序保持)。"""
    seen: set[str] = set()
    out: list[RawEvent] = []
    for e in events:
        if e.event_id in seen:
            continue
        seen.add(e.event_id)
        out.append(e)
    return out


# ---- 通知の抽象(テストでフェイクに差し替え可能) --------------------------


class Notifier(Protocol):
    def notify(self, signal: Signal) -> bool:  # pragma: no cover - Protocol
        ...


class AnalyzerLike(Protocol):
    async def analyze(self, event: RawEvent) -> AnalysisOutcome | None:  # pragma: no cover
        ...


@dataclass
class RunResult:
    collected: int = 0
    new: int = 0
    analyzed: int = 0
    notified: int = 0
    signals: list[Signal] = field(default_factory=list)
    cost: float = 0.0
    usage_summary: str = ""


# ---- パイプライン本体 ------------------------------------------------------


class Pipeline:
    def __init__(
        self,
        config: Config,
        sources: list[Source],
        analyzer: AnalyzerLike,
        store: Store,
        notifier: Notifier | None = None,
        usage: UsageTracker | None = None,
        dry_run: bool = False,
    ) -> None:
        self.config = config
        self.sources = sources
        self.analyzer = analyzer
        self.store = store
        self.notifier = notifier
        self.usage = usage
        self.dry_run = dry_run

    async def _collect(self) -> list[RawEvent]:
        results = await asyncio.gather(
            *(src.fetch_async() for src in self.sources), return_exceptions=True
        )
        events: list[RawEvent] = []
        for src, res in zip(self.sources, results, strict=True):
            if isinstance(res, Exception):
                logger.warning("[%s] 取得で例外: %s", src.name, res)
                continue
            logger.info("[%s] %d 件取得", src.name, len(res))
            events.extend(res)
        return events

    async def run_once(self) -> RunResult:
        result = RunResult()

        # 0) 初回のみ Discord 接続確認の稼働通知を1回だけ送る。
        if (
            not self.dry_run
            and self.notifier is not None
            and self.store.get_meta("startup_notified") is None
        ):
            send = getattr(self.notifier, "notify_text", None)
            if send and send(
                "✅ yosoku センサー稼働開始。これは Discord 接続の確認メッセージです。"
                "以後は『上がりそう』な銘柄を検知したときだけ通知します。"
            ):
                self.store.set_meta("startup_notified", "1")

        # 1) 収集(並列)
        events = dedup_events(await self._collect())
        result.collected = len(events)

        # 2) 重複排除 + 一次フィルタ(同期・高速)。候補だけ残す。
        candidates: list[RawEvent] = []
        for event in events:
            if self.store.is_seen(event.event_id):
                continue
            result.new += 1
            if not passes_prefilter(event, self.config):
                self.store.mark_seen(event.event_id, event.source, notified=False)
                continue
            candidates.append(event)

        # 3) 分析(セマフォで並列度を制御)
        sem = asyncio.Semaphore(max(1, self.config.analysis.concurrency))

        async def analyze_one(ev: RawEvent):
            async with sem:
                return ev, await self.analyzer.analyze(ev)

        analyzed = await asyncio.gather(*(analyze_one(e) for e in candidates))

        # 4) 通知判定・通知・記録(逐次:件数は少なく、DB/通知の競合を避ける)
        for event, outcome in analyzed:
            if outcome is None:
                # 分析失敗は seen にせず次回リトライ。
                continue
            result.analyzed += 1
            signal = Signal(event=event, analysis=outcome.analysis, stage=outcome.stage)

            notified = False
            if should_notify(outcome.analysis, self.config):
                result.signals.append(signal)
                if self.dry_run or self.notifier is None:
                    logger.info(
                        "[DRY-RUN] シグナル(%s): %s %s score=%+d conf=%d",
                        signal.stage,
                        signal.display_name,
                        signal.display_ticker,
                        outcome.analysis.score,
                        outcome.analysis.confidence,
                    )
                    notified = True
                else:
                    notified = self.notifier.notify(signal)
                if notified:
                    result.notified += 1

            self.store.record_signal(signal, notified=notified)
            self.store.mark_seen(event.event_id, event.source, notified=notified)

        if self.usage is not None:
            result.cost = self.usage.total_cost
            result.usage_summary = self.usage.summary()

        logger.info(
            "実行完了: collected=%d new=%d analyzed=%d notified=%d cost=$%.4f",
            result.collected,
            result.new,
            result.analyzed,
            result.notified,
            result.cost,
        )
        return result

    async def watch(self, interval: int | None = None) -> None:
        interval = interval or self.config.poll_interval
        logger.info("watch モード開始 (interval=%ds)", interval)
        while True:
            try:
                await self.run_once()
            except Exception:
                logger.exception("run_once で例外。次サイクルへ継続。")
            await asyncio.sleep(interval)

    async def aclose(self) -> None:
        close = getattr(self.analyzer, "aclose", None)
        if close:
            await close()


# ---- 組み立てヘルパ --------------------------------------------------------


def build_sources(config: Config) -> list[Source]:
    sources: list[Source] = []
    if config.tdnet.enabled:
        sources.append(
            TdnetSource(limit=config.tdnet.limit, watchlist=config.tdnet.watchlist)
        )
    if config.news.enabled and config.news.feeds:
        sources.append(NewsRssSource(feeds=config.news.feeds))
    return sources


def build_pipeline(config: Config, store: Store, dry_run: bool = False) -> Pipeline:
    usage = UsageTracker()
    analyzer = TieredAnalyzer(config, usage=usage)
    notifier: Notifier | None = None
    if not dry_run and config.discord_webhook_url:
        notifier = DiscordNotifier(config.discord_webhook_url)
    return Pipeline(
        config=config,
        sources=build_sources(config),
        analyzer=analyzer,
        store=store,
        notifier=notifier,
        usage=usage,
        dry_run=dry_run,
    )
