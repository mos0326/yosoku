"""TieredAnalyzer のテスト(Async Claude クライアントはモック)。"""

import asyncio
from types import SimpleNamespace

from yosoku.analyzer import TieredAnalyzer
from yosoku.config import Config
from yosoku.metrics import UsageTracker
from yosoku.models import Analysis, RawEvent


def _analysis(direction="bullish", score=80, conf=70, relevant=True, ticker=None, name=None):
    return Analysis(
        is_relevant=relevant,
        ticker=ticker,
        company_name=name,
        direction=direction,
        score=score,
        confidence=conf,
        horizon="days",
        rationale="理由",
        key_factors=["材料"],
    )


class FakeAsyncMessages:
    def __init__(self, by_model):
        self.by_model = by_model
        self.calls = []

    async def parse(self, **kwargs):
        self.calls.append(kwargs)
        analysis = self.by_model.get(kwargs["model"])
        usage = SimpleNamespace(input_tokens=10, output_tokens=5, cache_read_input_tokens=0)
        return SimpleNamespace(parsed_output=analysis, usage=usage)


class FakeAsyncClient:
    def __init__(self, by_model):
        self.messages = FakeAsyncMessages(by_model)

    async def close(self):
        pass


def _config():
    cfg = Config()
    cfg.model = "claude-opus-4-8"
    cfg.analysis.triage_model = "claude-haiku-4-5"
    cfg.analysis.enable_price_context = False
    cfg.analysis.fetch_document = False
    cfg.analysis.enable_web_context = False
    return cfg


def _run(coro):
    return asyncio.run(coro)


def test_two_stage_escalates_to_deep():
    cfg = _config()
    client = FakeAsyncClient(
        {
            "claude-haiku-4-5": _analysis(score=50),  # triage: 昇格ライン超え
            "claude-opus-4-8": _analysis(score=88),  # deep: 精査結果
        }
    )
    analyzer = TieredAnalyzer(cfg, client=client, usage=UsageTracker())
    event = RawEvent(source="tdnet", event_id="tdnet:1", title="決算", company_code="72030")
    outcome = _run(analyzer.analyze(event))
    assert outcome is not None
    assert outcome.stage == "deep"
    assert outcome.analysis.score == 88
    # 証券コードから ticker 補完
    assert outcome.analysis.ticker == "7203.T"
    # 両モデルが呼ばれている
    models = [c["model"] for c in client.messages.calls]
    assert "claude-haiku-4-5" in models and "claude-opus-4-8" in models


def test_two_stage_no_escalation_uses_triage():
    cfg = _config()
    cfg.analysis.triage_escalate_score = 30
    client = FakeAsyncClient({"claude-haiku-4-5": _analysis(score=10)})
    analyzer = TieredAnalyzer(cfg, client=client, usage=UsageTracker())
    event = RawEvent(source="tdnet", event_id="tdnet:2", title="軽微な訂正", company_code="72030")
    outcome = _run(analyzer.analyze(event))
    assert outcome.stage == "triage"
    assert outcome.analysis.score == 10
    # deep は呼ばれない
    assert [c["model"] for c in client.messages.calls] == ["claude-haiku-4-5"]


def test_deep_failure_falls_back_to_triage():
    cfg = _config()
    client = FakeAsyncClient(
        {"claude-haiku-4-5": _analysis(score=50), "claude-opus-4-8": None}
    )
    analyzer = TieredAnalyzer(cfg, client=client, usage=UsageTracker())
    event = RawEvent(source="tdnet", event_id="tdnet:3", title="決算", company_code="72030")
    outcome = _run(analyzer.analyze(event))
    assert outcome.stage == "triage"
    assert outcome.analysis.score == 50


def test_single_stage_only_deep():
    cfg = _config()
    cfg.analysis.two_stage = False
    client = FakeAsyncClient({"claude-opus-4-8": _analysis(score=70)})
    analyzer = TieredAnalyzer(cfg, client=client, usage=UsageTracker())
    event = RawEvent(source="tdnet", event_id="tdnet:4", title="決算", company_code="72030")
    outcome = _run(analyzer.analyze(event))
    assert outcome.stage == "deep"
    assert [c["model"] for c in client.messages.calls] == ["claude-opus-4-8"]


def test_fast_alert_skips_deep():
    # 明確な好材料(高スコア)は一次判定で即通知し、精査(deep)を呼ばない
    cfg = _config()
    cfg.analysis.fast_alert = True  # 速報優先を明示的に有効化
    client = FakeAsyncClient({"claude-haiku-4-5": _analysis(score=90, conf=80)})
    analyzer = TieredAnalyzer(cfg, client=client, usage=UsageTracker())
    event = RawEvent(
        source="tdnet", event_id="tdnet:fast", title="業績予想の上方修正", company_code="72030"
    )
    outcome = _run(analyzer.analyze(event))
    assert outcome.stage == "triage"
    assert outcome.analysis.score == 90
    # deep(opus)は呼ばれない
    assert [c["model"] for c in client.messages.calls] == ["claude-haiku-4-5"]


def test_fast_alert_disabled_goes_deep():
    cfg = _config()
    cfg.analysis.fast_alert = False
    client = FakeAsyncClient(
        {"claude-haiku-4-5": _analysis(score=90, conf=80), "claude-opus-4-8": _analysis(score=75)}
    )
    analyzer = TieredAnalyzer(cfg, client=client, usage=UsageTracker())
    event = RawEvent(
        source="tdnet", event_id="tdnet:slow", title="業績予想の上方修正", company_code="72030"
    )
    outcome = _run(analyzer.analyze(event))
    assert outcome.stage == "deep"
    assert "claude-opus-4-8" in [c["model"] for c in client.messages.calls]


def test_usage_is_tracked():
    cfg = _config()
    usage = UsageTracker()
    client = FakeAsyncClient(
        {"claude-haiku-4-5": _analysis(score=50), "claude-opus-4-8": _analysis(score=80)}
    )
    analyzer = TieredAnalyzer(cfg, client=client, usage=usage)
    event = RawEvent(source="tdnet", event_id="tdnet:5", title="決算", company_code="72030")
    _run(analyzer.analyze(event))
    assert usage.total_calls == 2
    assert usage.total_cost > 0


# ---- 最終判定(arbitrate) --------------------------------------------------


def _verdict(approve=True, reason="OK"):
    from yosoku.models import ArbiterVerdict

    return ArbiterVerdict(approve=approve, reason=reason)


def test_arbitrate_returns_verdict_without_thinking_param():
    cfg = _config()
    client = FakeAsyncClient({"claude-fable-5": _verdict(approve=True, reason="妥当")})
    analyzer = TieredAnalyzer(cfg, client=client, usage=UsageTracker())
    event = RawEvent(source="tdnet", event_id="tdnet:arb", title="上方修正", company_code="72030")
    out = _run(analyzer.arbitrate(event, _analysis(score=85)))
    assert out is not None and out.approve is True
    call = client.messages.calls[-1]
    assert call["model"] == "claude-fable-5"
    # thinking 常時ONのモデルなので thinking パラメータを渡さない(渡すと400)
    assert "thinking" not in call


def test_arbitrate_refusal_returns_none():
    class RefusingMessages(FakeAsyncMessages):
        async def parse(self, **kwargs):
            resp = await super().parse(**kwargs)
            resp.stop_reason = "refusal"
            return resp

    cfg = _config()
    client = FakeAsyncClient({"claude-fable-5": _verdict()})
    client.messages = RefusingMessages({"claude-fable-5": _verdict()})
    analyzer = TieredAnalyzer(cfg, client=client, usage=UsageTracker())
    event = RawEvent(source="tdnet", event_id="tdnet:ref", title="上方修正", company_code="72030")
    out = _run(analyzer.arbitrate(event, _analysis(score=85)))
    assert out is None  # 拒否はフェイルオープン(呼び出し側がそのまま通知)


def test_arbitrate_error_returns_none():
    class BoomMessages:
        async def parse(self, **kwargs):
            raise RuntimeError("boom")

    class BoomClient:
        def __init__(self):
            self.messages = BoomMessages()

        async def close(self):
            pass

    analyzer = TieredAnalyzer(_config(), client=BoomClient(), usage=UsageTracker())
    event = RawEvent(source="tdnet", event_id="tdnet:err", title="上方修正", company_code="72030")
    out = _run(analyzer.arbitrate(event, _analysis(score=85)))
    assert out is None
