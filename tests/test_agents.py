import asyncio

from yosoku.agents.analyst import AnalystAgent
from yosoku.agents.bus import Bus
from yosoku.agents.messages import APPROVED, RAW_EVENTS, SIGNALS
from yosoku.agents.notifier import NotifierAgent
from yosoku.agents.risk import RiskAgent
from yosoku.analyzer import AnalysisOutcome
from yosoku.config import Config
from yosoku.models import Analysis, RawEvent, Signal
from yosoku.store import Store


def _outcome(score=85, conf=70, direction="bullish", relevant=True):
    return AnalysisOutcome(
        Analysis(
            is_relevant=relevant,
            ticker="7203.T",
            company_name="テスト",
            direction=direction,
            score=score,
            confidence=conf,
            horizon="days",
            rationale="理由",
            key_factors=["材料"],
        ),
        "deep",
    )


def _signal(score=85):
    return Signal(
        event=RawEvent(source="tdnet", event_id="tdnet:x", title="決算", company_code="72030"),
        analysis=_outcome(score=score).analysis,
        stage="deep",
    )


class FakeAnalyzer:
    def __init__(self, mapping):
        self.mapping = mapping

    async def analyze(self, event):
        return self.mapping.get(event.event_id)


class FakeNotifier:
    def __init__(self):
        self.sent = []

    def notify(self, signal):
        self.sent.append(signal)
        return True


# ---- Bus ------------------------------------------------------------------


def test_bus_pub_sub():
    async def go():
        bus = Bus()
        q = bus.subscribe("t")
        await bus.publish("t", 123)
        return await q.get()

    assert asyncio.run(go()) == 123


def test_bus_fanout():
    async def go():
        bus = Bus()
        q1, q2 = bus.subscribe("t"), bus.subscribe("t")
        await bus.publish("t", "x")
        return await q1.get(), await q2.get()

    assert asyncio.run(go()) == ("x", "x")


# ---- Risk evaluate --------------------------------------------------------


def test_risk_evaluate_thresholds():
    risk = RiskAgent(Bus(), Store(":memory:"), Config(), daily_cap=50)
    assert risk.evaluate(_signal(score=85)) is True
    assert risk.evaluate(_signal(score=10)) is False  # 閾値未満


def test_risk_daily_cap():
    risk = RiskAgent(Bus(), Store(":memory:"), Config(), daily_cap=1)
    assert risk.evaluate(_signal(score=85)) is True
    risk._count = 1  # 上限到達
    assert risk.evaluate(_signal(score=85)) is False


# ---- 部門連携(end-to-end) -------------------------------------------------


def test_agents_flow_event_to_notification():
    async def go():
        bus = Bus()
        store = Store(":memory:")
        ev = RawEvent(source="tdnet", event_id="tdnet:flow", title="決算", company_code="72030")
        analyzer = FakeAnalyzer({"tdnet:flow": _outcome(score=85)})
        notifier = FakeNotifier()

        agents = [
            AnalystAgent(bus, analyzer),
            RiskAgent(bus, store, Config()),
            NotifierAgent(bus, notifier),
        ]
        tasks = [asyncio.create_task(a.run()) for a in agents]
        await asyncio.sleep(0.05)  # 各部門が subscribe するのを待つ
        await bus.publish(RAW_EVENTS, ev)
        await asyncio.sleep(0.2)  # 収集→分析→リスク→通知 を流す
        for t in tasks:
            t.cancel()
        return notifier.sent

    sent = asyncio.run(go())
    assert len(sent) == 1
    assert sent[0].event.event_id == "tdnet:flow"


def test_topics_constants():
    assert (RAW_EVENTS, SIGNALS, APPROVED) == (
        "events.raw",
        "signals.analyzed",
        "signals.approved",
    )
