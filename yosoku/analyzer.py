"""分析(Claude).

各イベント(決算開示・ニュース)を Claude に渡し、「上がりそうか」を
構造化出力(`Analysis`)として判定させる。

- 構造化出力 (`messages.parse` + `output_format`) でスキーマを保証。
- システムプロンプトは慎重な日本株アナリスト像。過度に強気にならないよう校正。
- モデルは既定で claude-opus-4-8。大量処理のコストを抑えたい場合は
  環境変数 YOSOKU_MODEL や config の model で sonnet/haiku へ変更可能。
"""

from __future__ import annotations

import logging
from typing import Optional

import anthropic

from yosoku.config import Config
from yosoku.models import Analysis, RawEvent
from yosoku.sources.prices import price_context, to_yahoo_ticker

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
あなたは日本株を担当する経験豊富なバイサイド・アナリストです。
適時開示(TDnet)やニュースの1件を読み、その内容が対象企業の**株価を短期的に\
押し上げる材料**になりそうかを冷静に評価します。

評価の指針:
- 事実(増益・上方修正・増配・自社株買い・大型受注・提携など)に基づき、\
  ポジティブ/ネガティブ/中立を判断する。
- 既に織り込み済みと思われる内容、観測記事、定型的な開示(単元株変更や\
  事務的訂正など)は中立寄り・低スコアにする。
- 過度に強気にならない。確証が弱ければ confidence を低くする。
- ニュースで特定の1銘柄に紐づけられない(市場全体の話題など)場合は\
  is_relevant を false にする。
- score は -100〜+100、confidence は 0〜100。
- rationale と key_factors は日本語で簡潔に書く。"""


class Analyzer:
    def __init__(self, config: Config, client: Optional[anthropic.Anthropic] = None) -> None:
        self.config = config
        # client 未指定なら環境変数(ANTHROPIC_API_KEY)から生成。
        self.client = client or anthropic.Anthropic(api_key=config.anthropic_api_key)

    def _build_user_prompt(self, event: RawEvent) -> str:
        lines = [
            f"ソース: {event.source}",
            f"見出し: {event.title}",
        ]
        if event.company_name:
            lines.append(f"企業名: {event.company_name}")
        if event.company_code:
            ticker = to_yahoo_ticker(event.company_code)
            lines.append(f"証券コード: {event.company_code}（{ticker}）")
        if event.published_at:
            lines.append(f"開示時刻: {event.published_at:%Y-%m-%d %H:%M}")
        if event.body:
            lines.append(f"本文/要約: {event.body[:1500]}")
        if event.url:
            lines.append(f"URL: {event.url}")

        # 直近の値動きを添える(設定 ON かつコードが分かる場合のみ)。
        if self.config.analysis.enable_price_context and event.company_code:
            ticker = to_yahoo_ticker(event.company_code)
            if ticker:
                ctx = price_context(ticker)
                if ctx:
                    lines.append(f"直近の値動き: {ctx}")

        lines.append(
            "\n上記が対象銘柄の株価を短期的に押し上げる材料になりそうかを判定してください。"
        )
        return "\n".join(lines)

    def analyze(self, event: RawEvent) -> Optional[Analysis]:
        """1件のイベントを分析する。失敗時は None。"""
        try:
            response = self.client.messages.parse(
                model=self.config.model,
                max_tokens=1024,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": self._build_user_prompt(event)}],
                output_format=Analysis,
            )
        except anthropic.APIError as e:
            logger.error("Claude 分析に失敗(%s): %s", event.event_id, e)
            return None

        analysis = response.parsed_output
        if analysis is None:
            logger.warning("構造化出力のパースに失敗: %s", event.event_id)
            return None

        # 開示で証券コードが分かっていてモデルが ticker を埋めていない場合は補完。
        if analysis.ticker is None and event.company_code:
            analysis.ticker = to_yahoo_ticker(event.company_code)
        if analysis.company_name is None and event.company_name:
            analysis.company_name = event.company_name
        return analysis
