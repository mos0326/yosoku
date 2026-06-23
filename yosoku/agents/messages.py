"""部門間でやり取りするトピック名とメッセージ型.

ドメイン本体(RawEvent / Analysis / Signal)は models.py を再利用する。
ここではトピック定数と、予測部門が出す Prediction を定義する。
"""

from __future__ import annotations

from pydantic import BaseModel

# --- バスのトピック名 ---
RAW_EVENTS = "events.raw"          # 収集 → 分析
SIGNALS = "signals.analyzed"       # 分析 → リスク管理
APPROVED = "signals.approved"      # リスク管理 → 通知
PREDICTIONS = "predictions"        # 予測 → (分析/通知)


class Prediction(BaseModel):
    """予測部門が出す「カタリスト前の有望候補」(投機的)。"""

    ticker: str
    company_name: str | None = None
    thesis: str               # なぜ上がりそうか(根拠)
    score: int                # 0〜100
    horizon: str = "days"
