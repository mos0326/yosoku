"""データソースの基底インターフェース."""

from __future__ import annotations

import abc

from yosoku.models import RawEvent


class Source(abc.ABC):
    """イベントを供給するソースの共通インターフェース。

    `fetch()` は「直近のイベント一覧(新しい→古い順は問わない)」を返す。
    重複排除はパイプライン側(store)で行うので、ソースは取得に専念する。
    """

    name: str = "base"

    @abc.abstractmethod
    def fetch(self) -> list[RawEvent]:  # pragma: no cover - 抽象メソッド
        raise NotImplementedError
