"""Web 文脈の取得(任意・実験的).

精査(deep)の前に、Claude の web_search ツールで対象企業/材料に関する
最近の事実関係を要約して取得する。判定材料を厚くするのが目的。

注意: サーバーツール(web_search)を使うため `analysis.enable_web_context: true`
のときだけ動く。失敗は致命的でない(None を返してスキップ)。
"""

from __future__ import annotations

import logging

import anthropic

from yosoku.metrics import UsageTracker
from yosoku.models import RawEvent

logger = logging.getLogger(__name__)

_WEB_TOOL = {"type": "web_search_20260209", "name": "web_search"}


def _collect_text(content) -> str:
    out = []
    for block in content or []:
        if getattr(block, "type", None) == "text":
            out.append(block.text)
    return "\n".join(out).strip()


async def gather_web_context(
    client: anthropic.AsyncAnthropic,
    event: RawEvent,
    model: str,
    usage: UsageTracker | None = None,
    max_continuations: int = 3,
) -> str | None:
    prompt = (
        "次の日本株の材料について、株価への影響を判断するうえで重要な"
        "最近の事実関係(業績・受注・規制・競合・需給など)を3〜5行で簡潔に"
        "要約してください。憶測は避け、確かな情報が無ければ「特になし」と書いてください。\n\n"
        f"見出し: {event.title}\n"
        f"企業: {event.company_name or '不明'}"
    )
    messages = [{"role": "user", "content": prompt}]
    try:
        resp = await client.messages.create(
            model=model,
            max_tokens=800,
            tools=[_WEB_TOOL],
            messages=messages,
        )
        if usage is not None:
            usage.add(model, getattr(resp, "usage", None))

        # web_search はサーバー側ループで pause_turn になることがある。
        cont = 0
        while resp.stop_reason == "pause_turn" and cont < max_continuations:
            messages = [
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": resp.content},
            ]
            resp = await client.messages.create(
                model=model, max_tokens=800, tools=[_WEB_TOOL], messages=messages
            )
            if usage is not None:
                usage.add(model, getattr(resp, "usage", None))
            cont += 1

        text = _collect_text(resp.content)
        return text or None
    except Exception as e:  # 実験的機能。失敗してもパイプラインは止めない。
        logger.info("Web文脈の取得に失敗(%s): %s", event.event_id, e)
        return None
