"""時刻ユーティリティ(JST基準).

TDnet 等の素朴な時刻文字列は JST として扱う。市場/活動時間帯の判定もここに集約し、
タイムゾーン計算の取り違えを一箇所に閉じ込める。
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

JST = timezone(timedelta(hours=9), name="JST")


def now_jst() -> datetime:
    return datetime.now(JST)


def to_jst(dt: datetime) -> datetime:
    """naive は JST とみなし、aware は JST に変換する。"""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=JST)
    return dt.astimezone(JST)


def parse_window(spec: str) -> tuple[time, time]:
    """'08:00-23:30' -> (time(8,0), time(23,30))。"""

    def _p(s: str) -> time:
        h, m = s.strip().split(":")
        return time(int(h), int(m))

    a, b = spec.split("-")
    return _p(a), _p(b)


def in_time_window(dt: datetime, spec: str) -> bool:
    """dt(JST換算)が時間帯 spec 内か。start<=end 前提(日跨ぎ非対応)。"""
    start, end = parse_window(spec)
    t = to_jst(dt).timetz().replace(tzinfo=None)
    return start <= t <= end


def is_active_now(
    active_window: str | None,
    weekdays_only: bool = True,
    now: datetime | None = None,
) -> bool:
    """いま稼働すべき時間帯か(純関数・テスト可)。

    active_window が None なら常時稼働。weekdays_only なら土日は休む。
    """
    n = to_jst(now) if now else now_jst()
    if weekdays_only and n.weekday() >= 5:  # 5=土, 6=日
        return False
    if active_window:
        return in_time_window(n, active_window)
    return True


def is_pts_hours(now: datetime | None = None) -> bool:
    """PTS(夜間/寄り前)時間帯か。平日の 寄り前 08:00-09:00 と 引け後 15:00-23:59。

    通常取引(09:00-15:00)中は False(その時間はザラ場価格を使う)。
    """
    n = to_jst(now) if now else now_jst()
    if n.weekday() >= 5:
        return False
    t = n.timetz().replace(tzinfo=None)
    return (time(8, 0) <= t < time(9, 0)) or (t >= time(15, 0))
