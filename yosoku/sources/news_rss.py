"""ニュース RSS ソース.

設定された RSS フィードを巡回し、記事を RawEvent に変換する。
ニュースは銘柄に直接紐づかないことが多いため、銘柄特定は分析(Claude)側に委ね、
ここでは記事メタ情報の取り込みに専念する。
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime
from time import mktime

import feedparser

from yosoku.models import RawEvent
from yosoku.sources.base import Source

logger = logging.getLogger(__name__)


def _entry_id(entry: dict, feed_url: str) -> str:
    """記事の安定 ID。guid/link が無ければタイトルのハッシュで代替。"""
    raw = entry.get("id") or entry.get("link") or entry.get("title") or ""
    if not raw:
        return ""
    digest = hashlib.sha1(f"{feed_url}|{raw}".encode()).hexdigest()[:16]
    return f"news:{digest}"


def _entry_time(entry: dict) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:
            try:
                return datetime.fromtimestamp(mktime(t))
            except (OverflowError, ValueError):
                continue
    return None


def parse_feed(parsed: feedparser.FeedParserDict, feed_url: str) -> list[RawEvent]:
    """feedparser の結果を RawEvent のリストへ変換する(ネットワーク非依存)。"""
    events: list[RawEvent] = []
    for entry in parsed.get("entries", []) or []:
        eid = _entry_id(entry, feed_url)
        title = (entry.get("title") or "").strip()
        if not eid or not title:
            continue
        summary = (entry.get("summary") or "").strip() or None
        events.append(
            RawEvent(
                source="news",
                event_id=eid,
                title=title,
                body=summary,
                url=entry.get("link") or None,
                published_at=_entry_time(entry),
                extra={"feed": feed_url},
            )
        )
    return events


class NewsRssSource(Source):
    name = "news"

    def __init__(self, feeds: list[str]) -> None:
        self.feeds = feeds

    def fetch(self) -> list[RawEvent]:
        events: list[RawEvent] = []
        for url in self.feeds:
            try:
                parsed = feedparser.parse(url)
                if parsed.get("bozo") and not parsed.get("entries"):
                    logger.warning("RSS の解析に失敗: %s", url)
                    continue
                events.extend(parse_feed(parsed, url))
            except Exception as e:  # feedparser は基本例外を投げないが保険
                logger.warning("RSS(%s) の取得に失敗: %s", url, e)
        return events
