"""答え合わせ(scoring)のテスト。ネットワーク非依存(price_fn はモック注入)。"""

from datetime import datetime, timedelta, timezone

from yosoku.scoring import (
    accuracy_report,
    is_anomalous,
    realized_return,
    score_pending,
    window_status,
)
from yosoku.store import Store

UTC = timezone.utc


# ---- 純関数: realized_return ----------------------------------------------


def test_realized_return_normal():
    assert realized_return(100.0, 110.0) == 0.10
    assert realized_return(200.0, 180.0) == -0.10


def test_realized_return_guards():
    assert realized_return(None, 100.0) is None
    assert realized_return(100.0, None) is None
    assert realized_return(0.0, 100.0) is None          # ゼロ除算回避
    assert realized_return(-5.0, 100.0) is None          # 負の entry は異常
    assert realized_return(float("nan"), 100.0) is None  # 非有限
    assert realized_return("x", 100.0) is None           # 型不正


def test_realized_return_currency_mismatch():
    # 通貨が食い違えば(ティッカー取り違え等)比率でも壊れるので None。
    assert realized_return(100.0, 110.0, "JPY", "USD") is None
    assert realized_return(100.0, 110.0, "JPY", "JPY") == 0.10


# ---- 純関数: is_anomalous --------------------------------------------------


def test_is_anomalous():
    assert is_anomalous(0.1) is False
    assert is_anomalous(-0.1) is False
    assert is_anomalous(2.0) is False    # 上限ちょうどは正常
    assert is_anomalous(4.0) is True     # 桁ズレ(分割相当)
    assert is_anomalous(-0.8) is True
    assert is_anomalous(None) is False


# ---- 純関数: window_status -------------------------------------------------


def test_window_status():
    # 下限未満
    assert window_status(5, None, min_age_hours=20, max_age_hours=168) == "too_early"
    # 窓内・市場前進不明 → 採点可
    assert window_status(48, None, min_age_hours=20, max_age_hours=168) == "due"
    # 上限超過
    assert window_status(200, None, min_age_hours=20, max_age_hours=168) == "window_missed"
    # 窓内だが市場が holding period ぶん進んでいない(休場等)→ 見送り
    assert window_status(48, 3, min_age_hours=20, max_age_hours=168) == "too_early"
    # 窓内かつ市場も十分前進 → 採点可
    assert window_status(48, 30, min_age_hours=20, max_age_hours=168) == "due"


# ---- 純関数: accuracy_report -----------------------------------------------


def test_accuracy_report_empty():
    rep = accuracy_report([])
    assert rep.total == 0 and rep.scored == 0
    assert "通知 0" in rep.render()


def test_accuracy_report_mix():
    rows = [
        {"entry_price": 100, "status": "scored", "return_pct": 0.08, "score": 85,
         "expected_move_pct": 5},
        {"entry_price": 100, "status": "scored", "return_pct": -0.02, "score": 70,
         "expected_move_pct": 10},
        {"entry_price": None, "status": None, "return_pct": None, "score": 60,
         "expected_move_pct": None},
        {"entry_price": 100, "status": None, "return_pct": None, "score": 60,
         "expected_move_pct": None},
        {"entry_price": 100, "status": "window_missed", "return_pct": None,
         "score": 60, "expected_move_pct": None},
        {"entry_price": 100, "status": "anomaly", "return_pct": 5.0, "score": 90,
         "expected_move_pct": None},
        {"entry_price": 100, "status": "eval_unavailable", "return_pct": None,
         "score": 60, "expected_move_pct": None},
    ]
    rep = accuracy_report(rows, hit_threshold=0.005)
    assert rep.total == 7
    assert rep.scored == 2
    assert rep.entry_missing == 1
    assert rep.pending == 1
    assert rep.window_missed == 1
    assert rep.anomaly == 1
    assert rep.eval_unavailable == 1
    # 2件中1件が ret>0
    assert rep.hit_rate == 0.5
    assert rep.cost_adj_hit_rate == 0.5
    assert abs(rep.mean_return - 0.03) < 1e-9
    # 想定到達: 0.08>=0.05 は達成、-0.02>=0.10 は未達 → 1/2
    assert rep.target_n == 2
    assert rep.target_hit_rate == 0.5
    # スコア帯
    assert rep.by_bucket["80+"].count == 1
    assert rep.by_bucket["60-79"].count == 1


# ---- I/O: score_pending(モック price_fn) ---------------------------------


def _seed_alert(store, entry=100.0, ticker="7203.T", ccy="JPY"):
    store.freeze_alert(
        event_id="tdnet:1", ticker=ticker, entry_price=entry, currency=ccy,
        entry_venue="regular", score=80, expected_move_pct=8,
    )


def test_freeze_alert_is_immutable():
    store = Store(":memory:")
    _seed_alert(store, entry=100.0)
    _seed_alert(store, entry=999.0)  # 二度目は無視される(冪等)
    rows = store.pending_alerts()
    assert len(rows) == 1
    assert rows[0]["entry_price"] == 100.0
    store.close()


def test_score_pending_scored_and_idempotent():
    store = Store(":memory:")
    _seed_alert(store, entry=100.0)
    calls = []

    def price_fn(ticker):
        calls.append(ticker)
        return {"price": 110.0, "currency": "JPY", "as_of": None}

    now = datetime.now(UTC) + timedelta(hours=48)  # 窓内
    counts = score_pending(store, min_age_hours=20, max_age_hours=168,
                           price_fn=price_fn, now=now)
    assert counts["scored"] == 1
    rep = accuracy_report(store.outcome_rows())
    assert rep.scored == 1
    assert abs(rep.mean_return - 0.10) < 1e-9

    # 二度目は pending が無く、price_fn も呼ばれない(冪等)。
    counts2 = score_pending(store, min_age_hours=20, max_age_hours=168,
                            price_fn=price_fn, now=now)
    assert counts2["scored"] == 0
    assert len(calls) == 1
    store.close()


def test_score_pending_too_early_does_not_fetch():
    store = Store(":memory:")
    _seed_alert(store)
    called = []

    def price_fn(ticker):
        called.append(ticker)
        return {"price": 110.0, "currency": "JPY", "as_of": None}

    now = datetime.now(UTC) + timedelta(hours=5)  # 下限未満
    counts = score_pending(store, min_age_hours=20, price_fn=price_fn, now=now)
    assert counts["too_early"] == 1
    assert called == []                      # 取得しない
    assert store.outcome_rows()[0]["status"] is None  # outcome 未作成
    store.close()


def test_score_pending_window_missed_without_fetch():
    store = Store(":memory:")
    _seed_alert(store)
    called = []

    def price_fn(ticker):
        called.append(ticker)
        return {"price": 110.0, "currency": "JPY", "as_of": None}

    now = datetime.now(UTC) + timedelta(hours=200)  # 上限超過
    counts = score_pending(store, max_age_hours=168, price_fn=price_fn, now=now)
    assert counts["window_missed"] == 1
    assert called == []                      # 取得せず期限切れで確定
    assert store.outcome_rows()[0]["status"] == "window_missed"
    store.close()


def test_score_pending_market_not_advanced_skips():
    store = Store(":memory:")
    _seed_alert(store)
    alerted = datetime.fromisoformat(store.pending_alerts()[0]["alerted_at"]).replace(
        tzinfo=UTC
    )

    def price_fn(ticker):
        # 市場の最終約定が alert から2hしか進んでいない(休場明け前など)。
        return {"price": 110.0, "currency": "JPY",
                "as_of": (alerted + timedelta(hours=2)).timestamp()}

    now = datetime.now(UTC) + timedelta(hours=48)
    counts = score_pending(store, min_age_hours=20, price_fn=price_fn, now=now)
    assert counts["too_early"] == 1
    assert store.outcome_rows()[0]["status"] is None  # 採点見送り
    store.close()


def test_score_pending_no_price_leaves_pending():
    store = Store(":memory:")
    _seed_alert(store)

    def price_fn(ticker):
        return None  # 取得失敗

    now = datetime.now(UTC) + timedelta(hours=48)  # 窓内
    counts = score_pending(store, min_age_hours=20, price_fn=price_fn, now=now)
    assert counts["no_price"] == 1
    # outcome は作らず pending のまま(窓内なら次回再試行)。
    assert store.outcome_rows()[0]["status"] is None
    assert len(store.pending_alerts()) == 1
    store.close()


def test_accuracy_report_scored_with_none_return_is_unavailable():
    # status が scored でも return_pct が None なら eval_unavailable に振り替える。
    rows = [
        {"entry_price": 100, "status": "scored", "return_pct": None, "score": 60,
         "expected_move_pct": None},
    ]
    rep = accuracy_report(rows)
    assert rep.scored == 0
    assert rep.eval_unavailable == 1


def test_score_pending_anomaly_and_currency():
    store = Store(":memory:")
    store.freeze_alert(
        event_id="a:split", ticker="1.T", entry_price=100.0, currency="JPY",
        entry_venue="regular", score=80, expected_move_pct=8,
    )
    store.freeze_alert(
        event_id="b:ccy", ticker="2.T", entry_price=100.0, currency="JPY",
        entry_venue="regular", score=80, expected_move_pct=8,
    )

    def price_fn(ticker):
        if ticker == "1.T":
            return {"price": 500.0, "currency": "JPY", "as_of": None}  # +400% 異常
        return {"price": 110.0, "currency": "USD", "as_of": None}      # 通貨不一致

    now = datetime.now(UTC) + timedelta(hours=48)
    counts = score_pending(store, min_age_hours=20, price_fn=price_fn, now=now)
    assert counts["anomaly"] == 1
    assert counts["eval_unavailable"] == 1
    rep = accuracy_report(store.outcome_rows())
    assert rep.anomaly == 1
    assert rep.eval_unavailable == 1
    assert rep.scored == 0
    store.close()
