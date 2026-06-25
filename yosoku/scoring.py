"""答え合わせ(outcome verification).

実際に通知したシグナルについて、通知時点のエントリー価格(alerts に凍結)と後刻の
価格を突き合わせ、実現リターンを記録して精度を集計する。

設計の要点(批判レビューを反映):
- **起点は alerts**(不変)。signals は再分析で上書きされうるため使わない。
- **採点窓**: エントリーが古すぎ(> max_age)れば `window_missed` として一度だけ確定し、
  pending から外す(cron 停止などで horizon と無関係な値で凍結するのを防ぐ)。
- **市場時間の前進を必須化**: 評価値の as-of(市場の最終約定時刻)がエントリーから
  min_age 以上進んでいなければ採点を見送る(金曜引け後→土日のゼロリターン量産を防ぐ)。
- **分割/配当/通貨ブレ**は完全補正せず、`realized_return` の防御(通貨不一致→None)と
  異常レンジ(`is_anomalous`)で隔離。生値ベースである旨はレポートに明記する。
- 価格取得は `price_fn` で差し替え可能(将来 yfinance 調整済み終値へ無改修で移行可能)。

純関数(`realized_return`/`is_anomalous`/`window_status`/`accuracy_report`)は
ネットワーク非依存で単体テストできる。`score_pending` のみ I/O。
"""

from __future__ import annotations

import logging
import math
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone

from yosoku.backtest import BucketStat, score_bucket
from yosoku.sources.prices import current_price
from yosoku.store import Store

logger = logging.getLogger(__name__)

# 採点の既定パラメータ。
DEFAULT_MIN_AGE_HOURS = 20.0   # この時間ぶん経過(かつ市場が前進)してから採点する。
DEFAULT_MAX_AGE_HOURS = 168.0  # これを超えても未採点なら window_missed(7日)。
DEFAULT_HIT_THRESHOLD = 0.005  # コスト控除後の「勝ち」しきい値(往復手数料+スプレッド相当 ≈0.5%)。
# 生値リターンの正気レンジ。外れたら分割/併合/権利落ち等の異常として隔離する。
SANE_LO = -0.6
SANE_HI = 2.0


# ---- 純関数 ----------------------------------------------------------------


def realized_return(
    entry: float | None,
    eval_price: float | None,
    entry_ccy: str | None = None,
    eval_ccy: str | None = None,
) -> float | None:
    """(eval-entry)/entry を小数で返す。算出不能なら None。

    契約: entry/eval が有限かつ entry>0、通貨が一致する場合のみ値を返す。
    呼び出し側でのガード漏れを設計で防ぐため、前提を満たさなければ None。
    """
    if entry is None or eval_price is None:
        return None
    try:
        entry = float(entry)
        eval_price = float(eval_price)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(entry) and math.isfinite(eval_price)) or entry <= 0:
        return None
    if entry_ccy and eval_ccy and entry_ccy != eval_ccy:
        return None  # 通貨不一致(ティッカー取り違え等)は比率でも壊れるので捨てる。
    return (eval_price - entry) / entry


def is_anomalous(ret: float | None, lo: float = SANE_LO, hi: float = SANE_HI) -> bool:
    """リターンが正気レンジ外か(分割/併合/権利落ち等の疑い)。"""
    if ret is None:
        return False
    return ret < lo or ret > hi


def window_status(
    age_hours: float,
    market_advance_hours: float | None,
    min_age_hours: float = DEFAULT_MIN_AGE_HOURS,
    max_age_hours: float = DEFAULT_MAX_AGE_HOURS,
) -> str:
    """採点タイミングの判定(純関数)。

    返り値: 'too_early' / 'due' / 'window_missed'。
    - age_hours: エントリーからの実経過(壁時計)。
    - market_advance_hours: 評価値 as-of がエントリーから進んだ時間。None なら未取得。
    """
    if age_hours > max_age_hours:
        return "window_missed"
    if age_hours < min_age_hours:
        return "too_early"
    # 市場時間が holding period ぶん前進していない(休場・場前など)なら見送る。
    if market_advance_hours is not None and market_advance_hours < min_age_hours:
        return "too_early"
    return "due"


@dataclass
class AccuracyReport:
    total: int = 0                 # 通知総数(エントリー凍結済み)
    entry_missing: int = 0         # エントリー価格を取れず評価不能
    pending: int = 0               # 採点待ち(窓に未到達)
    scored: int = 0                # 採点済み(正常)
    anomaly: int = 0               # 異常値で隔離
    window_missed: int = 0         # 期限切れ
    eval_unavailable: int = 0      # 取得不能/通貨不一致
    hit_rate: float = 0.0          # 生リターン勝率(ret>0)
    cost_adj_hit_rate: float = 0.0  # コスト控除後勝率(ret>threshold)
    mean_return: float = 0.0
    median_return: float = 0.0
    target_hit_rate: float | None = None  # 想定到達率(ret>=expected/100)
    target_n: int = 0              # 想定到達率の分母(expected 非 null)
    hit_threshold: float = DEFAULT_HIT_THRESHOLD
    by_bucket: dict[str, BucketStat] = field(default_factory=dict)

    def render(self) -> str:
        lines = [
            "通知後リターン実績(生値・未調整 / 市場・コスト未控除)",
            f"  母集団: 通知 {self.total} / 採点済 {self.scored} "
            f"(平均 {self.mean_return:+.2%} 中央 {self.median_return:+.2%})",
            f"  勝率: 生 {self.hit_rate:.1%} / "
            f"コスト控除後(>{self.hit_threshold:+.1%}) {self.cost_adj_hit_rate:.1%}",
        ]
        if self.target_n:
            tr = self.target_hit_rate if self.target_hit_rate is not None else 0.0
            lines.append(f"  想定到達率: {tr:.1%} (n={self.target_n})")
        lines.append(
            "  除外内訳: "
            f"エントリー欠損 {self.entry_missing} / 採点待ち {self.pending} / "
            f"期限切れ {self.window_missed} / 取得不能 {self.eval_unavailable} / "
            f"異常値 {self.anomaly}"
        )
        if any(st.count for st in self.by_bucket.values()):
            lines.append("  スコア帯別(採点済):")
            for bucket in ("80+", "60-79", "40-59", "0-39", "<0"):
                st = self.by_bucket.get(bucket)
                if st and st.count:
                    lines.append(
                        f"    {bucket:>6}: n={st.count:3d} "
                        f"平均={st.avg_return:+.2%} 勝率={st.hit_rate:.1%}"
                    )
        return "\n".join(lines)


def accuracy_report(rows, hit_threshold: float = DEFAULT_HIT_THRESHOLD) -> AccuracyReport:
    """alerts×outcomes の結合行から精度を集計する(純関数)。

    rows: 各行は score / expected_move_pct / entry_price / return_pct / status を持つ
    マッピング(sqlite3.Row や dict)。outcome が無い行は status=None。
    """
    rep = AccuracyReport(hit_threshold=hit_threshold)
    scored_returns: list[tuple[int, float]] = []
    target_hits = 0
    for r in rows:
        rep.total += 1
        status = r["status"] if _has(r, "status") else None
        entry = r["entry_price"] if _has(r, "entry_price") else None
        if entry is None:
            rep.entry_missing += 1
            continue
        if status is None:
            rep.pending += 1
            continue
        if status == "window_missed":
            rep.window_missed += 1
            continue
        if status == "eval_unavailable":
            rep.eval_unavailable += 1
            continue
        if status == "anomaly":
            rep.anomaly += 1
            continue
        if status != "scored":
            continue
        ret = r["return_pct"]
        if ret is None:
            rep.eval_unavailable += 1
            continue
        rep.scored += 1
        score = r["score"] if r["score"] is not None else 0
        scored_returns.append((int(score), float(ret)))
        exp = r["expected_move_pct"] if _has(r, "expected_move_pct") else None
        if exp is not None:
            rep.target_n += 1
            if ret >= exp / 100.0:
                target_hits += 1

    if scored_returns:
        rets = [ret for _, ret in scored_returns]
        rep.mean_return = sum(rets) / len(rets)
        rep.median_return = statistics.median(rets)
        rep.hit_rate = sum(1 for x in rets if x > 0) / len(rets)
        rep.cost_adj_hit_rate = sum(1 for x in rets if x > hit_threshold) / len(rets)
        for score, ret in scored_returns:
            b = rep.by_bucket.setdefault(score_bucket(score), BucketStat())
            b.count += 1
            b.total_return += ret
            if ret > 0:
                b.wins += 1
    if rep.target_n:
        rep.target_hit_rate = target_hits / rep.target_n
    return rep


def _has(row, key: str) -> bool:
    try:
        row[key]
        return True
    except (KeyError, IndexError, TypeError):
        return False


# ---- I/O(採点パス) --------------------------------------------------------


def _parse_utc(s: str | None) -> datetime | None:
    """SQLite の datetime('now') 文字列('YYYY-MM-DD HH:MM:SS', UTC)を aware 化。"""
    if not s:
        return None
    try:
        return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def score_pending(
    store: Store,
    *,
    min_age_hours: float = DEFAULT_MIN_AGE_HOURS,
    max_age_hours: float = DEFAULT_MAX_AGE_HOURS,
    price_fn=current_price,
    now: datetime | None = None,
    limit: int = 500,
) -> dict[str, int]:
    """採点窓に入った通知を評価して outcomes に記録する。集計カウントを返す。

    price_fn(ticker) -> {"price","currency","as_of",...}|None。差し替え可能。
    """
    now = now or datetime.now(timezone.utc)
    counts = {"scored": 0, "anomaly": 0, "window_missed": 0,
              "eval_unavailable": 0, "too_early": 0, "no_price": 0}

    for a in store.pending_alerts(limit=limit):
        alerted = _parse_utc(a["alerted_at"])
        if alerted is None:
            continue
        age_hours = (now - alerted).total_seconds() / 3600.0

        # 上限を過ぎたものは取得せず期限切れで確定(pending から外す)。
        if age_hours > max_age_hours:
            store.record_outcome(
                a["event_id"], None, None, "window_missed",
                min_age_hours, age_hours, None,
            )
            counts["window_missed"] += 1
            continue
        # 下限未満は何もしない(次サイクルで再評価)。
        if age_hours < min_age_hours:
            counts["too_early"] += 1
            continue

        price = price_fn(a["ticker"])
        if not price or price.get("price") is None:
            counts["no_price"] += 1
            continue  # 取得失敗。窓内なら次回再試行、超過すれば window_missed。

        as_of_dt = (
            datetime.fromtimestamp(price["as_of"], tz=timezone.utc)
            if price.get("as_of") else None
        )
        advance = (
            (as_of_dt - alerted).total_seconds() / 3600.0
            if as_of_dt else None
        )
        if window_status(age_hours, advance, min_age_hours, max_age_hours) != "due":
            counts["too_early"] += 1
            continue  # 市場が holding period ぶん前進していない(休場等)。見送り。

        ret = realized_return(
            a["entry_price"], price["price"], a["currency"], price.get("currency")
        )
        if ret is None:
            status = "eval_unavailable"
        elif is_anomalous(ret):
            status = "anomaly"
        else:
            status = "scored"
        realized_hours = (
            (as_of_dt - alerted).total_seconds() / 3600.0 if as_of_dt else age_hours
        )
        store.record_outcome(
            a["event_id"], price["price"], ret, status,
            min_age_hours, realized_hours,
            as_of_dt.isoformat() if as_of_dt else None,
        )
        counts[status] = counts.get(status, 0) + 1

    return counts
