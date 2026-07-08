"""パイプライン(非同期).

収集(並列) → 一次フィルタ → 重複排除 → 分析(並列・二段階) → 通知判定 → 通知。

- データソースは並行取得。
- 分析はセマフォで並列度を制御しつつ同時実行(I/O 待ちを潰す)。
- 通知判定など中核ロジックは純関数として切り出してテスト可能にしている。
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import timezone
from typing import Protocol

from yosoku.analyzer import AnalysisOutcome, TieredAnalyzer
from yosoku.clock import is_active_now, is_pts_hours, now_jst, to_jst
from yosoku.config import Config
from yosoku.metrics import UsageTracker
from yosoku.models import Analysis, ArbiterVerdict, RawEvent, Signal
from yosoku.notifier import DiscordNotifier
from yosoku.sources.base import Source
from yosoku.sources.news_rss import NewsRssSource
from yosoku.sources.prices import current_price
from yosoku.sources.pts import pts_price
from yosoku.sources.tdnet import TdnetSource
from yosoku.store import Store

logger = logging.getLogger(__name__)


# ---- 中核の純関数(テスト対象) -------------------------------------------


def is_pro_market(event: RawEvent) -> bool:
    """東京プロマーケット銘柄か(markets_string で判定)。"""
    m = str(event.extra.get("markets_string") or "")
    return ("プロ" in m) or ("PRO" in m.upper())


def passes_prefilter(event: RawEvent, config: Config) -> bool:
    """LLM 分析に回す前の一次フィルタ。"""
    if event.source != "tdnet":
        return True
    if config.analysis.exclude_pro_market and is_pro_market(event):
        return False  # プロマーケットのみ上場は対象外
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


def apply_verdict(analysis: Analysis, verdict: ArbiterVerdict) -> Analysis:
    """最終判定の修正案を分析結果に反映する(純関数・None のフィールドは据え置き)。"""
    if verdict.action:
        analysis.action = verdict.action
    if verdict.priority is not None:
        analysis.priority = verdict.priority
    if verdict.expected_move_pct is not None:
        analysis.expected_move_pct = verdict.expected_move_pct
    return analysis


def _signal_from_row(row) -> Signal | None:
    """signals テーブルの行から通知用の Signal を復元する(補欠通知用・失敗時 None)。"""
    try:
        analysis = Analysis.model_validate_json(row["raw"])
    except Exception:
        return None
    if analysis.ticker is None and row["ticker"]:
        analysis.ticker = row["ticker"]
    event = RawEvent(
        source=row["source"] or "tdnet",
        event_id=row["event_id"],
        title=row["title"] or "",
        url=row["url"],
        company_name=row["company_name"],
    )
    return Signal(event=event, analysis=analysis, stage=row["stage"] or "deep")


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

        # 0) 初回のみ Discord 接続確認の稼働通知を1回だけ送る(成否をログに明示)。
        if (
            not self.dry_run
            and self.notifier is not None
            and self.store.get_meta("startup_notified_v2") is None
        ):
            send = getattr(self.notifier, "notify_text", None)
            if send:
                ok = send(
                    "✅ yosoku センサー稼働確認。これは Discord 接続テストです。"
                    "以後は『上がりそう』な銘柄を検知したときだけ通知します。"
                )
                logger.info("Discord 稼働確認メッセージ: %s", "送信成功" if ok else "送信失敗")
                if ok:
                    self.store.set_meta("startup_notified_v2", "1")

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
            vetoed = False
            if should_notify(outcome.analysis, self.config):
                result.signals.append(signal)
                # 通知直前に現在値(+PTS)を載せる(精査で取得済みなら再利用)。
                await self._attach_prices(signal)
                # 最終判定: 上位モデルが通知直前に承認/却下(失敗時はそのまま通知)。
                verdict = await self._arbitrate(signal)
                if verdict is not None:
                    signal.event.extra["arbiter"] = {
                        "approve": verdict.approve,
                        "reason": verdict.reason,
                    }
                    if verdict.approve:
                        apply_verdict(signal.analysis, verdict)
                    else:
                        vetoed = True
                        logger.info(
                            "最終判定で却下: %s %s — %s",
                            signal.display_name,
                            signal.display_ticker,
                            verdict.reason,
                        )
                if not vetoed:
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
                    # 実送信した通知のみ、答え合わせ用にエントリー価格を不変で凍結する
                    # (notifier=None の擬似通知を起点に混ぜない)。
                    if not self.dry_run and self.notifier is not None:
                        self._freeze_alert(signal)

            self.store.record_signal(signal, notified=notified)
            self.store.mark_seen(event.event_id, event.source, notified=notified)

        # 5) デイリーピック: 夕方時点で当日の通知が不足していれば補欠で埋める。
        try:
            await self._maybe_daily_pick()
        except Exception:
            logger.exception("デイリーピックで例外。継続する。")

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

    async def _attach_prices(self, signal: Signal) -> None:
        """通知直前に現在値(+PTS時間帯なら PTS 価格)を event.extra に付与する。"""
        ticker = signal.display_ticker
        if not ticker or not self.config.analysis.fetch_price_on_alert:
            return
        if "price_at_alert" not in signal.event.extra:
            pi = await asyncio.to_thread(current_price, ticker)
            if pi:
                signal.event.extra["price_at_alert"] = pi
        if is_pts_hours() and "pts_price" not in signal.event.extra:
            pts = await asyncio.to_thread(pts_price, ticker)
            if pts:
                signal.event.extra["pts_price"] = pts

    async def _maybe_daily_pick(self, now=None) -> None:
        """当日の通知が最低件数に満たなければ、上位候補を「補欠」として通知する。

        daily_pick_time(JST)以降に1日1回だけ発火。対象は当日分析済みで
        未通知(基準未達・最終判定見送り)の強気シグナルのスコア上位。
        """
        a = self.config.analysis
        if self.dry_run or self.notifier is None or a.min_daily_alerts <= 0:
            return
        n = to_jst(now) if now else now_jst()
        try:
            hh, mm = (int(x) for x in str(a.daily_pick_time).split(":"))
        except (ValueError, AttributeError):
            logger.warning("daily_pick_time が不正: %r", a.daily_pick_time)
            return
        if (n.hour, n.minute) < (hh, mm):
            return
        meta_key = f"daily_pick:{n.date().isoformat()}"
        if self.store.get_meta(meta_key) is not None:
            return  # 本日分は実施済み

        day_start = n.replace(hour=0, minute=0, second=0, microsecond=0)
        day_start_utc = day_start.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        need = a.min_daily_alerts - self.store.count_notified_since(day_start_utc)
        if need <= 0:
            self.store.set_meta(meta_key, "0")  # 自然に足りた日は何もしない
            return

        sent = 0
        # 当日すでに通知した銘柄は補欠でも重複させない。
        seen_tickers: set[str] = self.store.notified_tickers_since(day_start_utc)
        candidates = self.store.top_unnotified_since(
            day_start_utc, limit=need + 5, min_score=a.daily_pick_min_score
        )
        for row in candidates:
            if sent >= need:
                break
            signal = _signal_from_row(row)
            if signal is None or not signal.analysis.is_relevant:
                continue
            ticker = signal.display_ticker
            if ticker in seen_tickers:
                continue  # 同一銘柄は1日1回まで
            signal.event.extra["daily_pick"] = True
            await self._attach_prices(signal)
            if self.notifier.notify(signal):
                sent += 1
                if ticker:
                    seen_tickers.add(ticker)
                self.store.set_signal_notified(signal.event.event_id)
                self._freeze_alert(signal)
        self.store.set_meta(meta_key, str(sent))  # 失敗込みで1日1回だけ試行
        if sent < need:
            send_text = getattr(self.notifier, "notify_text", None)
            if send_text:
                send_text(
                    f"🌙 定時ピック: 本日の通知は {a.min_daily_alerts - need + sent}件でした。"
                    f"基準(score≥{a.daily_pick_min_score})に届く補欠候補が足りませんでした。"
                )
        logger.info("デイリーピック: %d件送信(不足%d件)", sent, need)

    async def _arbitrate(self, signal: Signal) -> ArbiterVerdict | None:
        """通知直前の最終判定を(有効時のみ)実行する。使えなければ None。

        アナライザーが arbitrate を持たない(テスト用フェイク等)場合や
        呼び出し失敗時も None = 「判定なし・そのまま通知」に倒す。
        """
        if not self.config.analysis.enable_arbiter:
            return None
        arbitrate = getattr(self.analyzer, "arbitrate", None)
        if arbitrate is None:
            return None
        try:
            return await arbitrate(signal.event, signal.analysis)
        except Exception:
            logger.exception("最終判定で例外(%s)。そのまま通知する。", signal.event.event_id)
            return None

    def _freeze_alert(self, signal: Signal) -> None:
        """通知が飛んだ瞬間のエントリー価格を alerts に凍結する(答え合わせの起点)。

        PTS 時間帯は実際に約定可能な PTS 価格をエントリーに採る。取得できていなければ
        ザラ場の現在値。どちらも無ければ entry=None(後段で「エントリー欠損」として可視化)。
        """
        extra = signal.event.extra
        pi = extra.get("price_at_alert") or {}
        pts = extra.get("pts_price") or {}
        if pts.get("price") is not None and is_pts_hours():
            entry, venue, ccy = pts["price"], "pts", "JPY"
        else:
            entry, venue, ccy = pi.get("price"), "regular", pi.get("currency")
        try:
            self.store.freeze_alert(
                event_id=signal.event.event_id,
                ticker=signal.display_ticker,
                entry_price=entry,
                currency=ccy,
                entry_venue=venue,
                score=signal.analysis.score,
                expected_move_pct=signal.analysis.expected_move_pct,
            )
        except Exception:  # 凍結失敗で通知フローを止めない
            logger.exception("alert の凍結に失敗(%s)", signal.event.event_id)

    async def watch(
        self, interval: int | None = None, max_runtime: int | None = None
    ) -> None:
        """interval 秒ごとに run_once を繰り返す。

        max_runtime(秒)を指定すると、その時間に達したらクリーンに終了する
        (GitHub Actions の長時間ジョブを連鎖させる用途。終了時に状態保存が走る)。
        """
        interval = interval or self.config.poll_interval
        deadline = (
            time.monotonic() + max_runtime if max_runtime is not None else None
        )
        logger.info(
            "watch モード開始 (interval=%ds, max_runtime=%s)",
            interval,
            f"{max_runtime}s" if max_runtime else "無制限",
        )
        while True:
            if is_active_now(self.config.active_window, self.config.weekdays_only):
                try:
                    await self.run_once()
                except Exception:
                    logger.exception("run_once で例外。次サイクルへ継続。")
            else:
                logger.debug("稼働時間外のためスキップ。")
            if deadline is not None and time.monotonic() >= deadline:
                logger.info("max_runtime 到達。クリーン終了する。")
                return
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
