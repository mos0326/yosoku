from yosoku.sources.tdnet import parse_items

SAMPLE = {
    "total_count": 2,
    "items": [
        {
            "Tdnet": {
                "id": "1259010",
                "pubdate": "2026-06-12 19:30:00",
                "company_code": "34800",
                "company_name": "テスト商事",
                "title": "2026年10月期 第２四半期決算短信〔上方修正〕",
                "document_url": "https://example.com/a.pdf",
                "markets_string": "東証プライム",
                "url_xbrl": "https://example.com/a.zip",
            }
        },
        {
            "Tdnet": {
                "id": "1259011",
                "pubdate": "2026-06-12 19:35:00",
                "company_code": "72030",
                "company_name": "サンプル自動車",
                "title": "自己株式の取得に係る事項の決定",
                "document_url": "https://example.com/b.pdf",
            }
        },
        # 不正データ(title なし)は無視される
        {"Tdnet": {"id": "x", "pubdate": "", "title": ""}},
        {"NotTdnet": {}},
    ],
}


def test_parse_items_basic():
    events = parse_items(SAMPLE)
    assert len(events) == 2

    e0 = events[0]
    assert e0.source == "tdnet"
    assert e0.event_id == "tdnet:1259010"
    assert e0.company_code == "34800"
    assert e0.company_name == "テスト商事"
    assert "上方修正" in e0.title
    assert e0.url == "https://example.com/a.pdf"
    assert e0.published_at is not None
    assert e0.published_at.year == 2026
    assert e0.extra.get("markets_string") == "東証プライム"


def test_parse_items_empty():
    assert parse_items({}) == []
    assert parse_items({"items": []}) == []
