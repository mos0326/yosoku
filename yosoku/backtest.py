"""バックテスト.

過去の適時開示を取り込み、現在の分析ロジックで判定し、その後の株価リターンと
突き合わせて「シグナルがどれだけ効いたか」を評価する。

集計(`score_bucket` / `summarize`)はネットワーク非依存で単体テストできる。
実取得(`forward_return` / `run_backtest`)は yfinance / Claude / TDnet を使う。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime

from yosoku.analyzer import TieredAnalyzer
from yosoku.config import Config
from yosoku.metrics import UsageTracker
from yosoku.models import Signal
from yosoku.pipeline import passes_prefilter, should_notify
from yosoku.sources.prices import to_yahoo_ticker
from yosoku.sources.tdnet import TdnetSource

logger = logging.getLogger(__name__)


def score_bucket(score: int) -> str:
    if score >= 80:
        return "80+"
    if score >= 60:
        return "60-79"
    if score >= 40:
        return "40-59"
    if score >= 0:
        return "0-39"
    return "<0"


@dataclass
class BucketStat:
    count: int = 0
    wins: int = 0
    total_return: float = 0.0

    @property
    def avg_return(self) -> float:
        return self.total_return / self.count if self.count else 0.0

    @property
    def hit_rate(self) -> float:
        return self.wins / self.count if self.count else 0.0


@dataclass
class BacktestResult:
    total_signals: int = 0
    evaluated: int = 0
    avg_return: float = 0.0
    hit_rate: float = 0.0
    by_bucket: dict[str, BucketStat] = field(default_factory=dict)
    cost: float = 0.0

    def render(self) -> str:
        lines = [
            f"シグナル数={self.total_signals} 評価可能={self.evaluated} "
            f"平均リターン={self.avg_return:+.2%} 勝率={self.hit_rate:.1%} "
            f"コスト=${self.cost:.4f}",
            "スコア帯別:",
        ]
        for bucket in ("80+", "60-79", "40-59", "0-39", "<0"):
            st = self.by_bucket.get(bucket)
            if st and st.count:
                lines.append(
                    f"  {bucket:>6}: n={st.count:3d} 平均={st.avg_return:+.2%} "
                    f"勝率={st.hit_rate:.1%}"
                )
        return "\n".join(lines)


def summarize(scored_returns: list[tuple[int, float]]) -> BacktestResult:
    """(score, forward_return) のリストから集計する。"""
    result = BacktestResult(total_signals=len(scored_returns))
    if not scored_returns:
        return result
    result.evaluated = len(scored_returns)
    total = 0.0
    wins = 0
    for score, ret in scored_returns:
        total += ret
        if ret > 0:
            wins += 1
        b = result.by_bucket.setdefault(score_bucket(score), BucketStat())
        b.count += 1
        b.total_return += ret
        if ret > 0:
            b.wins += 1
    result.avg_return = total / len(scored_returns)
    result.hit_rate = wins / len(scored_returns)
    return result


def forward_return(
    ticker: str, event_dt: datetime, horizon_days: int = 5
) -> float | None:
    """開示日(以降の最初の営業日)を起点に horizon 営業日後までのリターン。失敗時 None。"""
    try:
        import pandas as pd  # noqa: F401  (yfinance が依存)
        import yfinance as yf
    except ImportError:
        return None
    try:
        start = event_dt.date()
        df = yf.Ticker(ticker).history(period="3mo")
        if df is None or df.empty:
            return None
        close = df["Close"].dropna()
        # 開示日以降の最初の終値を起点に。
        idx = [d for d in close.index if d.date() >= start]
        if len(idx) < 2:
            return None
        entry = float(close.loc[idx[0]])
        exit_i = min(horizon_days, len(idx) - 1)
        exit_p = float(close.loc[idx[exit_i]])
        if entry == 0:
            return None
        return (exit_p - entry) / entry
    except Exception as e:
        logger.info("forward_return 取得失敗(%s): %s", ticker, e)
        return None


async def run_backtest(
    config: Config,
    start: str,
    end: str,
    horizon_days: int = 5,
    limit: int = 200,
) -> BacktestResult:
    """過去レンジの開示を分析し、シグナルの forward return を集計する。"""
    usage = UsageTracker()
    analyzer = TieredAnalyzer(config, usage=usage)
    try:
        src = TdnetSource(limit=config.tdnet.limit)
        events = await asyncio.to_thread(src.fetch_range, start, end, limit)
        events = [e for e in events if passes_prefilter(e, config)]
        logger.info("バックテスト対象 %d 件を分析", len(events))

        sem = asyncio.Semaphore(max(1, config.analysis.concurrency))

        async def analyze_one(ev):
            async with sem:
                return ev, await analyzer.analyze(ev)

        analyzed = await asyncio.gather(*(analyze_one(e) for e in events))

        signals: list[Signal] = []
        for ev, outcome in analyzed:
            if outcome and should_notify(outcome.analysis, config):
                signals.append(Signal(event=ev, analysis=outcome.analysis, stage=outcome.stage))

        # forward return を取得(ブロッキングを別スレッドへ)
        scored: list[tuple[int, float]] = []
        for sig in signals:
            ticker = sig.display_ticker or (
                to_yahoo_ticker(sig.event.company_code) if sig.event.company_code else None
            )
            if not ticker or not sig.event.published_at:
                continue
            ret = await asyncio.to_thread(
                forward_return, ticker, sig.event.published_at, horizon_days
            )
            if ret is not None:
                scored.append((sig.analysis.score, ret))

        result = summarize(scored)
        result.total_signals = len(signals)
        result.cost = usage.total_cost
        return result
    finally:
        await analyzer.aclose()
