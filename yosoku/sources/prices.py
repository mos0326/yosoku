"""株価コンテキスト取得(yfinance).

証券コード → Yahoo Finance ティッカー変換と、直近の値動きを短いテキストに
要約する処理を提供する。分析プロンプトに「直近の地合い」を添えるのが目的で、
取得失敗は致命的でない(None を返してスキップ)。
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def to_yahoo_ticker(company_code: str) -> Optional[str]:
    """TSE の証券コードを yfinance 用ティッカー(例 '7203.T')へ変換する。

    TDnet は5桁コード(例 "72030")で返す。末尾が "0" の純数字5桁は
    4桁証券コード + チェック桁とみなして先頭4桁を採用する。
    新形式の英数字5桁コードはそのまま使う。
    """
    if not company_code:
        return None
    code = str(company_code).strip().upper()
    if not code:
        return None
    if len(code) == 5 and code.isdigit() and code.endswith("0"):
        code = code[:4]
    return f"{code}.T"


def price_context(ticker: str, lookback_days: int = 30) -> Optional[str]:
    """直近の値動きを1行サマリにして返す。失敗時は None。

    yfinance(と pandas)は重く外部 I/O に依存するため、遅延 import + 例外握りつぶし。
    """
    try:
        import yfinance as yf  # 遅延 import
    except ImportError:
        logger.info("yfinance 未インストールのため株価コンテキストをスキップ")
        return None

    try:
        hist = yf.Ticker(ticker).history(period=f"{lookback_days}d")
        if hist is None or hist.empty:
            return None
        close = hist["Close"].dropna()
        if len(close) < 2:
            return None

        last = float(close.iloc[-1])
        prev = float(close.iloc[-2])
        day_chg = (last - prev) / prev * 100 if prev else 0.0

        parts = [f"終値 {last:.1f}", f"前日比 {day_chg:+.1f}%"]

        if len(close) >= 6:
            base5 = float(close.iloc[-6])
            chg5 = (last - base5) / base5 * 100 if base5 else 0.0
            parts.append(f"5営業日 {chg5:+.1f}%")

        if len(close) >= 20:
            sma20 = float(close.iloc[-20:].mean())
            rel = (last - sma20) / sma20 * 100 if sma20 else 0.0
            parts.append(f"20日線乖離 {rel:+.1f}%")

        if "Volume" in hist and len(hist["Volume"].dropna()) >= 6:
            vol = hist["Volume"].dropna()
            last_vol = float(vol.iloc[-1])
            avg_vol = float(vol.iloc[-6:-1].mean()) if len(vol) >= 6 else 0.0
            if avg_vol:
                parts.append(f"出来高 直近平均比 {last_vol / avg_vol:.1f}倍")

        return f"{ticker}: " + ", ".join(parts)
    except Exception as e:  # 値動き取得は best-effort
        logger.info("株価コンテキスト取得失敗(%s): %s", ticker, e)
        return None
