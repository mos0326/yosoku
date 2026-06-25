"""PTS(株探スクレイピング)のテスト。ネットワーク非依存(parse_pts は純関数)。"""

from yosoku.sources.pts import kabutan_code, parse_pts

# 株探の実レイアウトに合わせた最小HTML(検証済みパターン)。
_HTML_FULL = (
    '<div class="kabuka1">PTS</div> <div class="kabuka2">177円</div> '
    '<div class="kabuka3">18:50　06/25</div>'
)
# 価格はあるが時刻ブロックが欠けるケース。
_HTML_PRICE_ONLY = '<div class="kabuka1">PTS</div> <div class="kabuka2">2,705円</div>'
# PTS 気配なし。
_HTML_NONE = '<div class="kabuka1">現在値</div> <div class="kabuka2">1,000円</div>'


def test_kabutan_code_strips_suffix_and_padding():
    assert kabutan_code("4169.T") == "4169"
    assert kabutan_code("41690") == "4169"   # 5桁TSEコードの末尾0を落とす
    assert kabutan_code("7203") == "7203"
    assert kabutan_code(None) is None
    assert kabutan_code("") is None


def test_parse_pts_full():
    out = parse_pts(_HTML_FULL)
    assert out == {"price": 177.0, "time": "18:50　06/25"}


def test_parse_pts_with_comma_and_no_time():
    out = parse_pts(_HTML_PRICE_ONLY)
    assert out is not None
    assert out["price"] == 2705.0       # カンマ除去
    assert out["time"] is None


def test_parse_pts_returns_none_when_absent():
    assert parse_pts(_HTML_NONE) is None
    assert parse_pts("") is None
