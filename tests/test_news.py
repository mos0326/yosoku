import feedparser

from yosoku.sources.news_rss import parse_feed

RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>テスト経済ニュース</title>
    <item>
      <title>A社が通期業績予想を上方修正</title>
      <link>https://example.com/news/1</link>
      <guid>https://example.com/news/1</guid>
      <description>営業利益が従来予想を上回る見通し。</description>
      <pubDate>Fri, 12 Jun 2026 10:00:00 +0900</pubDate>
    </item>
    <item>
      <title>市場概況: 日経平均は続伸</title>
      <link>https://example.com/news/2</link>
      <guid>https://example.com/news/2</guid>
    </item>
  </channel>
</rss>
"""


def test_parse_feed():
    parsed = feedparser.parse(RSS)
    events = parse_feed(parsed, "https://example.com/feed.xml")
    assert len(events) == 2

    e0 = events[0]
    assert e0.source == "news"
    assert e0.event_id.startswith("news:")
    assert "上方修正" in e0.title
    assert e0.url == "https://example.com/news/1"
    assert e0.body is not None
    assert e0.extra["feed"] == "https://example.com/feed.xml"


def test_event_ids_are_stable_and_unique():
    parsed = feedparser.parse(RSS)
    a = parse_feed(parsed, "https://example.com/feed.xml")
    b = parse_feed(feedparser.parse(RSS), "https://example.com/feed.xml")
    # 同じ記事は同じ ID(再実行で安定)
    assert a[0].event_id == b[0].event_id
    # 別記事は別 ID
    assert a[0].event_id != a[1].event_id
