"""Guards on the patterns web/michael.js uses to mark up an answer.

The page has no test runner, and these patterns decide what a reader sees
marked as a pinpoint citation. The source is lifted out of michael.js and
compiled with Python's re, which accepts every construct used here - word
boundaries, a negative lookahead, character classes and bounded repetition.
If someone reaches for a JavaScript-only construct, this file stops
compiling and that is the signal to move the guard into a JS runner rather
than to weaken it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

MICHAEL_JS = Path(__file__).resolve().parents[1] / "web" / "michael.js"


def _pattern(name: str) -> re.Pattern[str]:
    source = MICHAEL_JS.read_text(encoding="utf-8")
    match = re.search(rf"const {name} = new RegExp\(String\.raw`(.*?)`", source, re.DOTALL)
    assert match, f"{name} is no longer a String.raw RegExp in michael.js"
    return re.compile(match.group(1))


CITATION = _pattern("CITATION")


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Fair Work Act 2009 (Cth) s 117", "Fair Work Act 2009 (Cth)"),
        ("Privacy Act 1988 (Cth) s 26WK", "Privacy Act 1988 (Cth)"),
        (
            "Occupational Safety and Health Act 1984 (WA) s 19",
            "Occupational Safety and Health Act 1984 (WA)",
        ),
        (
            "Minimum Conditions of Employment Act 1993 (WA) s 17",
            "Minimum Conditions of Employment Act 1993 (WA)",
        ),
        (
            "Long Service Leave Act 1958 (WA) Sch 1 cl 4",
            "Long Service Leave Act 1958 (WA)",
        ),
        ("Fair Work Regulations 2009 (Cth) s 3.01", "Fair Work Regulations 2009 (Cth)"),
        (
            "Fair Work Act 2009 (Cth) s 117 (snapshot 2026-07-07)",
            "Fair Work Act 2009 (Cth)",
        ),
    ],
)
def test_a_real_citation_captures_the_act_name_and_nothing_else(text: str, expected: str) -> None:
    """Connectors inside a title are part of the name. Both of these are real
    Western Australian Acts, and dropping "and" or "of" would truncate them."""
    match = CITATION.search(text)
    assert match, text
    assert match.group(1) == expected


@pytest.mark.parametrize(
    "text",
    [
        "Either party may terminate the agreement with notice required by the "
        "Fair Work Act 2009 (Cth) s 117",
        "The employer must give the employee a Casual Employment Information "
        "Statement. Fair Work Act 2009 (Cth) s 125B",
        "Whether the engagement is in substance casual. Fair Work Act 2009 (Cth) s 15A",
        "This clause is based on Fair Work Act 2009 (Cth) s 15A",
        "An eligible data breach is defined in Privacy Act 1988 (Cth) s 26WE",
    ],
)
def test_prose_before_an_act_name_is_not_swallowed_into_the_citation(
    text: str,
) -> None:
    """Every sentence here is lifted from a real bench output. Before the fix
    each rendered as one citation chip, the longest 826px wide, because the
    act-name pattern ran backwards across the full stop."""
    match = CITATION.search(text)
    assert match, text
    assert len(match.group(1).split()) <= 6, match.group(1)
    assert "." not in match.group(1)


def test_the_pattern_carries_no_control_characters() -> None:
    """A backslash-escaping mistake once wrote a literal backspace into the
    pattern here, which reads as an ordinary regex on screen and matches
    nothing at all."""
    assert not any(ord(c) < 32 for c in CITATION.pattern)
