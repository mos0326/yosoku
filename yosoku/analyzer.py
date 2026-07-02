"""分析(Claude) — 二段階・非同期.

性能と精度を両立するため、2段階で評価する:

1. **triage**: 安価・高速モデル(既定 haiku)で全候補を素早くスクリーニング。
   見出し(+短い本文)だけを見て関連性と概算スコアを出す。
2. **deep**: triage で有望と判定されたものだけ、高性能モデル(既定 opus 4.8)で精査。
   開示PDF本文・直近の値動き・(任意で)Web文脈を与え、adaptive thinking で推論。

これにより「大量の開示を安く捌きつつ、効きそうな少数に推論コストを集中」できる。
非同期(`AsyncAnthropic`)なのでパイプライン側からセマフォで並列実行する。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import anthropic

from yosoku.config import Config
from yosoku.metrics import UsageTracker
from yosoku.models import Analysis, ArbiterVerdict, RawEvent
from yosoku.research import gather_web_context
from yosoku.sources.document import fetch_document_text
from yosoku.sources.prices import current_price, to_yahoo_ticker

logger = logging.getLogger(__name__)

TRIAGE_SYSTEM = """\
あなたは日本株の材料を高速にスクリーニングするアシスタントです。
適時開示やニュースの見出し(と短い本文)を読み、対象企業の株価を**短期的に\
押し上げる材料**になりうるかを手早く判定します。
- 確実性より網羅性を優先し、少しでも上昇材料になりそうなら score を高めに。
- 銘柄を特定できない一般ニュースは is_relevant=false。
- action(今すぐ/押し目待ち/明日以降/見送り)・priority(1-5)・expected_move_pct も
  ざっくり埋めてよい(後段で精査される)。
- 後段で精査するので、ここでは素早く粗く判断してよい。"""

DEEP_SYSTEM = """\
あなたは日本株を担当する経験豊富なバイサイド・アナリストです。
与えられた適時開示・ニュース(本文・直近の値動きを含む)を精読し、その内容が\
対象企業の**短期的な株価**を押し上げる材料かを、根拠に基づいて厳密に評価します。

評価の指針:
- 開示本文の数値(売上・営業利益・経常/純利益・通期予想・進捗率・配当・\
  自己株取得枠など)を読み取り、市場予想やコンセンサスに対するサプライズの\
  方向と大きさを推定する。
- 「上方修正」「増配」「大型受注」「自社株買い」「提携・M&A」「好決算」は\
  ポジティブ材料になりやすい。逆に「下方修正」「減配」「希薄化を伴う増資」は\
  ネガティブ。
- 既に株価に織り込み済みと思われるもの、観測記事、定型的・事務的な開示\
  (単元株変更、軽微な訂正、代表者異動の事務連絡など)は中立寄り・低スコア。
- 直近で既に大きく上昇している場合は出尽くし/材料織り込みの可能性も考慮する。
- 過度に強気にならない。根拠が弱ければ confidence を下げる。
- score は -100〜+100、confidence は 0〜100。
- rationale と key_factors は日本語で、数値や事実を引用しつつ簡潔に書く。

買い推奨(必ず埋める):
- 与えられた「現在値(本日±%)」を踏まえて action を決める:
  まだ動いていない/小幅上昇なら '今すぐ'(初動に乗る)。既に当日大きく上がって
  いれば '押し目待ち' か '見送り'。引けた後の好材料は '明日以降' も検討する。
- expected_move_pct: 短期(数日)の想定上昇率(%)を控えめに見積もる(例 5〜15)。
- priority: 1(低)〜5(高)。確度が高く初動を狙えるほど高くする。"""

ARBITER_SYSTEM = """\
あなたは日本株ファンドの最終意思決定者(シニアPM)です。アナリストが「買い」と
判断したシグナルが1件だけ回ってきます。通知(=ユーザーの売買トリガー)に値するかを
最終判定してください。あなたの却下はそのまま通知の取りやめになります。

判定の観点:
- 材料の新規性と強さ: 定型開示・軽微な修正・観測の後追いは却下。
- 織り込み度: 現在値が既に大きく上げているなら出尽くしを疑う。
- 実行可能性: 一般の個人投資家が明日までに妥当な価格で買えるか。
- アナリストの根拠に飛躍・過大評価がないか(数字と整合しているか)。

出力:
- 迷ったら approve=false(通知は少なく正確に。誤報1件は信頼を大きく損なう)。
- reason は1〜2文で、ユーザーがそのまま読める日本語で書く。
- action / priority / expected_move_pct は「修正が必要な場合のみ」埋める
  (アナリスト案が妥当なら null のままにする)。"""


@dataclass
class AnalysisOutcome:
    analysis: Analysis
    stage: str  # "triage" or "deep"


class TieredAnalyzer:
    def __init__(
        self,
        config: Config,
        client: anthropic.AsyncAnthropic | None = None,
        usage: UsageTracker | None = None,
    ) -> None:
        self.config = config
        self.usage = usage or UsageTracker()
        self.client = client or anthropic.AsyncAnthropic(
            api_key=config.anthropic_api_key,
            max_retries=config.analysis.max_retries,
            timeout=config.analysis.request_timeout,
        )

    async def aclose(self) -> None:
        try:
            await self.client.close()
        except Exception:  # クローズ失敗は無視
            pass

    # ---- 公開API --------------------------------------------------------

    async def analyze(self, event: RawEvent) -> AnalysisOutcome | None:
        """1件を評価する。失敗時は None(次サイクルで再試行)。"""
        a = self.config.analysis

        if not a.two_stage:
            await self._enrich_for_deep(event)
            deep = await self._call(event, model=self.config.model, deep=True)
            return AnalysisOutcome(deep, "deep") if deep else None

        # 1) triage
        triage = await self._call(event, model=a.triage_model, deep=False)
        if triage is None:
            return None

        # 速報優先: 一次判定の時点で通知条件を満たす明確な好材料は、
        # 重い精査(deep)を待たずに即通知する(数秒で届ける)。pipeline.should_notify
        # と同じ条件をここで先取りする。
        if a.fast_alert and self._triage_alertable(triage):
            return AnalysisOutcome(triage, "triage")

        escalate = triage.is_relevant and triage.score >= a.triage_escalate_score
        if not escalate:
            return AnalysisOutcome(triage, "triage")

        # 2) deep(本文・値動き・Web文脈を付与して精査)
        await self._enrich_for_deep(event)
        deep = await self._call(event, model=self.config.model, deep=True)
        if deep is None:
            # 精査に失敗したら triage 結果にフォールバック(取りこぼし防止)。
            return AnalysisOutcome(triage, "triage")
        return AnalysisOutcome(deep, "deep")

    def _triage_alertable(self, triage: Analysis) -> bool:
        """一次判定がそのまま通知条件を満たすか(pipeline.should_notify と同条件)。"""
        a = self.config.analysis
        return (
            triage.is_relevant
            and triage.direction == "bullish"
            and triage.score >= a.score_threshold
            and triage.confidence >= a.confidence_threshold
            and (triage.ticker is not None or a.notify_tickerless)
        )

    async def arbitrate(
        self, event: RawEvent, analysis: Analysis
    ) -> ArbiterVerdict | None:
        """通知直前の最終判定(上位モデルによるセカンドオピニオン)。

        失敗・拒否・パース不能はすべて None を返し、呼び出し側はそのまま通知する
        (フェイルオープン: 最終判定が使えなくてもアラートを取りこぼさない)。
        """
        a = self.config.analysis
        lines = [f"見出し: {event.title}"]
        if event.company_name:
            lines.append(f"企業名: {event.company_name}")
        if event.company_code:
            lines.append(f"証券コード: {event.company_code}")
        if event.published_at:
            lines.append(f"開示時刻: {event.published_at:%Y-%m-%d %H:%M}")
        lines.append(
            "\n--- アナリスト(精査)の評価 ---\n"
            f"score={analysis.score:+d} confidence={analysis.confidence} "
            f"direction={analysis.direction} horizon={analysis.horizon}\n"
            f"action={analysis.action} priority={analysis.priority} "
            f"expected_move_pct={analysis.expected_move_pct}\n"
            f"理由: {analysis.rationale}\n"
            f"材料: {' / '.join(analysis.key_factors[:5])}"
        )
        pi = event.extra.get("price_at_alert") or {}
        if pi.get("price") is not None:
            chg = (
                f" (本日 {pi['change_pct']:+.1f}%)"
                if pi.get("change_pct") is not None
                else ""
            )
            lines.append(f"現在値: {pi['price']:,.0f}{chg}")
        pts = event.extra.get("pts_price") or {}
        if pts.get("price") is not None:
            lines.append(f"PTS: {pts['price']:,.0f}")
        if event.document_text:
            lines.append(f"\n--- 開示本文(抜粋) ---\n{event.document_text[:1200]}\n---")
        lines.append("\nこのシグナルを通知して良いか最終判定してください。")

        try:
            # Fable は thinking 常時ONのため thinking パラメータは渡さない(渡すと400)。
            response = await self.client.messages.parse(
                model=a.arbiter_model,
                max_tokens=8192,
                system=ARBITER_SYSTEM,
                messages=[{"role": "user", "content": "\n".join(lines)}],
                output_format=ArbiterVerdict,
            )
        except anthropic.APIError as e:
            logger.warning("最終判定に失敗(%s, %s): %s", event.event_id, a.arbiter_model, e)
            return None
        except Exception as e:  # パース失敗など
            logger.warning("最終判定の応答処理に失敗(%s): %s", event.event_id, e)
            return None

        self.usage.add(a.arbiter_model, getattr(response, "usage", None))
        if getattr(response, "stop_reason", None) == "refusal":
            logger.warning("最終判定がセーフティ拒否(%s)。通知はそのまま実施。", event.event_id)
            return None
        verdict = getattr(response, "parsed_output", None)
        if verdict is None:
            logger.warning("最終判定の構造化出力が空: %s", event.event_id)
            return None
        return verdict

    # ---- 内部 -----------------------------------------------------------

    async def _enrich_for_deep(self, event: RawEvent) -> None:
        """精査前に開示PDF本文・Web文脈を(必要なら)取得して event に載せる。"""
        a = self.config.analysis
        if (
            a.fetch_document
            and event.document_text is None
            and event.url
            and ".pdf" in (event.url or "").lower()
        ):
            text = await asyncio.to_thread(
                fetch_document_text, event.url, a.max_document_chars
            )
            if text:
                event.document_text = text

        if a.enable_web_context and not event.extra.get("web_context"):
            ctx = await gather_web_context(
                self.client, event, model=self.config.model, usage=self.usage
            )
            if ctx:
                event.extra["web_context"] = ctx

    def _build_user_prompt(self, event: RawEvent, deep: bool) -> str:
        lines = [f"ソース: {event.source}", f"見出し: {event.title}"]
        if event.company_name:
            lines.append(f"企業名: {event.company_name}")
        if event.company_code:
            lines.append(
                f"証券コード: {event.company_code}（{to_yahoo_ticker(event.company_code)}）"
            )
        if event.published_at:
            lines.append(f"開示時刻: {event.published_at:%Y-%m-%d %H:%M}")
        if event.body:
            limit = 1500 if deep else 400
            lines.append(f"本文/要約: {event.body[:limit]}")
        if deep and event.document_text:
            lines.append(f"\n--- 開示本文(抜粋) ---\n{event.document_text}\n---")
        if deep and event.extra.get("web_context"):
            lines.append(f"\n--- 参考(Web) ---\n{event.extra['web_context']}\n---")
        lines.append(
            "\n上記が対象銘柄の株価を短期的に押し上げる材料になりそうかを判定してください。"
        )
        return "\n".join(lines)

    async def _add_price_context(self, event: RawEvent, lines_holder: list[str]) -> None:
        """現在値(本日±%)を取得して精査プロンプトに添える。買い時判断の根拠になる。"""
        if not self.config.analysis.enable_price_context or not event.company_code:
            return
        ticker = to_yahoo_ticker(event.company_code)
        if not ticker:
            return
        pi = await asyncio.to_thread(current_price, ticker)
        if not pi:
            return
        event.extra["price_at_alert"] = pi  # 通知でも再利用(二重取得を避ける)
        cur = "¥" if pi.get("currency") == "JPY" else ""
        chg = (
            f" (本日 {pi['change_pct']:+.1f}%)"
            if pi.get("change_pct") is not None
            else ""
        )
        lines_holder.append(f"現在値: {cur}{pi['price']:,.0f}{chg}")

    async def _call(
        self, event: RawEvent, model: str, deep: bool
    ) -> Analysis | None:
        prompt_lines = [self._build_user_prompt(event, deep)]
        if deep:
            # 値動きはブロッキングなので別スレッドで取得して末尾に足す。
            extra: list[str] = []
            await self._add_price_context(event, extra)
            if extra:
                prompt_lines.append("\n".join(extra))
        user_content = "\n".join(prompt_lines)

        kwargs: dict = {
            "model": model,
            "max_tokens": 4096 if (deep and self.config.analysis.deep_thinking) else 1024,
            "messages": [{"role": "user", "content": user_content}],
            "output_format": Analysis,
        }
        if deep:
            # システムプロンプトはキャッシュ可能ブロックにしておく(前方互換)。
            kwargs["system"] = [
                {
                    "type": "text",
                    "text": DEEP_SYSTEM,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
            if self.config.analysis.deep_thinking:
                kwargs["thinking"] = {"type": "adaptive"}
        else:
            kwargs["system"] = TRIAGE_SYSTEM

        try:
            response = await self.client.messages.parse(**kwargs)
        except anthropic.APIError as e:
            logger.error("Claude 分析失敗(%s, %s): %s", event.event_id, model, e)
            return None
        except Exception as e:  # 構造化出力のパース失敗など
            logger.warning("分析の応答処理に失敗(%s, %s): %s", event.event_id, model, e)
            return None

        self.usage.add(model, getattr(response, "usage", None))

        analysis = getattr(response, "parsed_output", None)
        if analysis is None:
            logger.warning("構造化出力が空: %s (%s)", event.event_id, model)
            return None

        # 開示で分かっている情報で補完。
        if analysis.ticker is None and event.company_code:
            analysis.ticker = to_yahoo_ticker(event.company_code)
        if analysis.company_name is None and event.company_name:
            analysis.company_name = event.company_name
        return analysis
