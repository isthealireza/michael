"""Turn a contract file's bytes into text the clause splitter can read.

Deliberately separate from `michael.ingest.extract_text`, which does the same
job for legislation. The answering path must not import from `ingest` - the
guard test enforces it - and the two will diverge: PDF belongs here when it
arrives, and never there.
"""

from __future__ import annotations

import re

from michael.docx_text import docx_to_text, looks_like_docx
from michael.html_text import html_to_text, looks_like_html

#: A markdown heading marker at the start of a line, and inline bold markers.
#: Stripped so "## 1. PARTIES" is seen by the splitter as "1. PARTIES".
MARKDOWN_HEADING = re.compile(r"^#{1,6}[ \t]*", re.MULTILINE)
MARKDOWN_BOLD = re.compile(r"\*\*(?P<text>[^*]*)\*\*")


class ContractTextError(RuntimeError):
    """The file carried no usable text. Never silently returns an empty string."""


def _strip_markdown(text: str) -> str:
    """Remove the markdown markers that would hide a clause heading."""
    text = MARKDOWN_HEADING.sub("", text)
    return MARKDOWN_BOLD.sub(lambda m: m.group("text"), text)


def contract_text(body: bytes, *, content_type: str = "", origin: str = "") -> str:
    """Return the contract's text, or raise rather than return nothing.

    An empty result is always an error here. A comparison of two empty
    documents would report no changes, which is indistinguishable from a
    comparison of two identical documents and is the wrong answer to give.
    """
    where = origin or "input"

    if looks_like_docx(body, content_type):
        text = docx_to_text(body)
    else:
        decoded = body.decode("utf-8", errors="replace")
        text = html_to_text(decoded) if looks_like_html(decoded, content_type) else decoded

    text = _strip_markdown(text)
    if not text.strip():
        raise ContractTextError(f"{where}: no text survived extraction")
    return text
