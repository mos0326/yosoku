from types import SimpleNamespace

from yosoku.metrics import UsageTracker, estimate_cost


def test_estimate_cost_opus():
    # opus 4.8: $5/1M in, $25/1M out
    assert estimate_cost("claude-opus-4-8", 1_000_000, 0) == 5.0
    assert estimate_cost("claude-opus-4-8", 0, 1_000_000) == 25.0
    # キャッシュ読みは入力の0.1倍
    assert estimate_cost("claude-opus-4-8", 0, 0, cache_read_tokens=1_000_000) == 0.5


def test_estimate_cost_unknown_model_zero():
    assert estimate_cost("unknown-model", 1_000_000, 1_000_000) == 0.0


def test_usage_tracker_accumulates():
    t = UsageTracker()
    u = SimpleNamespace(input_tokens=100, output_tokens=50, cache_read_input_tokens=0)
    t.add("claude-haiku-4-5", u)
    t.add("claude-haiku-4-5", u)
    t.add("claude-opus-4-8", None)  # None は無視
    assert t.total_calls == 2
    assert t.by_model["claude-haiku-4-5"].input_tokens == 200
    assert t.total_cost > 0
    assert "claude-haiku-4-5" in t.summary()
