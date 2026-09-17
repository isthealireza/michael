import pytest

from michael.contract_text import ContractTextError, contract_text


def test_plain_utf8_passes_through() -> None:
    assert contract_text(b"1. PARTIES\nThe parties agree.") == "1. PARTIES\nThe parties agree."


def test_markdown_headings_are_stripped_to_their_text() -> None:
    body = b"## 1. PARTIES\n\n**Employer:** Acme Pty Ltd\n"
    out = contract_text(body)
    assert "1. PARTIES" in out
    assert "#" not in out
    assert "**" not in out
    assert "Employer: Acme Pty Ltd" in out


def test_html_is_converted() -> None:
    body = b"<html><body><h2>1. PARTIES</h2><p>The parties agree.</p></body></html>"
    out = contract_text(body)
    assert "1. PARTIES" in out
    assert "<h2>" not in out


def test_empty_input_is_an_error_naming_its_origin() -> None:
    with pytest.raises(ContractTextError, match="draft.docx"):
        contract_text(b"   \n  ", origin="draft.docx")
