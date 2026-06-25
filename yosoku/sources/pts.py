"""PTS(私設取引システム)価格の取得.

Yahoo には日本株の PTS 値が無いため、株探(kabutan)のページから best-effort で
スクレイピングする。レイアウト変更や遮断で失敗しうるので、失敗時は None を返す
(致命的でない)。PTS 時間帯(夜間/寄り前)にのみ呼ぶ想定。
"""

from __future__ import annotations

import logging
import re

import requests

logger = logging.getLogger(__name__)

_URL = "https://kabutan.jp/stock/"
_UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
# <div class="kabuka1">PTS</div> <div class="kabuka2">177円</div>
#   <div class="kabuka3">18:50　06/25</div>
_RE_FULL = re.compile(
    r'kabuka1">PTS</div>\s*<div class="kabuka2">\s*([\d,]+)\s*円\s*</div>'
    r'\s*<div class="kabuka3">\s*([^<]*?)\s*</div>',
    re.S,
)
_RE_PRICE = re.compile(r'kabuka1">PTS</div>\s*<div class="kabuka2">\s*([\d,]+)\s*円', re.S)


def kabutan_code(ticker_or_code: str | None) -> str | None:
    """'4169.T' や '41690' から株探用の銘柄コードを得る。"""
    if not ticker_or_code:
        return None
    code = str(ticker_or_code).split(".")[0].strip().upper()
    if len(code) == 5 and code.isdigit() and code.endswith("0"):
        code = code[:4]
    return code or None


def parse_pts(html: str) -> dict | None:
    """株探HTMLから PTS 価格を抽出する(ネットワーク非依存・テスト可)。"""
    m = _RE_FULL.search(html)
    if m:
        when = m.group(2)
        raw = m.group(1)
    else:
        m = _RE_PRICE.search(html)
        if not m:
            return None
        when, raw = None, m.group(1)
    try:
        price = float(raw.replace(",", ""))
    except ValueError:
        return None
    return {"price": price, "time": when}


def pts_price(
    ticker_or_code: str, timeout: float = 10.0, session: requests.Session | None = None
) -> dict | None:
    """PTS 現在値を取得する。失敗時 None。返り値: {"price": float, "time": str|None}"""
    code = kabutan_code(ticker_or_code)
    if not code:
        return None
    sess = session or requests
    try:
        resp = sess.get(_URL, params={"code": code}, headers=_UA, timeout=timeout)
        resp.raise_for_status()
        return parse_pts(resp.text)
    except requests.RequestException as e:
        logger.info("PTS価格の取得に失敗(%s): %s", code, e)
        return None
