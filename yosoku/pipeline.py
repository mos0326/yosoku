"""パイプライン.

収集 → 一次フィルタ → 重複排除 → 分析(Claude) → 通知判定 → Discord 通知。

各ステップを差し替え可能にし、通知判定など中核ロジックは純関数として
切り出してテストしやすくしている。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Optional, Protocol

from yosoku.analyzer import Analyzer
from yosoku.config import Config
from yosoku.models import Analysis, RawEvent, Signal
from yosoku.notifier import DiscordNotifier
from yosoku.sources.base import Source
from yosoku.sources.news_rss import NewsRssSource
from yosoku.sources.tdnet import TdnetSource
from yosoku.store import Store

logger = logging.getLogger(__name__)


# ---- 中核の純関数(テスト対象) -------------------------------------------


def passes_prefilter(event: RawEvent, config: Config) -> bool:
    """LLM 分析に回す前の一次フィルタ。

    TDnet 開示は、設定キーワードのいずれかを見出しに含むものだけ通す
    (キーワード未設定なら全件通す)。ニュースは常に通す(銘柄特定は分析側)。
    """
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


# ---- 通知の抽象(テストでフェイクに差し替え可能) --------------------------


class Notifier(Protocol):
    def notify(self, signal: Signal) -> bool:  # pragma: no cover - Protocol
        ...


@dataclass
class RunResult:
    """1回の実行結果サマリ。"""

    collected: int = 0
    new: int = 0
    analyzed: int = 0
    notified: int = 0
    signals: list[Signal] = field(default_factory=list)


# ---- パイプライン本体 ------------------------------------------------------


class Pipeline:
    def __init__(
        self,
        config: Config,
        sources: list[Source],
        analyzer: Analyzer,
        store: Store,
        notifier: Optional[Notifier] = None,
        dry_run: bool = False,
    ) -> None:
        self.config = config
        self.sources = sources
        self.analyzer = analyzer
        self.store = store
        self.notifier = notifier
        self.dry_run = dry_run

    def run_once(self) -> RunResult:
        result = RunResult()

        # 1) 収集
        events: list[RawEvent] = []
        for src in self.sources:
            got = src.fetch()
            logger.info("[%s] %d 件取得", src.name, len(got))
            events.extend(got)
        result.collected = len(events)

        for event in events:
            # 2) 重複排除
            if self.store.is_seen(event.event_id):
                continue
            result.new += 1

            # 3) 一次フィルタ(対象外でも seen に記録して再評価を防ぐ)
            if not passes_prefilter(event, self.config):
                self.store.mark_seen(event.event_id, event.source, notified=False)
                continue

            # 4) 分析
            analysis = self.analyzer.analyze(event)
            result.analyzed += 1
            if analysis is None:
                # 分析失敗は seen にせず、次回リトライさせる。
                continue

            signal = Signal(event=event, analysis=analysis)

            # 5) 通知判定 + 通知
            notified = False
            if should_notify(analysis, self.config):
                result.signals.append(signal)
                if self.dry_run or self.notifier is None:
                    logger.info(
                        "[DRY-RUN] シグナル: %s %s score=%+d conf=%d",
                        signal.display_name,
                        signal.display_ticker,
                        analysis.score,
                        analysis.confidence,
                    )
                    notified = True
                else:
                    notified = self.notifier.notify(signal)
                if notified:
                    result.notified += 1

            self.store.mark_seen(event.event_id, event.source, notified=notified)

        logger.info(
            "実行完了: collected=%d new=%d analyzed=%d notified=%d",
            result.collected,
            result.new,
            result.analyzed,
            result.notified,
        )
        return result

    def watch(self, interval: Optional[int] = None) -> None:
        """interval 秒ごとに run_once を繰り返す。"""
        interval = interval or self.config.poll_interval
        logger.info("watch モード開始 (interval=%ds)", interval)
        while True:
            try:
                self.run_once()
            except Exception:  # 1回の失敗でループを止めない
                logger.exception("run_once で例外。次サイクルへ継続。")
            time.sleep(interval)


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


def build_pipeline(
    config: Config, store: Store, dry_run: bool = False
) -> Pipeline:
    analyzer = Analyzer(config)
    notifier: Optional[Notifier] = None
    if not dry_run and config.discord_webhook_url:
        notifier = DiscordNotifier(config.discord_webhook_url)
    return Pipeline(
        config=config,
        sources=build_sources(config),
        analyzer=analyzer,
        store=store,
        notifier=notifier,
        dry_run=dry_run,
    )
