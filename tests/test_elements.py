from pathlib import Path

import pytest

from michael.elements import Element, ElementConfigError, elements_for, load_elements


def test_the_shipped_file_loads() -> None:
    elements = load_elements()
    assert elements
    assert all(isinstance(e, Element) for e in elements)


def test_every_element_id_is_unique() -> None:
    ids = [e.id for e in load_elements()]
    assert len(ids) == len(set(ids))


def test_every_element_has_synonyms() -> None:
    assert all(e.synonyms for e in load_elements())


def test_employment_adds_to_the_universal_list_rather_than_replacing_it() -> None:
    universal = {e.id for e in elements_for()}
    employment = {e.id for e in elements_for("employment")}
    assert universal < employment
    assert "modern_award" in employment
    assert "governing_law" in employment


def test_an_unknown_domain_falls_back_to_universal() -> None:
    assert {e.id for e in elements_for("nonsense")} == {e.id for e in elements_for()}


def test_a_duplicate_id_is_refused(tmp_path: Path) -> None:
    bad = tmp_path / "elements.yaml"
    bad.write_text(
        "universal:\n"
        "  - {id: x, label: X, placeholder: X, synonyms: [x], basis: drafting convention}\n"
        "  - {id: x, label: Y, placeholder: Y, synonyms: [y], basis: drafting convention}\n",
        encoding="utf-8",
    )
    with pytest.raises(ElementConfigError, match="duplicate"):
        load_elements(bad)


def test_an_element_with_no_synonyms_is_refused(tmp_path: Path) -> None:
    bad = tmp_path / "elements.yaml"
    bad.write_text(
        "universal:\n"
        "  - {id: x, label: X, placeholder: X, synonyms: [], basis: drafting convention}\n",
        encoding="utf-8",
    )
    with pytest.raises(ElementConfigError, match="synonyms"):
        load_elements(bad)
