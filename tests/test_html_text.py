"""HTML to text is deterministic, and must not leak markup into a provision."""

from __future__ import annotations

from michael.html_text import html_to_text, looks_like_html

PAGE = """<!DOCTYPE html>
<html><head><title>t</title><style>p{color:red}</style></head>
<body>
  <h1>Fair Work Act 2009</h1>
  <script>tracker();</script>
  <p>15A&nbsp;Meaning of <b>casual employee</b></p>
  <p>(1)&nbsp;A person is a <i>casual employee</i> of an employer if:</p>
  <ul><li>(a) the employment has no firm advance commitment; and</li>
      <li>(b) the person is entitled to a casual loading.</li></ul>
</body></html>"""


def test_no_markup_survives() -> None:
    text = html_to_text(PAGE)
    for fragment in ("<", ">", "&nbsp;", "tracker()", "color:red"):
        assert fragment not in text


def test_script_and_style_content_is_dropped() -> None:
    assert "tracker" not in html_to_text(PAGE)
    assert "color" not in html_to_text(PAGE)


def test_visible_text_is_preserved_including_inline_emphasis() -> None:
    text = html_to_text(PAGE)
    assert "15A Meaning of casual employee" in text
    assert "A person is a casual employee of an employer if:" in text
    assert "no firm advance commitment" in text


def test_block_elements_become_line_breaks_so_sections_can_be_split() -> None:
    lines = [line for line in html_to_text(PAGE).splitlines() if line]
    assert "Fair Work Act 2009" in lines
    assert any(line.startswith("15A ") for line in lines)


def test_output_is_stable_for_hashing() -> None:
    assert html_to_text(PAGE) == html_to_text(PAGE)


def test_blank_runs_collapse_and_edges_are_trimmed() -> None:
    text = html_to_text("<div></div><div></div><p>one</p><div></div><div></div><p>two</p>")
    assert text == "one\n\ntwo"


def test_detection_by_content_type_and_by_sniffing() -> None:
    assert looks_like_html("<p>x</p>", "text/html; charset=utf-8")
    assert looks_like_html("<!DOCTYPE html><html><body>x</body></html>")
    assert not looks_like_html("1. Short title\n\nThis Act may be cited...", "text/plain")


def test_entities_are_unescaped() -> None:
    assert html_to_text("<p>a &amp; b &lt; c</p>") == "a & b < c"


# A void element has no closing tag. Counting one as a suppressible element
# leaks the counter and silently discards the rest of the document — which is
# exactly what happened to a 1.2 MB Act from legislation.gov.au.
REAL_WORLD_HEAD = """<!DOCTYPE html><html lang="en"><head>
  <meta charset="utf-8">
  <title>Fair Work Act 2009 - Federal Register of Legislation</title>
  <base href="/">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="icon" type="image/x-icon" href="favicon.ico">
  <style>html{position:relative}</style>
</head><body><p>15A Meaning of casual employee</p></body></html>"""


def test_void_elements_in_head_do_not_suppress_the_body() -> None:
    text = html_to_text(REAL_WORLD_HEAD)
    assert "15A Meaning of casual employee" in text
    assert text, "body text was discarded by an unclosed void element"


def test_head_metadata_does_not_leak_into_the_text() -> None:
    text = html_to_text(REAL_WORLD_HEAD)
    assert "position:relative" not in text
    assert "width=device-width" not in text


def test_self_closing_dropped_element_does_not_suppress_what_follows() -> None:
    text = html_to_text("<body><svg/><p>operative words</p></body>")
    assert "operative words" in text


def test_a_long_document_with_many_void_elements_still_extracts() -> None:
    head = "<meta charset='utf-8'>" * 500 + "<link rel='x' href='y'>" * 500
    text = html_to_text(f"<html><head>{head}</head><body><p>section 7 text</p></body></html>")
    assert "section 7 text" in text
