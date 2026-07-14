import io

import pytest

from app.pipeline.document_extract import (
    MAX_EXTRACTED_CHARS,
    ExtractionError,
    UnsupportedFileType,
    extract_text,
)


def test_plain_text_extraction():
    text, truncated = extract_text("notes.txt", b"hello world")
    assert text == "hello world"
    assert truncated is False


def test_extensionless_filename_treated_as_plain_text():
    text, truncated = extract_text("README", b"just text")
    assert text == "just text"
    assert truncated is False


def test_latin1_fallback_on_invalid_utf8():
    text, _ = extract_text("notes.txt", b"caf\xe9")
    assert "caf" in text


def test_unsupported_extension_raises():
    with pytest.raises(UnsupportedFileType):
        extract_text("archive.zip", b"whatever")


def test_truncates_past_max_chars():
    content = ("x" * (MAX_EXTRACTED_CHARS + 500)).encode()
    text, truncated = extract_text("big.txt", content)
    assert truncated is True
    assert len(text) == MAX_EXTRACTED_CHARS


def test_docx_extraction_round_trip():
    docx = pytest.importorskip("docx")
    doc = docx.Document()
    doc.add_paragraph("hello from docx")
    buf = io.BytesIO()
    doc.save(buf)

    text, truncated = extract_text("report.docx", buf.getvalue())
    assert "hello from docx" in text
    assert truncated is False


def test_docx_with_no_text_raises_extraction_error():
    docx = pytest.importorskip("docx")
    doc = docx.Document()  # no paragraphs added
    buf = io.BytesIO()
    doc.save(buf)

    with pytest.raises(ExtractionError):
        extract_text("empty.docx", buf.getvalue())


def test_corrupt_pdf_raises_extraction_error():
    pytest.importorskip("pypdf")
    with pytest.raises(ExtractionError):
        extract_text("bad.pdf", b"not a real pdf")
