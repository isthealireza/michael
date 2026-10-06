from __future__ import annotations

from typing import Any, Literal
from unittest.mock import patch

from michael.retrieve import (
    _extract_instrument_name,
    _extract_pinpoint,
    _query_jurisdiction,
    _resolve_instrument,
)

#: A database row and a calibration entry are both heterogeneous mappings:
#: these tests read named keys out of them and never depend on one value type.
_Row = dict[str, Any]


class _Cursor:
    def __init__(self, rows: list[_Row]) -> None:
        self._rows = rows

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *args: object) -> Literal[False]:
        return False

    def execute(self, *args: object, **kwargs: object) -> None:
        return None

    def fetchall(self) -> list[_Row]:
        return self._rows


class _Conn:
    def __init__(self, rows: list[_Row]) -> None:
        self._rows = rows

    def __enter__(self) -> _Conn:
        return self

    def __exit__(self, *args: object) -> Literal[False]:
        return False

    def cursor(self) -> _Cursor:
        return _Cursor(self._rows)


def _doc(id_: int, jurisdiction: str, citation: str, title: str = "") -> _Row:
    return {"id": id_, "jurisdiction": jurisdiction, "citation": citation, "title": title}


def test_wa_jurisdiction_narrows_retrieval_away_from_the_cth_twin() -> None:
    query = "primary duty of care under Western Australian work health and safety law"
    assert _query_jurisdiction(query) == "wa"
    with patch("michael.retrieve.readonly", return_value=_Conn([])):
        resolution = _resolve_instrument(query)
    assert resolution.jurisdiction == "wa"
    assert resolution.document_ids == ()  # no instrument named -> jurisdiction-only narrowing


def test_naming_corporations_act_2001_resolves_that_instrument_not_regulations() -> None:
    rows = [
        _doc(10, "commonwealth", "Corporations Act 2001 (Cth)", "Corporations Act 2001"),
        _doc(11, "commonwealth", "Corporations Regulations 2001 (Cth)", "Corporations Regulations 2001"),
    ]
    with patch("michael.retrieve.readonly", return_value=_Conn(rows)):
        resolution = _resolve_instrument("Corporations Act 2001 (Cth) s 180")
    assert resolution.document_ids == (10,)
    assert resolution.pinpoint == "180"
    assert resolution.jurisdiction == "commonwealth"


def test_a_known_absent_query_resolves_to_no_instrument_or_pinpoint() -> None:
    with patch("michael.retrieve.readonly", return_value=_Conn([])):
        resolution = _resolve_instrument("how do I bake sourdough bread at home")
    assert resolution.document_ids == ()
    assert resolution.pinpoint is None
    assert resolution.jurisdiction is None


def test_decimal_regulation_pinpoint_is_detected() -> None:
    assert _extract_pinpoint("Corporations Regulations 2001 reg 1.0.01") == "1.0.01"


def test_schedule_and_plain_section_pinpoints_are_detected() -> None:
    assert _extract_pinpoint("Competition and Consumer Act Sch 2 cl 54") == "Sch 2 cl 54"
    assert _extract_pinpoint("what does section 19 say") == "19"


def test_instrument_name_extracts_the_title_verbatim() -> None:
    assert _extract_instrument_name("Work Health and Safety Act 2020 duty") == "Work Health and Safety Act 2020"
    assert _extract_instrument_name("the Act does not matter") is None
    assert _extract_instrument_name("The Act does not matter") is None
    assert _extract_instrument_name("An Act to provide for...") is None


def test_wa_duty_of_care_query_resolves_to_wa_not_cth() -> None:
    import json
    from pathlib import Path

    path = Path(__file__).parents[1] / "calibration" / "instrument_queries.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    wa = next(c for c in data["known_good"] if c["citation"] == "Work Health and Safety Act 2020 (WA)")
    rows = [
        _doc(10, "wa", "Work Health and Safety Act 2020 (WA)"),
        _doc(11, "commonwealth", "Work Health and Safety Act 2011 (Cth)"),
    ]
    with patch("michael.retrieve.readonly", return_value=_Conn(rows)):
        resolution = _resolve_instrument(wa["query"])
    assert resolution.document_ids == (10,)


def test_every_named_query_resolves_to_the_named_instrument_only() -> None:
    import json
    from pathlib import Path

    data = json.loads((Path(__file__).parents[1] / "calibration" / "instrument_queries.json").read_text())
    docs = [
        _doc(i + 1, "wa" if "(WA)" in g["citation"] else "commonwealth", g["citation"])
        for i, g in enumerate(data["known_good"])
    ]
    by_id = {d["id"]: d["citation"] for d in docs}
    for g in data["known_good"]:
        with patch("michael.retrieve.readonly", return_value=_Conn(docs)):
            resolution = _resolve_instrument(g["query"])
        citations = {by_id[i] for i in resolution.document_ids}
        assert citations == {g["citation"]}, f"{g['query']} -> {citations}"


def test_absent_queries_resolve_to_no_instrument() -> None:
    import json
    from pathlib import Path

    data = json.loads((Path(__file__).parents[1] / "calibration" / "instrument_queries.json").read_text())
    docs = [
        _doc(i + 1, "wa" if "(WA)" in g["citation"] else "commonwealth", g["citation"])
        for i, g in enumerate(data["known_good"])
    ]
    for a in data["known_absent"]:
        with patch("michael.retrieve.readonly", return_value=_Conn(docs)):
            resolution = _resolve_instrument(a["query"])
        assert resolution.document_ids == (), f"{a['query']} -> {resolution.document_ids}"
