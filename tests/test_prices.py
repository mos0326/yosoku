from yosoku.sources.prices import to_yahoo_ticker


def test_5digit_numeric_with_trailing_zero():
    # 5桁数字・末尾0 は先頭4桁を採用
    assert to_yahoo_ticker("72030") == "7203.T"
    assert to_yahoo_ticker("34800") == "3480.T"


def test_4digit_passthrough():
    assert to_yahoo_ticker("7203") == "7203.T"


def test_alphanumeric_5char_kept():
    # 新形式の英数字コードはそのまま
    assert to_yahoo_ticker("130A0") == "130A0.T"


def test_empty():
    assert to_yahoo_ticker("") is None
    assert to_yahoo_ticker("   ") is None
