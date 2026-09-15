"""Domain routing: configuration only, and deterministic."""

from __future__ import annotations

from michael.domains import load_domains, route


def test_all_seven_seed_domains_are_present() -> None:
    names = {d.name for d in load_domains()}
    assert names == {
        "employment",
        "contracts",
        "consumer",
        "property",
        "corporate",
        "work_health_safety",
        "privacy",
    }


def test_employment_request_routes_to_employment_and_filters_to_commonwealth() -> None:
    routing = route("I need a casual employment contract for a new employee")
    assert routing.name == "employment"
    assert routing.jurisdictions == ("commonwealth",)
    assert "casual" in routing.matched_keywords


def test_a_specific_phrase_outweighs_a_generic_word() -> None:
    """'australian consumer law' beats a bare 'contract' in another domain."""
    routing = route("does the australian consumer law apply to this supply contract")
    assert routing.name == "consumer"


def test_unrecognised_request_is_not_refused_it_is_reported() -> None:
    routing = route("what is the airspeed velocity of an unladen swallow")
    assert routing.domain is None
    assert routing.recognised is False
    assert routing.jurisdictions == ()  # unfiltered search
    assert routing.doc_types == ()


def test_keyword_matching_is_whole_word() -> None:
    """'award' must not fire on 'awarded damages'."""
    routing = route("the court awarded damages to the plaintiff")
    assert routing.name != "employment" or "award" not in routing.matched_keywords


def test_routing_is_deterministic() -> None:
    request = "privacy policy and data breach obligations"
    assert route(request).name == route(request).name == "privacy"
