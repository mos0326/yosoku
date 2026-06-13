from yosoku.store import Store


def test_dedup_in_memory():
    store = Store(":memory:")
    assert store.is_seen("tdnet:1") is False
    store.mark_seen("tdnet:1", "tdnet", notified=True)
    assert store.is_seen("tdnet:1") is True
    # 二重登録しても落ちない
    store.mark_seen("tdnet:1", "tdnet", notified=False)
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
