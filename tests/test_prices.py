from yosoku.sources.prices import current_price, to_yahoo_ticker


class _FakeResp:
    def __init__(self, payload):
        self._p = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._p


class _FakeSession:
    def __init__(self, payload):
        self._p = payload

    def get(self, url, **kwargs):
        return _FakeResp(self._p)


def test_current_price_parse():
    payload = {
        "chart": {
            "result": [
                {"meta": {"regularMarketPrice": 170.0, "chartPreviousClose": 165.0, "currency": "JPY"}}
            ]
        }
    }
    pi = current_price("4169.T", session=_FakeSession(payload))
    assert pi is not None
    assert pi["price"] == 170.0
    assert pi["currency"] == "JPY"
    assert abs(pi["change_pct"] - (5.0 / 165.0 * 100.0)) < 1e-6


def test_current_price_bad_returns_none():
    assert current_price("") is None
    assert current_price("X.T", session=_FakeSession({"chart": {"result": []}})) is None
    assert current_price("X.T", session=_FakeSession({"chart": {"result": [{"meta": {}}]}})) is None



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
