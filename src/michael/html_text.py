"""Deterministic HTML to plain text.

legislation.gov.au serves the consolidated text of an Act as HTML. Storing that
markup as a provision would put tags inside the words Michael quotes as
operative, so it is stripped here before section splitting.

Written on the standard library's parser rather than pulling in a dependency:
the transformation is small, and it has to be deterministic and testable for
the same reason hashing and parsing are.
"""

from __future__ import annotations

from html.parser import HTMLParser

#: Elements whose *content* is never text. Only elements with a real closing
#: tag belong here: a void element such as <meta> or <link> never produces a
#: handle_endtag, so counting one would suppress the whole rest of the
#: document. That bug silently discarded a 1.2 MB Act.
DROPPED = frozenset({"script", "style", "noscript", "svg", "template"})

#: Void elements, which never close. Listed so that a DROPPED entry can never
#: leak the suppression counter if this set is edited later.
VOID = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)

#: Elements that end the current line.
BLOCKS = frozenset(
    {
        "p",
        "div",
        "br",
        "tr",
        "li",
        "section",
        "article",
        "header",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "table",
        "tbody",
        "thead",
        "blockquote",
    }
)


class _Extractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._suppress = 0

    def handle_starttag(self, tag: str, attrs: object) -> None:
        if tag in DROPPED and tag not in VOID:
            self._suppress += 1
        elif tag in BLOCKS:
            self.parts.append("\n")

    def handle_startendtag(self, tag: str, attrs: object) -> None:
        # Self-closing form (<svg/>) opens and closes at once, so it must not
        # touch the suppression counter.
        if tag in BLOCKS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in DROPPED and tag not in VOID:
            self._suppress = max(0, self._suppress - 1)
        elif tag in BLOCKS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._suppress:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    """Return the visible text of ``html``.

    Block elements become line breaks, runs of blank lines collapse to one, and
    trailing spaces are dropped, so the output is stable for hashing and for
    the section splitter's line-anchored regex.
    """
    parser = _Extractor()
    parser.feed(html)
    parser.close()

    text = "".join(parser.parts).replace("\xa0", " ")

    cleaned: list[str] = []
    blank = 0
    for raw in text.splitlines():
        line = " ".join(raw.split())
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


def looks_like_html(body: str, content_type: str = "") -> bool:
    """True when the body should be run through :func:`html_to_text` first."""
    if "html" in content_type.lower():
        return True
    head = body.lstrip()[:200].lower()
    return head.startswith("<!doctype html") or head.startswith("<html")
