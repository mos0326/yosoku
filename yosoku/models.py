"""ドメインモデル.

`RawEvent`  : データソースから取り込んだ生イベント(決算開示・ニュース等)。
`Analysis`  : Claude による分析結果(構造化出力スキーマ)。
`Signal`    : `RawEvent` + `Analysis`。通知判定・通知本文の入力になる。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Direction = Literal["bullish", "bearish", "neutral"]
Horizon = Literal["intraday", "days", "weeks", "unknown"]


class RawEvent(BaseModel):
    """データソースから取り込んだ1件の生イベント。

    `event_id` はソースをまたいで一意になるよう、ソース名を接頭辞に付ける
    (例: ``tdnet:1259010``)。重複通知の防止に使う。
    """

    source: str
    event_id: str
    title: str
    body: str | None = None
    url: str | None = None
    # TSE の証券コード。TDnet は5桁(例 "72030")で来る。yfinance 用の
    # 4桁コードへの変換は sources.prices.to_yahoo_ticker が行う。
    company_code: str | None = None
    company_name: str | None = None
    published_at: datetime | None = None
    # 精査ステージで埋める開示PDF本文(抽出テキスト)。
    document_text: str | None = None
    # ソース固有の付加情報(開示種別フラグ、XBRL URL 等)。
    extra: dict = Field(default_factory=dict)


class Analysis(BaseModel):
    """Claude による「上がりそうか」の判定結果(構造化出力スキーマ)。

    このクラスがそのまま Anthropic SDK の ``output_format`` として渡され、
    モデルの応答が本スキーマに沿った JSON に拘束される。
    数値レンジ(score/confidence)は SDK が JSON Schema からは外し、
    クライアント側(pydantic)で検証する。
    """

    is_relevant: bool = Field(
        description="特定の1銘柄に紐づく売買シグナルとして意味があるなら true。"
        "市場全体の一般ニュースなど銘柄を特定できない場合は false。"
    )
    ticker: str | None = Field(
        default=None,
        description="識別できる場合の Yahoo Finance ティッカー(例 '7203.T')。"
        "不明なら null。",
    )
    company_name: str | None = Field(default=None, description="対象企業名(分かれば)。")
    direction: Direction = Field(description="株価への方向性: bullish/bearish/neutral。")
    score: int = Field(
        description="-100(強い下落) 〜 +100(強い上昇)。0 は中立。",
        ge=-100,
        le=100,
    )
    confidence: int = Field(description="判定の確信度 0〜100。", ge=0, le=100)
    horizon: Horizon = Field(description="効果が出ると見込む時間軸。")
    rationale: str = Field(description="判定理由(日本語、2〜4文程度)。")
    key_factors: list[str] = Field(
        default_factory=list, description="判断材料の箇条書き(日本語、最大5個)。"
    )
    expected_move_pct: int | None = Field(
        default=None,
        description="短期(数日)の想定上昇率(%)。例: 8。根拠が弱ければ控えめに。不明なら null。",
    )
    action: str | None = Field(
        default=None,
        description="買いの推奨アクション。次のいずれか: "
        "'今すぐ'(初動に乗る) / '押し目待ち'(一旦の下げを待つ) / "
        "'明日以降'(翌営業日を待つ) / '見送り'(手を出さない)。",
    )
    priority: int | None = Field(
        default=None,
        description="優先度 1(低)〜5(高)。確度が高く今すぐ動くべきほど高い。",
    )


class Signal(BaseModel):
    """生イベントと分析結果のペア。"""

    event: RawEvent
    analysis: Analysis
    # 最終結果がどの段階で出たか("triage" / "deep")。
    stage: str = "deep"

    @property
    def display_ticker(self) -> str | None:
        """通知に出すティッカー。分析結果を優先し、無ければイベント側から導出。"""
        if self.analysis.ticker:
            return self.analysis.ticker
        from yosoku.sources.prices import to_yahoo_ticker

        if self.event.company_code:
            return to_yahoo_ticker(self.event.company_code)
        return None

    @property
    def display_name(self) -> str | None:
        return self.analysis.company_name or self.event.company_name
