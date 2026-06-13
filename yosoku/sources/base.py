"""データソースの基底インターフェース."""

from __future__ import annotations

import abc
import asyncio

from yosoku.models import RawEvent


class Source(abc.ABC):
    """イベントを供給するソースの共通インターフェース。

    `fetch()` は同期取得。`fetch_async()` は既定で `fetch` を別スレッドに
    逃がすラッパで、複数ソースを並行取得するのに使う。
    """

    name: str = "base"

    @abc.abstractmethod
    def fetch(self) -> list[RawEvent]:  # pragma: no cover - 抽象メソッド
        raise NotImplementedError

    async def fetch_async(self) -> list[RawEvent]:
        return await asyncio.to_thread(self.fetch)
