from datetime import datetime, timezone

from yosoku.clock import JST, in_time_window, is_active_now, parse_window, to_jst


def test_to_jst_naive_is_treated_as_jst():
    naive = datetime(2026, 6, 22, 10, 0, 0)
    aware = to_jst(naive)
    assert aware.tzinfo == JST
    assert aware.hour == 10


def test_to_jst_converts_utc():
    utc = datetime(2026, 6, 22, 1, 0, 0, tzinfo=timezone.utc)  # 10:00 JST
    assert to_jst(utc).hour == 10


def test_parse_window():
    start, end = parse_window("08:00-23:30")
    assert (start.hour, start.minute) == (8, 0)
    assert (end.hour, end.minute) == (23, 30)


def test_in_time_window():
    inside = datetime(2026, 6, 22, 10, 0, tzinfo=JST)
    outside = datetime(2026, 6, 22, 23, 45, tzinfo=JST)
    assert in_time_window(inside, "08:00-23:30") is True
    assert in_time_window(outside, "08:00-23:30") is False


def test_is_active_now():
    # 2026-06-22 は月曜
    monday_10 = datetime(2026, 6, 22, 10, 0, tzinfo=JST)
    monday_2am = datetime(2026, 6, 22, 2, 0, tzinfo=JST)
    saturday = datetime(2026, 6, 20, 10, 0, tzinfo=JST)  # 土曜

    assert is_active_now("08:00-23:30", weekdays_only=True, now=monday_10) is True
    assert is_active_now("08:00-23:30", weekdays_only=True, now=monday_2am) is False
    assert is_active_now("08:00-23:30", weekdays_only=True, now=saturday) is False
    # 窓なし(常時) + 平日 → True、土日は weekdays_only で False
    assert is_active_now(None, weekdays_only=True, now=monday_2am) is True
    assert is_active_now(None, weekdays_only=False, now=saturday) is True
