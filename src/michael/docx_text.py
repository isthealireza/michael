"""Deterministic DOCX to plain text.

The Federal Register publishes the authorised consolidated text of an Act as
a Word document. A .docx is a zip archive holding XML, so this is built on
``zipfile`` and ``ElementTree`` rather than a new dependency: the extraction has
to be deterministic and testable, and that is easier to guarantee in fifty lines
we own than in a general-purpose document library.

Only what Michael needs is extracted: paragraph text, in document order, with
tabs preserved as spaces and each paragraph on its own line. Styles, images,
headers, footers and revision marks are ignored.
"""

from __future__ import annotations

import zipfile
from io import BytesIO
from xml.etree import ElementTree

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

DOCUMENT_PART = "word/document.xml"

#: A .docx is an OOXML zip. Every zip starts with this magic.
ZIP_MAGIC = b"PK\x03\x04"


class DocxError(RuntimeError):
    """The bytes are not a readable .docx."""


def looks_like_docx(body: bytes, content_type: str = "") -> bool:
    """True when ``body`` should go through :func:`docx_to_text`."""
    if not body.startswith(ZIP_MAGIC):
        return False
    if "wordprocessingml" in content_type.lower():
        return True
    # A zip that contains a Word document part is a .docx whatever it was
    # served as; the Register sometimes sends a generic octet-stream.
    try:
        with zipfile.ZipFile(BytesIO(body)) as archive:
            return DOCUMENT_PART in archive.namelist()
    except zipfile.BadZipFile:
        return False


def _paragraph_text(paragraph: ElementTree.Element) -> str:
    """Concatenate the text runs of one paragraph, in order."""
    pieces: list[str] = []
    for node in paragraph.iter():
        if node.tag == f"{W}t":
            pieces.append(node.text or "")
        elif node.tag == f"{W}tab":
            pieces.append(" ")
        elif node.tag in (f"{W}br", f"{W}cr"):
            pieces.append("\n")
    return "".join(pieces)


def docx_to_text(body: bytes) -> str:
    """Return the document text, one paragraph per line.

    Runs of blank lines collapse to one and trailing whitespace is dropped, so
    the output is stable and the section splitter's line-anchored regex can see
    each heading at the start of its own line.
    """
    try:
        with zipfile.ZipFile(BytesIO(body)) as archive:
            if DOCUMENT_PART not in archive.namelist():
                raise DocxError("archive contains no word/document.xml")
            xml = archive.read(DOCUMENT_PART)
    except zipfile.BadZipFile as exc:
        raise DocxError(f"not a readable zip archive: {exc}") from exc

    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError as exc:
        raise DocxError(f"word/document.xml is not well-formed: {exc}") from exc

    lines: list[str] = []
    for paragraph in root.iter(f"{W}p"):
        for raw in _paragraph_text(paragraph).split("\n"):
            lines.append(" ".join(raw.split()))

    cleaned: list[str] = []
    blank = 0
    for line in lines:
        if line:
            cleaned.append(line)
            blank = 0
        else:
            blank += 1
            if blank == 1 and cleaned:
                cleaned.append("")
    while cleaned and not cleaned[-1]:
        cleaned.pop()
    return "\n".join(cleaned)
