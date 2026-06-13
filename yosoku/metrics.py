"""コスト・トークン使用量の集計.

Anthropic の応答 ``usage`` を蓄積し、概算コスト($)を計算する。
可観測性(どのモデルにいくら使ったか)を出すために使う。
"""

from __future__ import annotations

from dataclasses import dataclass, field

# モデルごとの $/100万トークン (input, output)。
PRICING: dict[str, tuple[float, float]] = {
    "claude-fable-5": (10.0, 50.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-opus-4-6": (5.0, 25.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
}


def estimate_cost(
    model: str, input_tokens: int, output_tokens: int, cache_read_tokens: int = 0
) -> float:
    """概算コスト($)。キャッシュ読み出しは入力単価の約0.1倍で計上。"""
    pin, pout = PRICING.get(model, (0.0, 0.0))
    return (
        input_tokens * pin + output_tokens * pout + cache_read_tokens * pin * 0.1
    ) / 1_000_000


@dataclass
class _ModelUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    calls: int = 0


@dataclass
class UsageTracker:
    """モデル別のトークン使用量を蓄積する。

    単一イベントループ内からのみ更新する前提(ロック不要)。
    """

    by_model: dict[str, _ModelUsage] = field(default_factory=dict)

    def add(self, model: str, usage) -> None:
        """Anthropic の usage オブジェクト(または None)を加算する。"""
        if usage is None:
            return
        mu = self.by_model.setdefault(model, _ModelUsage())
        mu.input_tokens += int(getattr(usage, "input_tokens", 0) or 0)
        mu.output_tokens += int(getattr(usage, "output_tokens", 0) or 0)
        mu.cache_read_tokens += int(getattr(usage, "cache_read_input_tokens", 0) or 0)
        mu.calls += 1

    @property
    def total_cost(self) -> float:
        return sum(
            estimate_cost(m, u.input_tokens, u.output_tokens, u.cache_read_tokens)
            for m, u in self.by_model.items()
        )

    @property
    def total_calls(self) -> int:
        return sum(u.calls for u in self.by_model.values())

    def summary(self) -> str:
        if not self.by_model:
            return "LLM呼び出しなし"
        parts = []
        for m, u in self.by_model.items():
            parts.append(
                f"{m}: {u.calls}回 in={u.input_tokens} out={u.output_tokens} "
                f"${estimate_cost(m, u.input_tokens, u.output_tokens, u.cache_read_tokens):.4f}"
            )
        parts.append(f"合計 ${self.total_cost:.4f}")
        return " | ".join(parts)
