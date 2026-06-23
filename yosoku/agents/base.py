"""エージェント(部門)の基底クラス."""

from __future__ import annotations

import abc

from yosoku.agents.bus import Bus


class Agent(abc.ABC):
    """1部門 = 1 Agent。`run()` を実装し、Bus 経由でメッセージを送受信する。"""

    name: str = "agent"

    def __init__(self, bus: Bus) -> None:
        self.bus = bus

    @abc.abstractmethod
    async def run(self) -> None:  # pragma: no cover - 抽象
        """常駐ループ。orchestrator から並行起動される。"""
        raise NotImplementedError
