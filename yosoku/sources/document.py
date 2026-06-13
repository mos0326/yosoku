"""開示書類(PDF)の本文抽出.

TDnet の ``document_url`` は PDF。見出しだけでなく本文(売上・利益・ガイダンス等)を
LLM に渡すことで判定精度が大きく上がる。ダウンロードと抽出を分離し、抽出側
(`extract_text_from_pdf_bytes`)はネットワーク非依存で単体テストできる。
"""

from __future__ import annotations

import io
import logging
import re

import requests

logger = logging.getLogger(__name__)

_WS = re.compile(r"[ \t　]+")
_NL = re.compile(r"\n{3,}")


def _clean(text: str) -> str:
    text = _WS.sub(" ", text)
    text = _NL.sub("\n\n", text)
    return text.strip()


def extract_text_from_pdf_bytes(data: bytes, max_chars: int = 6000) -> str | None:
    """PDF バイト列からテキストを抽出する。失敗時は None。"""
    try:
        from pypdf import PdfReader
    except ImportError:
        logger.info("pypdf 未インストールのため本文抽出をスキップ")
        return None
    try:
        reader = PdfReader(io.BytesIO(data))
        chunks: list[str] = []
        total = 0
        for page in reader.pages:
            t = page.extract_text() or ""
            if not t:
                continue
            chunks.append(t)
            total += len(t)
            if total >= max_chars:
                break
        text = _clean("\n".join(chunks))
        if not text:
            return None
        return text[:max_chars]
    except Exception as e:  # 壊れたPDF等は best-effort
        logger.info("PDF本文抽出に失敗: %s", e)
        return None


def fetch_document_text(
    url: str | None,
    max_chars: int = 6000,
    timeout: float = 20.0,
    session: requests.Session | None = None,
) -> str | None:
    """URL から PDF を取得し本文を抽出する。PDF 以外/失敗時は None。"""
    if not url or ".pdf" not in url.lower():
        return None
    sess = session or requests
    try:
        resp = sess.get(url, timeout=timeout)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.info("開示PDF取得に失敗(%s): %s", url, e)
        return None
    return extract_text_from_pdf_bytes(resp.content, max_chars=max_chars)
