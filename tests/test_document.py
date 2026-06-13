from yosoku.sources.document import extract_text_from_pdf_bytes, fetch_document_text


def _make_pdf(text: str) -> bytes:
    """テキストを1つ含む最小の有効PDFを、xrefオフセットを正しく計算して生成する。"""
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
    ]
    stream = b"BT /F1 24 Tf 72 700 Td (" + text.encode("latin-1") + b") Tj ET"
    objs.append(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    out = b"%PDF-1.4\n"
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += str(i).encode() + b" 0 obj\n" + body + b"\nendobj\n"
    xref_pos = len(out)
    n = len(objs) + 1
    out += b"xref\n0 " + str(n).encode() + b"\n0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (
        b"trailer\n<< /Size " + str(n).encode() + b" /Root 1 0 R >>\n"
        b"startxref\n" + str(xref_pos).encode() + b"\n%%EOF"
    )
    return out


def test_extract_text_from_real_pdf():
    pdf = _make_pdf("Revenue up 20 percent")
    text = extract_text_from_pdf_bytes(pdf)
    assert text is not None
    assert "Revenue" in text
    assert "20 percent" in text


def test_extract_truncates():
    pdf = _make_pdf("ABCDEFGHIJ")
    text = extract_text_from_pdf_bytes(pdf, max_chars=4)
    assert text is not None
    assert len(text) <= 4


def test_extract_garbage_returns_none():
    assert extract_text_from_pdf_bytes(b"not a pdf at all") is None


def test_fetch_document_text_non_pdf_url():
    # PDF 以外の URL は取得せず None
    assert fetch_document_text("https://example.com/page.html") is None
    assert fetch_document_text(None) is None
