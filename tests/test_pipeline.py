import asyncio

from yosoku.analyzer import AnalysisOutcome
from yosoku.config import Config
from yosoku.metrics import UsageTracker
from yosoku.models import Analysis, ArbiterVerdict, RawEvent, Signal
from yosoku.pipeline import (
    Pipeline,
    apply_verdict,
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


class FakeArbiterAnalyzer(FakeAnalyzer):
    """arbitrate 付きのフェイク(verdict=None なら判定なし、例外なら失敗を再現)。"""

    def __init__(self, mapping, verdict=None, raise_error=False):
        super().__init__(mapping)
        self.verdict = verdict
        self.raise_error = raise_error
        self.arbitrated = []

    async def arbitrate(self, event, analysis):
        self.arbitrated.append(event.event_id)
        if self.raise_error:
            raise RuntimeError("arbiter down")
        return self.verdict


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


def test_embed_shows_action_priority_and_expected_move():
    from yosoku.notifier import build_embed

    ev = _event()
    a = _outcome(score=80).analysis
    a.action = "今すぐ"
    a.priority = 4
    a.expected_move_pct = 12
    sig = Signal(event=ev, analysis=a, stage="deep")
    emb = build_embed(sig)
    names = {f["name"]: f["value"] for f in emb["fields"]}
    assert "🔥" in emb["title"]                 # アクション絵文字がタイトルに付く
    assert "今すぐ" in names["買い時"]
    assert "★★★★☆" in names["優先度"]
    assert names["想定上昇率"] == "+12%"


def test_embed_shows_pts_price():
    from yosoku.notifier import build_embed

    ev = _event()
    ev.extra["pts_price"] = {"price": 177.0, "time": "18:50　06/25"}
    sig = Signal(event=ev, analysis=_outcome(score=80).analysis, stage="deep")
    emb = build_embed(sig)
    names = {f["name"]: f["value"] for f in emb["fields"]}
    assert "PTS" in names
    assert "¥177" in names["PTS"]
    assert "18:50" in names["PTS"]


def test_dedup_events():
    evs = [_event(eid="a"), _event(eid="b"), _event(eid="a")]
    out = dedup_events(evs)
    assert [e.event_id for e in out] == ["a", "b"]


# ---- run_once 統合 --------------------------------------------------------


def _pipeline(events, mapping, cfg=None, notifier=None, analyzer=None):
    if cfg is None:
        cfg = Config()
        cfg.analysis.min_daily_alerts = 0  # 既定はデイリーピック無効(実行時刻に依存させない)
    cfg.analysis.fetch_price_on_alert = False  # テストでは実ネットワークを叩かない
    store = Store(":memory:")
    analyzer = analyzer or FakeAnalyzer(mapping)
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
    cfg = Config()
    cfg.analysis.min_daily_alerts = 0  # ピックの不足通知を混ぜない(時刻非依存に)
    pipe = Pipeline(
        config=cfg,
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
    cfg = Config()
    cfg.weekdays_only = False  # 実行日の曜日に依存せず1サイクル走らせる
    pipe, store, analyzer, notifier = _pipeline(
        [ev], {"tdnet:watch": _outcome(score=90)}, cfg=cfg
    )
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


# ---- 最終判定(arbiter) ----------------------------------------------------


def test_apply_verdict_partial_override():
    a = _outcome(score=80).analysis
    a.action, a.priority, a.expected_move_pct = "今すぐ", 4, 10
    v = ArbiterVerdict(approve=True, reason="妥当", action="押し目待ち")
    apply_verdict(a, v)
    assert a.action == "押し目待ち"
    assert a.priority == 4            # None のフィールドは据え置き
    assert a.expected_move_pct == 10


def test_arbiter_veto_blocks_notification():
    ev = _event()
    veto = ArbiterVerdict(approve=False, reason="織り込み済みと判断")
    analyzer = FakeArbiterAnalyzer({"tdnet:1": _outcome(score=90)}, verdict=veto)
    pipe, store, _, notifier = _pipeline([ev], {}, analyzer=analyzer)
    result = _run(pipe)
    assert result.notified == 0
    assert notifier.sent == []
    assert analyzer.arbitrated == ["tdnet:1"]
    assert store.is_seen("tdnet:1")   # 却下は確定扱い(次サイクルで再通知しない)


def test_arbiter_approve_refines_and_notifies():
    from yosoku.notifier import build_embed

    ev = _event()
    ok = ArbiterVerdict(approve=True, reason="初動前で妥当", action="今すぐ", priority=5)
    analyzer = FakeArbiterAnalyzer({"tdnet:1": _outcome(score=90)}, verdict=ok)
    pipe, store, _, notifier = _pipeline([ev], {}, analyzer=analyzer)
    result = _run(pipe)
    assert result.notified == 1
    sig = notifier.sent[0]
    assert sig.analysis.action == "今すぐ"
    assert sig.analysis.priority == 5
    names = {f["name"]: f["value"] for f in build_embed(sig)["fields"]}
    assert "🧠 最終判定" in names
    assert "初動前で妥当" in names["🧠 最終判定"]


def test_arbiter_failure_fails_open():
    ev = _event()
    analyzer = FakeArbiterAnalyzer({"tdnet:1": _outcome(score=90)}, raise_error=True)
    pipe, store, _, notifier = _pipeline([ev], {}, analyzer=analyzer)
    result = _run(pipe)
    assert result.notified == 1       # 判定が使えなくても通知は落とさない


def test_arbiter_disabled_skips_call():
    ev = _event()
    cfg = Config()
    cfg.analysis.enable_arbiter = False
    veto = ArbiterVerdict(approve=False, reason="使われないはず")
    analyzer = FakeArbiterAnalyzer({"tdnet:1": _outcome(score=90)}, verdict=veto)
    pipe, store, _, notifier = _pipeline([ev], {}, cfg=cfg, analyzer=analyzer)
    result = _run(pipe)
    assert result.notified == 1
    assert analyzer.arbitrated == []  # 無効時は呼ばれない


# ---- デイリーピック(1日最低件数の保証) -------------------------------------


def _seed_signal(store, eid, score=55, notified=False, ticker="7203.T"):
    ev = _event(eid=eid)
    store.record_signal(
        Signal(event=ev, analysis=_outcome(score=score, ticker=ticker).analysis, stage="deep"),
        notified=notified,
    )


def _pick_now():
    from yosoku.clock import now_jst

    return now_jst()  # daily_pick_time="00:00" と組み合わせて常に発火時刻扱いにする


def test_daily_pick_fills_quota_with_top_candidates():
    cfg = Config()
    cfg.analysis.min_daily_alerts = 2
    cfg.analysis.daily_pick_time = "00:00"
    pipe, store, _, notifier = _pipeline([], {}, cfg=cfg)
    _seed_signal(store, "tdnet:p1", score=55, ticker="1111.T")
    _seed_signal(store, "tdnet:p2", score=50, ticker="2222.T")
    _seed_signal(store, "tdnet:p3", score=45, ticker="3333.T")

    asyncio.run(pipe._maybe_daily_pick(now=_pick_now()))
    assert len(notifier.sent) == 2                       # 上位2件だけ
    tickers = {s.display_ticker for s in notifier.sent}
    assert tickers == {"1111.T", "2222.T"}
    assert all(s.event.extra.get("daily_pick") for s in notifier.sent)
    # 選ばれた候補は通知済みに更新され、二重選出されない
    asyncio.run(pipe._maybe_daily_pick(now=_pick_now()))
    assert len(notifier.sent) == 2                       # メタで1日1回


def test_daily_pick_skips_when_quota_met():
    cfg = Config()
    cfg.analysis.min_daily_alerts = 1
    cfg.analysis.daily_pick_time = "00:00"
    pipe, store, _, notifier = _pipeline([], {}, cfg=cfg)
    _seed_signal(store, "tdnet:done", score=90, notified=True)   # 当日すでに1件通知済み
    _seed_signal(store, "tdnet:cand", score=55)

    asyncio.run(pipe._maybe_daily_pick(now=_pick_now()))
    assert notifier.sent == []


def test_daily_pick_before_time_does_nothing():
    cfg = Config()
    cfg.analysis.min_daily_alerts = 2
    cfg.analysis.daily_pick_time = "19:00"
    pipe, store, _, notifier = _pipeline([], {}, cfg=cfg)
    _seed_signal(store, "tdnet:early", score=55)

    before = _pick_now().replace(hour=10, minute=0)
    asyncio.run(pipe._maybe_daily_pick(now=before))
    assert notifier.sent == []
    # メタも立てない(その日のうちに発火時刻が来たら実施できる)
    asyncio.run(pipe._maybe_daily_pick(now=before.replace(hour=20)))
    assert len(notifier.sent) == 1


def test_daily_pick_respects_min_score_floor():
    cfg = Config()
    cfg.analysis.min_daily_alerts = 2
    cfg.analysis.daily_pick_time = "00:00"
    cfg.analysis.daily_pick_min_score = 30
    pipe, store, _, notifier = _pipeline([], {}, cfg=cfg)
    _seed_signal(store, "tdnet:junk", score=10)          # floor 未満は採用しない

    asyncio.run(pipe._maybe_daily_pick(now=_pick_now()))
    assert notifier.sent == []


def test_daily_pick_embed_shows_kubun():
    from yosoku.notifier import build_embed, notify_content

    ev = _event()
    ev.extra["daily_pick"] = True
    sig = Signal(event=ev, analysis=_outcome(score=55).analysis, stage="deep")
    assert "本日の注目候補" in notify_content(sig)
    names = {f["name"]: f["value"] for f in build_embed(sig)["fields"]}
    assert "区分" in names
    assert "ベスト候補" in names["区分"]


def test_daily_pick_skips_ticker_already_notified_today():
    cfg = Config()
    cfg.analysis.min_daily_alerts = 2
    cfg.analysis.daily_pick_time = "00:00"
    pipe, store, _, notifier = _pipeline([], {}, cfg=cfg)
    # 同一銘柄: 朝に正規通知済み + 夕方に別イベントの未通知候補
    _seed_signal(store, "tdnet:morning", score=90, notified=True, ticker="7203.T")
    _seed_signal(store, "tdnet:dup", score=55, ticker="7203.T")
    _seed_signal(store, "tdnet:other", score=50, ticker="8888.T")

    asyncio.run(pipe._maybe_daily_pick(now=_pick_now()))
    # 不足1件は 7203.T の重複ではなく別銘柄で埋める
    assert [s.display_ticker for s in notifier.sent] == ["8888.T"]


# ---- クォータ枯渇の自己診断通知 ----------------------------------------------


def test_quota_alert_sent_once_per_day():
    class QuotaAnalyzer(FakeAnalyzer):
        quota_error = "You have reached your specified API usage limits."

    notifier = FakeTextNotifier()
    pipe, store, _, _ = _pipeline([], {}, notifier=notifier, analyzer=QuotaAnalyzer({}))
    _run(pipe)
    alerts = [t for t in notifier.texts if "利用上限" in t]
    assert len(alerts) == 1                          # 原因と直し方を通知
    assert "Settings → Limits" in alerts[0]
    _run(pipe)
    alerts2 = [t for t in notifier.texts if "利用上限" in t]
    assert len(alerts2) == 1                         # 同日は再送しない


def test_no_quota_alert_when_healthy():
    notifier = FakeTextNotifier()
    pipe, store, _, _ = _pipeline([], {}, notifier=notifier)
    _run(pipe)
    assert all("利用上限" not in t for t in notifier.texts)


def test_watch_warns_when_notifier_missing(caplog):
    import logging as _logging

    ev = _event(eid="tdnet:nn")
    cfg = Config()
    cfg.weekdays_only = False
    cfg.analysis.min_daily_alerts = 0
    cfg.analysis.fetch_price_on_alert = False
    pipe = Pipeline(
        config=cfg,
        sources=[FakeSource([ev])],
        analyzer=FakeAnalyzer({}),
        store=Store(":memory:"),
        notifier=None,  # DISCORD_WEBHOOK_URL 未設定相当
        usage=UsageTracker(),
    )
    with caplog.at_level(_logging.ERROR, logger="yosoku.pipeline"):
        asyncio.run(pipe.watch(interval=0, max_runtime=0))
    assert any("DISCORD_WEBHOOK_URL 未設定" in r.message for r in caplog.records)
