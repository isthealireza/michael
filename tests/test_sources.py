"""The host allowlist is a hard boundary. These tests are the boundary's spec."""

from __future__ import annotations

import httpx
import pytest

from michael.sources import (
    ALLOWED_HOSTS,
    SourceRefused,
    check_doc_type,
    check_url,
    fetch,
    host_of,
    is_allowed_host,
    is_guidance_host,
)


@pytest.mark.parametrize("host", sorted(ALLOWED_HOSTS))
def test_allowlisted_hosts_are_allowed(host: str) -> None:
    assert is_allowed_host(host)
    assert check_url(f"https://{host}/some/path") == host


@pytest.mark.parametrize(
    "host",
    ["www.legislation.gov.au", "www2.austlii.edu.au", "classic.austlii.edu.au"],
)
def test_subdomains_of_allowlisted_hosts_are_allowed(host: str) -> None:
    assert is_allowed_host(host)


@pytest.mark.parametrize(
    "host",
    [
        "legislation.gov.au.evil.com",
        "notlegislation.gov.au",
        "austlii.edu.au.attacker.net",
        "example.com",
        "",
    ],
)
def test_lookalike_hosts_are_refused(host: str) -> None:
    assert not is_allowed_host(host)


@pytest.mark.parametrize(
    "url",
    [
        "http://legislation.gov.au/x",  # not https
        "ftp://legislation.gov.au/x",  # not https
        "https://user:pw@legislation.gov.au/x",  # embedded credentials
        "https://legislation.gov.au:8443/x",  # non-443 port
        "https://127.0.0.1/x",  # IP literal
        "https://example.com/x",  # off the allowlist
        "https://legislation.gov.au.evil.com/x",  # suffix lookalike
    ],
)
def test_check_url_refuses(url: str) -> None:
    with pytest.raises(SourceRefused):
        check_url(url)


def test_host_of_lowercases_and_strips_trailing_dot() -> None:
    assert host_of("https://Legislation.GOV.au./x") == "legislation.gov.au"


def test_refusal_is_logged_before_the_exception_escapes() -> None:
    from michael.config import settings

    with pytest.raises(SourceRefused):
        fetch("https://example.com/legislation.pdf")
    log = settings().ingestion_log
    assert log.is_file()
    assert "refused" in log.read_text(encoding="utf-8")


def test_redirect_off_the_allowlist_is_refused() -> None:
    """An allowed host may not redirect Michael onto a host that is not."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "legislation.gov.au":
            return httpx.Response(302, headers={"location": "https://evil.example.com/payload"})
        return httpx.Response(200, content=b"should never be reached")

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
    with pytest.raises(SourceRefused, match="evil.example.com"):
        fetch("https://legislation.gov.au/start", client=client)


def test_allowed_fetch_hashes_the_original_bytes_and_stores_them() -> None:
    import hashlib

    body = b"Section 1. Short title\n\nThis Act may be cited as the Example Act."

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body, headers={"content-type": "text/plain"})

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
    source = fetch("https://legislation.gov.au/example", client=client)

    assert source.sha256 == hashlib.sha256(body).hexdigest()
    assert source.path.read_bytes() == body
    assert source.host == "legislation.gov.au"


def test_a_local_file_cannot_launder_an_off_allowlist_source(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """ingest_file records provenance, so its source_url faces the same allowlist."""
    from michael.ingest import ingest_file

    document = tmp_path / "act.txt"
    document.write_text(
        "1. Short title\n\nThis Act may be cited as the Example Act.", encoding="utf-8"
    )

    with pytest.raises(SourceRefused, match="not on the allowlist"):
        ingest_file(
            path=document,
            source_url="https://example.com/fair-work-act.docx",
            jurisdiction="commonwealth",
            title="Example",
            citation="Example Act 2000 (Cth)",
            doc_type="act",
        )


# --- departmental guidance --------------------------------------------------


@pytest.mark.parametrize(
    "host", ["immigration.homeaffairs.gov.au", "www.immigration.homeaffairs.gov.au"]
)
def test_the_home_affairs_immigration_site_and_its_subdomains_are_allowed(host: str) -> None:
    assert is_allowed_host(host)
    assert is_guidance_host(host)


@pytest.mark.parametrize(
    "host",
    [
        "homeaffairs.gov.au",  # the parent was not asked for
        "www.homeaffairs.gov.au",
        "immigration.homeaffairs.gov.au.evil.com",
        "notimmigration.homeaffairs.gov.au",
    ],
)
def test_only_the_immigration_site_was_added(host: str) -> None:
    assert not is_allowed_host(host)
    assert not is_guidance_host(host)


GUIDANCE_URL = "https://immigration.homeaffairs.gov.au/visas/fixture"
LEGISLATION_URL = "https://www.legislation.gov.au/C1958A00062/latest/text"


@pytest.mark.parametrize("doc_type", ["act", "regulation", "award", "case"])
def test_a_guidance_page_cannot_be_stored_as_law(doc_type: str) -> None:
    with pytest.raises(SourceRefused, match="departmental guidance"):
        check_doc_type(GUIDANCE_URL, doc_type)


def test_law_cannot_be_stored_as_guidance() -> None:
    with pytest.raises(SourceRefused, match="only for"):
        check_doc_type(LEGISLATION_URL, "guidance")


def test_the_matching_pairs_pass() -> None:
    check_doc_type(GUIDANCE_URL, "guidance")
    check_doc_type(LEGISLATION_URL, "act")
