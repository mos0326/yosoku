import asyncio

from yosoku.analyzer import AnalysisOutcome
from yosoku.config import Config
from yosoku.metrics import UsageTracker
from yosoku.models import Analysis, RawEvent, Signal
from yosoku.pipeline import (
    Pipeline,
    dedup_events,
    passes_prefilter,
    should_notify,
)
from yosoku.sources.base import Source
from yosoku.store import Store

# ---- フェイク -------------------------------------------------------------


class FakeSource(Source):
    name = "fake"

    def __init__(self, events):
        self._events = events

    def fetch(self):
        return list(self._events)


class FakeAnalyzer:
    """event_id → AnalysisOutcome|None で応答する非同期アナライザー。"""

    def __init__(self, mapping):
        self.mapping = mapping
        self.calls = []

    async def analyze(self, event):
        self.calls.append(event.event_id)
        return self.mapping.get(event.event_id)


class FakeNotifier:
    def __init__(self, succeed=True):
        self.succeed = succeed
        self.sent = []

    def notify(self, signal: Signal) -> bool:
        self.sent.append(signal)
        return self.succeed


def _event(eid="tdnet:1", source="tdnet", title="2026年3月期 決算短信〔上方修正〕", code="72030"):
    return RawEvent(
        source=source, event_id=eid, title=title, company_code=code, company_name="テスト"
    )


def _outcome(direction="bullish", score=80, conf=70, relevant=True, ticker="7203.T", stage="deep"):
    return AnalysisOutcome(
        Analysis(
            is_relevant=relevant,
            ticker=ticker,
            company_name="テスト",
            direction=direction,
            score=score,
            confidence=conf,
            horizon="days",
            rationale="上方修正で短期上昇が見込める。",
            key_factors=["上方修正"],
        ),
        stage,
    )


def _run(pipe):
    return asyncio.run(pipe.run_once())


# ---- 純関数 ---------------------------------------------------------------


def test_passes_prefilter_keyword():
    cfg = Config()
    cfg.analysis.relevance_keywords = ["上方修正", "決算"]
    assert passes_prefilter(_event(title="決算短信"), cfg) is True
    assert passes_prefilter(_event(title="本社移転のお知らせ"), cfg) is False
    assert passes_prefilter(_event(source="news", title="移転"), cfg) is True


def test_passes_prefilter_no_keywords_passes_all():
    cfg = Config()
    cfg.analysis.relevance_keywords = []
    assert passes_prefilter(_event(title="何でも"), cfg) is True


def test_should_notify_thresholds():
    cfg = Config()
    cfg.analysis.score_threshold = 60
    cfg.analysis.confidence_threshold = 50
    a = _outcome().analysis
    assert should_notify(a, cfg) is True
    assert should_notify(_outcome(score=50).analysis, cfg) is False
    assert should_notify(_outcome(conf=40).analysis, cfg) is False
    assert should_notify(_outcome(direction="bearish").analysis, cfg) is False
    assert should_notify(_outcome(relevant=False).analysis, cfg) is False


def test_should_notify_tickerless():
    cfg = Config()
    cfg.analysis.notify_tickerless = False
    assert should_notify(_outcome(ticker=None).analysis, cfg) is False
    cfg.analysis.notify_tickerless = True
    assert should_notify(_outcome(ticker=None).analysis, cfg) is True


def test_prefilter_excludes_pro_market():
    from yosoku.pipeline import is_pro_market

    cfg = Config()
    pro = _event(title="決算短信")
    pro.extra["markets_string"] = "東京プロマーケット"
    general = _event(title="決算短信")
    general.extra["markets_string"] = "東証グロース"

    assert is_pro_market(pro) is True
    assert is_pro_market(general) is False
    assert passes_prefilter(pro, cfg) is False       # プロマーケットは弾く
    assert passes_prefilter(general, cfg) is True     # 一般市場は通す
    cfg.analysis.exclude_pro_market = False
    assert passes_prefilter(pro, cfg) is True         # 除外オフなら通す


def test_embed_shows_price():
    from yosoku.notifier import build_embed

    ev = _event()
    ev.extra["price_at_alert"] = {
        "price": 170.0, "prev_close": 165.0, "change_pct": 3.03, "currency": "JPY"
    }
    sig = Signal(event=ev, analysis=_outcome(score=80).analysis, stage="deep")
    emb = build_embed(sig)
    assert "¥170" in emb["title"]
    assert any(f["name"].startswith("株価") for f in emb["fields"])


def test_dedup_events():
    evs = [_event(eid="a"), _event(eid="b"), _event(eid="a")]
    out = dedup_events(evs)
    assert [e.event_id for e in out] == ["a", "b"]


# ---- run_once 統合 --------------------------------------------------------


def _pipeline(events, mapping, cfg=None, notifier=None):
    cfg = cfg or Config()
    cfg.analysis.fetch_price_on_alert = False  # テストでは実ネットワークを叩かない
    store = Store(":memory:")
    analyzer = FakeAnalyzer(mapping)
    notifier = notifier if notifier is not None else FakeNotifier()
    pipe = Pipeline(
        config=cfg,
        sources=[FakeSource(events)],
        analyzer=analyzer,
        store=store,
        notifier=notifier,
        usage=UsageTracker(),
    )
    return pipe, store, analyzer, notifier


def test_run_once_notifies_bullish():
    ev = _event(eid="tdnet:100")
    pipe, store, analyzer, notifier = _pipeline([ev], {"tdnet:100": _outcome(score=80, conf=70)})
    result = _run(pipe)
    assert result.collected == 1
    assert result.new == 1
    assert result.analyzed == 1
    assert result.notified == 1
    assert len(notifier.sent) == 1
    # 履歴に記録される
    rows = store.recent_signals()
    assert len(rows) == 1
    assert rows[0]["notified"] == 1
    # 二度目は重複として何もしない
    result2 = _run(pipe)
    assert result2.new == 0
    assert result2.analyzed == 0


def test_run_once_skips_low_score():
    ev = _event(eid="tdnet:101")
    pipe, store, analyzer, notifier = _pipeline([ev], {"tdnet:101": _outcome(score=10, conf=70)})
    result = _run(pipe)
    assert result.analyzed == 1
    assert result.notified == 0
    assert notifier.sent == []
    assert store.is_seen("tdnet:101") is True


def test_run_once_prefilter_skips_analysis():
    cfg = Config()
    cfg.analysis.relevance_keywords = ["決算"]
    ev = _event(eid="tdnet:102", title="本社移転のお知らせ")
    pipe, store, analyzer, notifier = _pipeline([ev], {}, cfg=cfg)
    result = _run(pipe)
    assert analyzer.calls == []
    assert result.analyzed == 0
    assert store.is_seen("tdnet:102") is True


def test_run_once_analysis_failure_retries():
    ev = _event(eid="tdnet:103")
    pipe, store, analyzer, notifier = _pipeline([ev], {"tdnet:103": None})
    result = _run(pipe)
    assert result.analyzed == 0  # 失敗はカウントしない
    assert result.notified == 0
    assert store.is_seen("tdnet:103") is False


class FakeTextNotifier(FakeNotifier):
    def __init__(self, succeed=True):
        super().__init__(succeed)
        self.texts = []

    def notify_text(self, content: str) -> bool:
        self.texts.append(content)
        return self.succeed


def test_startup_ping_sent_once():
    ev = _event(eid="tdnet:200")
    store = Store(":memory:")
    notifier = FakeTextNotifier()
    pipe = Pipeline(
        config=Config(),
        sources=[FakeSource([ev])],
        analyzer=FakeAnalyzer({"tdnet:200": _outcome(score=10)}),
        store=store,
        notifier=notifier,
        usage=UsageTracker(),
    )
    asyncio.run(pipe.run_once())
    assert len(notifier.texts) == 1  # 初回に稼働通知が1回
    asyncio.run(pipe.run_once())
    assert len(notifier.texts) == 1  # 二度目は送らない


def test_watch_max_runtime_terminates():
    # max_runtime=0 なら1サイクル実行してクリーン終了する(無限ループしない)
    ev = _event(eid="tdnet:watch")
    pipe, store, analyzer, notifier = _pipeline([ev], {"tdnet:watch": _outcome(score=90)})
    # 戻ってくれば(ハングしなければ)成功
    asyncio.run(pipe.watch(interval=0, max_runtime=0))
    assert "tdnet:watch" in analyzer.calls


def test_run_once_concurrent_many():
    # 多数イベントでも並列に処理され、bullish のみ通知される
    events = [_event(eid=f"tdnet:{i}", title="決算短信") for i in range(20)]
    mapping = {
        f"tdnet:{i}": _outcome(score=90 if i % 2 == 0 else 10) for i in range(20)
    }
    cfg = Config()
    cfg.analysis.concurrency = 8
    pipe, store, analyzer, notifier = _pipeline(events, mapping, cfg=cfg)
    result = _run(pipe)
    assert result.analyzed == 20
    assert result.notified == 10
