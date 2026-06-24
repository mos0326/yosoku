"""通知(Discord Webhook).

シグナルを Discord の埋め込み(embed)として送信する。
方向に応じて色を変え、ティッカー・スコア・理由・リンクを載せる。
"""

from __future__ import annotations

import logging

import requests

from yosoku.models import Signal

logger = logging.getLogger(__name__)

# 方向ごとの埋め込み色。
_COLORS = {
    "bullish": 0x2ECC71,  # green
    "bearish": 0xE74C3C,  # red
    "neutral": 0x95A5A6,  # gray
}


def _format_price(signal: Signal) -> str:
    """通知時点の株価を '¥170 (+3.0%)' のような文字列にする。無ければ空。"""
    pi = signal.event.extra.get("price_at_alert")
    if not pi or pi.get("price") is None:
        return ""
    cur = "¥" if pi.get("currency") == "JPY" else f"{pi.get('currency', '')} "
    s = f"{cur}{pi['price']:,.0f}"
    if pi.get("change_pct") is not None:
        s += f" ({pi['change_pct']:+.1f}%)"
    return s


def build_embed(signal: Signal) -> dict:
    a = signal.analysis
    ticker = signal.display_ticker or "—"
    name = signal.display_name or ""
    price = _format_price(signal)
    title = f"📈 {name} {ticker}" + (f"  {price}" if price else "")
    title = title.strip()

    fields = [
        {"name": "方向", "value": a.direction, "inline": True},
        {"name": "スコア", "value": f"{a.score:+d}", "inline": True},
        {"name": "確信度", "value": f"{a.confidence}%", "inline": True},
        {"name": "時間軸", "value": a.horizon, "inline": True},
        {"name": "見出し", "value": signal.event.title[:1000], "inline": False},
        {"name": "理由", "value": a.rationale[:1000], "inline": False},
    ]
    if price:
        # 株価を分かりやすく専用欄でも出す(通知時点の値)。
        fields.insert(0, {"name": "株価(通知時点)", "value": price, "inline": True})
    if a.key_factors:
        fields.append(
            {
                "name": "材料",
                "value": "\n".join(f"• {k}" for k in a.key_factors[:5])[:1000],
                "inline": False,
            }
        )

    embed: dict = {
        "title": title[:256],
        "color": _COLORS.get(a.direction, _COLORS["neutral"]),
        "fields": fields,
    }
    if signal.event.url:
        embed["url"] = signal.event.url
    return embed


class DiscordNotifier:
    def __init__(
        self,
        webhook_url: str,
        timeout: float = 10.0,
        session: requests.Session | None = None,
    ) -> None:
        self.webhook_url = webhook_url
        self.timeout = timeout
        self.session = session or requests.Session()

    def notify(self, signal: Signal) -> bool:
        payload = {
            "content": "🔔 トレーディングシグナル検知",
            "embeds": [build_embed(signal)],
        }
        try:
            resp = self.session.post(
                self.webhook_url, json=payload, timeout=self.timeout
            )
            resp.raise_for_status()
            return True
        except requests.RequestException as e:
            logger.error("Discord 通知に失敗: %s", e)
            return False

    def notify_text(self, content: str) -> bool:
        """プレーンなテキストメッセージを送る(接続テスト・稼働通知用)。"""
        try:
            resp = self.session.post(
                self.webhook_url, json={"content": content}, timeout=self.timeout
            )
            resp.raise_for_status()
            logger.info("Discord 送信OK (status=%s)", resp.status_code)
            return True
        except requests.RequestException as e:
            logger.error("Discord 送信に失敗: %s", e)
            return False

