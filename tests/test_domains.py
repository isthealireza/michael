"""Domain routing: configuration only, and deterministic."""

from __future__ import annotations

from michael.domains import load_domains, route


def test_all_eight_domains_are_present() -> None:
    names = {d.name for d in load_domains()}
    assert names == {
        "employment",
        "contracts",
        "consumer",
        "property",
        "corporate",
        "work_health_safety",
        "privacy",
        "migration",
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


def test_every_domain_that_should_reach_case_law_does() -> None:
    """A domain's doc_types decide whether a judgment can be retrieved at all.

    `employment` filtered to [act, regulation, award] while the corpus held no
    judgments, which cost nothing. Once the case-law splitter landed it became
    a silent ceiling: unfair dismissal, casual characterisation and redundancy
    are argued from decided cases, and an employment question that could not
    reach one would answer from the Act alone and read as complete.

    Asserted here rather than left to the YAML, because nothing failed when the
    line was changed — the filter had no test of its own in either direction.
    """
    by_name = {d.name: d for d in load_domains()}
    for name in ("employment", "contracts", "consumer", "property", "corporate", "migration"):
        assert "case" in by_name[name].doc_types, f"{name} cannot retrieve a judgment"


def test_the_domains_that_deliberately_exclude_case_law_are_named() -> None:
    """The remaining two are a decision nobody has taken yet, not an oversight.

    `work_health_safety` and `privacy` also exclude `case`. Both are arguably
    wrong for the same reason `employment` was — WHS prosecutions and privacy
    determinations are reasoned in decided cases — but widening them moves the
    calibrated retrieval numbers, so they are left as they are and pinned here
    so the state is deliberate and visible rather than inherited.
    """
    by_name = {d.name: d for d in load_domains()}
    for name in ("work_health_safety", "privacy"):
        assert "case" not in by_name[name].doc_types, (
            f"{name} now retrieves case law - intended, but re-measure the "
            f"threshold and update this test rather than deleting it"
        )


def test_a_visa_request_routes_to_migration_and_filters_to_commonwealth() -> None:
    routing = route("what are the criteria for an employer sponsored visa")
    assert routing.name == "migration"
    assert routing.jurisdictions == ("commonwealth",)


def test_migration_does_not_take_employment_requests() -> None:
    assert route("I need a casual employment contract for a new employee").name == "employment"
    assert route("nomination of a director at the company AGM").name == "corporate"


def test_only_migration_reaches_departmental_guidance() -> None:
    """Guidance is admitted where it was asked for and nowhere else. Adding it
    to another domain changes what that domain retrieves, and moves the
    calibrated numbers; do it on purpose, and update this test."""
    by_name = {d.name: d for d in load_domains()}
    assert "guidance" in by_name["migration"].doc_types
    for name, domain in by_name.items():
        if name != "migration":
            assert "guidance" not in domain.doc_types, name
