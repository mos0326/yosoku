from yosoku.models import Analysis, RawEvent, Signal
from yosoku.store import Store


def test_dedup_in_memory():
    store = Store(":memory:")
    assert store.is_seen("tdnet:1") is False
    store.mark_seen("tdnet:1", "tdnet", notified=True)
    assert store.is_seen("tdnet:1") is True
    store.mark_seen("tdnet:1", "tdnet", notified=False)  # 二重でも落ちない
    assert store.is_seen("tdnet:1") is True
    store.close()


def test_persistence(tmp_path):
    path = str(tmp_path / "state.db")
    s1 = Store(path)
    s1.mark_seen("news:abc", "news")
    s1.close()
    s2 = Store(path)
    assert s2.is_seen("news:abc") is True
    s2.close()


def _signal():
    event = RawEvent(
        source="tdnet",
        event_id="tdnet:9",
        title="決算短信〔上方修正〕",
        company_code="72030",
        company_name="テスト自動車",
        url="https://example.com/a.pdf",
    )
    analysis = Analysis(
        is_relevant=True,
        ticker="7203.T",
        company_name="テスト自動車",
        direction="bullish",
        score=82,
        confidence=66,
        horizon="days",
        rationale="上方修正。",
        key_factors=["上方修正"],
    )
    return Signal(event=event, analysis=analysis, stage="deep")


def test_record_and_list_signals():
    store = Store(":memory:")
    store.record_signal(_signal(), notified=True)
    rows = store.recent_signals(limit=10)
    assert len(rows) == 1
    r = rows[0]
    assert r["ticker"] == "7203.T"
    assert r["score"] == 82
    assert r["stage"] == "deep"
    assert r["notified"] == 1
    # notified_only フィルタ
    assert len(store.recent_signals(notified_only=True)) == 1
    store.close()
