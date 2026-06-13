"""TDnet 適時開示ソース.

東証の適時開示(決算短信・業績予想修正・配当・自己株買い 等)を、
無料の JSON ラッパー Yanoshin TDnet WebAPI 経由で取得する。

  エンドポイント: https://webapi.yanoshin.jp/webapi/tdnet/list/recent.json
  レスポンス: {"items": [{"Tdnet": {... title, company_code, pubdate ...}}], ...}

HTTP 取得(`fetch`)と JSON → RawEvent 変換(`parse_items`)を分離してあり、
変換ロジックはネットワーク無しで単体テストできる。
"""

from __future__ import annotations

import logging
from datetime import datetime

import requests

from yosoku.models import RawEvent
from yosoku.sources.base import Source

logger = logging.getLogger(__name__)

RECENT_URL = "https://webapi.yanoshin.jp/webapi/tdnet/list/recent.json"
# 単一銘柄の開示一覧(証券コード指定)。{code} は4桁 or 5桁コード。
CODE_URL = "https://webapi.yanoshin.jp/webapi/tdnet/list/{code}.json"
# 日付範囲(バックテスト用)。{range} は "YYYYMMDD-YYYYMMDD" 等。
RANGE_URL = "https://webapi.yanoshin.jp/webapi/tdnet/list/{range}.json"


def _parse_pubdate(raw: str | None) -> datetime | None:
    if not raw:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw.strip(), fmt)
        except ValueError:
            continue
    return None


def parse_items(payload: dict) -> list[RawEvent]:
    """Yanoshin TDnet WebAPI の JSON を RawEvent のリストへ変換する。"""
    events: list[RawEvent] = []
    for item in payload.get("items", []) or []:
        td = item.get("Tdnet") if isinstance(item, dict) else None
        if not td:
            continue
        tid = str(td.get("id") or "").strip()
        title = (td.get("title") or "").strip()
        if not tid or not title:
            continue

        # 開示種別フラグ(決算短信か等)を extra に残しておく。
        extra = {
            k: td.get(k)
            for k in (
                "markets_string",
                "url_xbrl",
                "url_report_type_summary",
                "url_report_type_earnings_forecast",
                "url_report_type_expected_dividends",
            )
            if td.get(k)
        }

        events.append(
            RawEvent(
                source="tdnet",
                event_id=f"tdnet:{tid}",
                title=title,
                body=None,
                url=td.get("document_url") or None,
                company_code=(str(td.get("company_code")).strip() or None)
                if td.get("company_code")
                else None,
                company_name=(td.get("company_name") or "").strip() or None,
                published_at=_parse_pubdate(td.get("pubdate")),
                extra=extra,
            )
        )
    return events


class TdnetSource(Source):
    name = "tdnet"

    def __init__(
        self,
        limit: int = 50,
        watchlist: list[str] | None = None,
        timeout: float = 15.0,
        session: requests.Session | None = None,
    ) -> None:
        self.limit = limit
        self.watchlist = [str(c).strip() for c in (watchlist or [])]
        self.timeout = timeout
        self.session = session or requests.Session()

    def fetch(self) -> list[RawEvent]:
        if self.watchlist:
            return self._fetch_watchlist()
        return self._fetch_recent()

    def _fetch_recent(self) -> list[RawEvent]:
        try:
            resp = self.session.get(
                RECENT_URL, params={"limit": self.limit}, timeout=self.timeout
            )
            resp.raise_for_status()
            return parse_items(resp.json())
        except (requests.RequestException, ValueError) as e:
            logger.warning("TDnet recent の取得に失敗: %s", e)
            return []

    def _fetch_watchlist(self) -> list[RawEvent]:
        events: list[RawEvent] = []
        for code in self.watchlist:
            try:
                resp = self.session.get(
                    CODE_URL.format(code=code),
                    params={"limit": self.limit},
                    timeout=self.timeout,
                )
                resp.raise_for_status()
                events.extend(parse_items(resp.json()))
            except (requests.RequestException, ValueError) as e:
                logger.warning("TDnet(%s) の取得に失敗: %s", code, e)
        return events

    def fetch_range(self, start: str, end: str, limit: int = 1000) -> list[RawEvent]:
        """日付範囲の開示を取得する(バックテスト用)。

        start/end は "YYYYMMDD"。Yanoshin の "YYYYMMDD-YYYYMMDD" 形式を使う。
        """
        rng = f"{start}-{end}"
        try:
            resp = self.session.get(
                RANGE_URL.format(range=rng),
                params={"limit": limit},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            return parse_items(resp.json())
        except (requests.RequestException, ValueError) as e:
            logger.warning("TDnet 範囲取得(%s)に失敗: %s", rng, e)
            return []
