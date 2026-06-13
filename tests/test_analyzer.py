"""Analyzer のテスト(Claude クライアントはモック)。"""

from types import SimpleNamespace

from yosoku.analyzer import Analyzer
from yosoku.config import Config
from yosoku.models import Analysis, RawEvent


class FakeMessages:
    def __init__(self, analysis):
        self._analysis = analysis
        self.last_kwargs = None

    def parse(self, **kwargs):
        self.last_kwargs = kwargs
        return SimpleNamespace(parsed_output=self._analysis)


class FakeClient:
    def __init__(self, analysis):
        self.messages = FakeMessages(analysis)


def _config():
    cfg = Config()
    cfg.model = "claude-opus-4-8"
    # 値動き取得(yfinance/ネットワーク)を避ける
    cfg.analysis.enable_price_context = False
    return cfg


def test_analyze_backfills_ticker_and_name():
    # モデルが ticker/company_name を埋めなかったケース
    returned = Analysis(
        is_relevant=True,
        ticker=None,
        company_name=None,
        direction="bullish",
        score=75,
        confidence=60,
        horizon="days",
        rationale="増配で見直し買いが入りやすい。",
        key_factors=["増配"],
    )
    client = FakeClient(returned)
    analyzer = Analyzer(_config(), client=client)

    event = RawEvent(
        source="tdnet",
        event_id="tdnet:1",
        title="配当予想の修正（増配）に関するお知らせ",
        company_code="72030",
        company_name="サンプル自動車",
    )
    result = analyzer.analyze(event)

    assert result is not None
    # 証券コードから 7203.T を補完
    assert result.ticker == "7203.T"
    assert result.company_name == "サンプル自動車"

    # プロンプトに見出しと証券コードが含まれている
    user_msg = client.messages.last_kwargs["messages"][0]["content"]
    assert "増配" in user_msg
    assert "72030" in user_msg
    assert client.messages.last_kwargs["output_format"] is Analysis
    assert client.messages.last_kwargs["model"] == "claude-opus-4-8"


def test_analyze_returns_none_on_parse_failure():
    client = FakeClient(None)  # parsed_output が None
    analyzer = Analyzer(_config(), client=client)
    event = RawEvent(source="news", event_id="news:1", title="市場概況")
    assert analyzer.analyze(event) is None
