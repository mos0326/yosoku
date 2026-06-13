from yosoku.config import Config
from yosoku.models import Analysis, RawEvent, Signal
from yosoku.pipeline import Pipeline, passes_prefilter, should_notify
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
    """event_id → Analysis のマップで応答する。None も表現できる。"""

    def __init__(self, mapping):
        self.mapping = mapping
        self.calls = []

    def analyze(self, event):
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


def _analysis(direction="bullish", score=80, conf=70, relevant=True, ticker="7203.T"):
    return Analysis(
        is_relevant=relevant,
        ticker=ticker,
        company_name="テスト",
        direction=direction,
        score=score,
        confidence=conf,
        horizon="days",
        rationale="上方修正で短期上昇が見込める。",
        key_factors=["上方修正"],
    )


# ---- 純関数 ---------------------------------------------------------------


def test_passes_prefilter_keyword():
    cfg = Config()
    cfg.analysis.relevance_keywords = ["上方修正", "決算"]
    assert passes_prefilter(_event(title="決算短信"), cfg) is True
    assert passes_prefilter(_event(title="本社移転のお知らせ"), cfg) is False
    # ニュースは常に通す
    assert passes_prefilter(_event(source="news", title="移転"), cfg) is True


def test_passes_prefilter_no_keywords_passes_all():
    cfg = Config()
    cfg.analysis.relevance_keywords = []
    assert passes_prefilter(_event(title="何でも"), cfg) is True


def test_should_notify_thresholds():
    cfg = Config()
    cfg.analysis.score_threshold = 60
    cfg.analysis.confidence_threshold = 50

    assert should_notify(_analysis(score=80, conf=70), cfg) is True
    # スコア不足
    assert should_notify(_analysis(score=50, conf=70), cfg) is False
    # 確信度不足
    assert should_notify(_analysis(score=80, conf=40), cfg) is False
    # bearish は通知しない
    assert should_notify(_analysis(direction="bearish", score=80, conf=70), cfg) is False
    # 無関係は通知しない
    assert should_notify(_analysis(relevant=False), cfg) is False


def test_should_notify_tickerless():
    cfg = Config()
    cfg.analysis.notify_tickerless = False
    assert should_notify(_analysis(ticker=None), cfg) is False
    cfg.analysis.notify_tickerless = True
    assert should_notify(_analysis(ticker=None), cfg) is True


# ---- run_once 統合 --------------------------------------------------------


def _pipeline(events, mapping, cfg=None, notifier=None):
    cfg = cfg or Config()
    store = Store(":memory:")
    analyzer = FakeAnalyzer(mapping)
    notifier = notifier if notifier is not None else FakeNotifier()
    pipe = Pipeline(
        config=cfg,
        sources=[FakeSource(events)],
        analyzer=analyzer,
        store=store,
        notifier=notifier,
    )
    return pipe, store, analyzer, notifier


def test_run_once_notifies_bullish():
    ev = _event(eid="tdnet:100")
    pipe, store, analyzer, notifier = _pipeline(
        [ev], {"tdnet:100": _analysis(score=80, conf=70)}
    )
    result = pipe.run_once()
    assert result.collected == 1
    assert result.new == 1
    assert result.analyzed == 1
    assert result.notified == 1
    assert len(notifier.sent) == 1
    # 二度目は重複として何もしない
    result2 = pipe.run_once()
    assert result2.new == 0
    assert result2.analyzed == 0


def test_run_once_skips_low_score():
    ev = _event(eid="tdnet:101")
    pipe, store, analyzer, notifier = _pipeline(
        [ev], {"tdnet:101": _analysis(score=10, conf=70)}
    )
    result = pipe.run_once()
    assert result.analyzed == 1
    assert result.notified == 0
    assert notifier.sent == []
    # 分析済みなので seen 登録され、再分析されない
    assert store.is_seen("tdnet:101") is True


def test_run_once_prefilter_skips_analysis():
    cfg = Config()
    cfg.analysis.relevance_keywords = ["決算"]
    ev = _event(eid="tdnet:102", title="本社移転のお知らせ")
    pipe, store, analyzer, notifier = _pipeline([ev], {}, cfg=cfg)
    result = pipe.run_once()
    # 一次フィルタで弾かれ分析されない
    assert analyzer.calls == []
    assert result.analyzed == 0
    assert store.is_seen("tdnet:102") is True


def test_run_once_analysis_failure_retries():
    ev = _event(eid="tdnet:103")
    # 分析が None(失敗)を返すと seen にせず次回リトライ
    pipe, store, analyzer, notifier = _pipeline([ev], {"tdnet:103": None})
    result = pipe.run_once()
    assert result.analyzed == 1
    assert result.notified == 0
    assert store.is_seen("tdnet:103") is False
