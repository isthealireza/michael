"""DOCX extraction, proven against a real OOXML archive built in the test.

The point of these tests is the thing that actually matters for Michael: the
operative text of a provision, including subsection markers like (1) and (2),
must survive extraction. A parser that returns only headings would let a draft
cite a section whose "text" is its title.
"""

from __future__ import annotations

import zipfile
from io import BytesIO

import pytest

from michael.docx_text import DocxError, docx_to_text, looks_like_docx

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

PARAGRAPHS = [
    "15A  Meaning of casual employee",
    "(1)  A person is a casual employee of an employer if:",
    "(a)  the employment has no firm advance commitment; and",
    "(b)  the person would be entitled to a casual loading.",
    "(2)  For the purposes of subsection (1), the question whether the employment "
    "has a firm advance commitment is assessed on the basis of the real substance "
    "of the employment relationship.",
    "61  The National Employment Standards",
    "(1)  This Part sets minimum standards that apply to the employment of employees.",
]


def build_docx(paragraphs: list[str]) -> bytes:
    """A minimal but genuine .docx archive."""
    body = "".join(f'<w:p><w:r><w:t xml:space="preserve">{p}</w:t></w:r></w:p>' for p in paragraphs)
    document = (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f'<w:document xmlns:w="{W}"><w:body>{body}</w:body></w:document>'
    )
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types/>')
        archive.writestr("word/document.xml", document)
    return buffer.getvalue()


@pytest.fixture
def sample() -> bytes:
    return build_docx(PARAGRAPHS)


def test_detected_as_docx(sample: bytes) -> None:
    assert looks_like_docx(sample)
    assert looks_like_docx(
        sample, "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )


def test_plain_text_and_html_are_not_mistaken_for_docx() -> None:
    assert not looks_like_docx(b"1. Short title\n\nThis Act may be cited...")
    assert not looks_like_docx(b"<!DOCTYPE html><html></html>", "text/html")


def test_a_zip_that_is_not_a_word_document_is_rejected() -> None:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("readme.txt", "not a word document")
    assert not looks_like_docx(buffer.getvalue())
    with pytest.raises(DocxError, match="no word/document.xml"):
        docx_to_text(buffer.getvalue())


def test_corrupt_bytes_fail_closed() -> None:
    with pytest.raises(DocxError):
        docx_to_text(b"PK\x03\x04 and then garbage")


def test_operative_text_survives_not_just_headings(sample: bytes) -> None:
    """The regression that made this parser necessary."""
    text = docx_to_text(sample)
    assert "(1)" in text
    assert "(2)" in text
    assert "no firm advance commitment" in text
    assert "real substance" in text


def test_every_paragraph_is_on_its_own_line(sample: bytes) -> None:
    lines = docx_to_text(sample).splitlines()
    assert any(line.startswith("15A") for line in lines)
    assert any(line.startswith("(1)") for line in lines)
    assert any(line.startswith("61") for line in lines)


def test_sections_split_out_of_extracted_docx_text(sample: bytes) -> None:
    from michael.ingest import split_sections

    provisions = {p.section_number: p for p in split_sections(docx_to_text(sample))}
    assert "15A" in provisions
    assert provisions["15A"].heading == "Meaning of casual employee"
    # The body must carry operative words, not just the heading.
    assert "(1)" in provisions["15A"].text
    assert "casual loading" in provisions["15A"].text


def test_extraction_is_deterministic(sample: bytes) -> None:
    assert docx_to_text(sample) == docx_to_text(sample)


def test_tabs_and_breaks_become_whitespace() -> None:
    body = (
        f'<?xml version="1.0"?><w:document xmlns:w="{W}"><w:body>'
        f"<w:p><w:r><w:t>7</w:t><w:tab/><w:t>Heading</w:t><w:br/><w:t>after break</w:t></w:r></w:p>"
        f"</w:body></w:document>"
    )
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", body)
    text = docx_to_text(buffer.getvalue())
    assert "7 Heading" in text
    assert "after break" in text
