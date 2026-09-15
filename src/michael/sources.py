"""Source fetching, restricted to an allowlist of official hosts.

The allowlist is a hard boundary. There is no configuration key, no environment
override and no argument that widens it. A refusal is logged and raised.

Every hop of a redirect chain is validated: a 302 from an allowed host to an
arbitrary host is a refusal, not a fetch.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from michael.config import settings

#: The only hosts Michael will fetch from. Subdomains of these hosts are
#: included (``www2.austlii.edu.au``); nothing else is.
ALLOWED_HOSTS: frozenset[str] = frozenset(
    {
        "legislation.wa.gov.au",
        "legislation.gov.au",
        "fairwork.gov.au",
        "austlii.edu.au",
    }
)

MAX_REDIRECTS = 5
MAX_BYTES = 64 * 1024 * 1024
TIMEOUT_SECONDS = 60.0


class SourceRefused(Exception):
    """The URL is not fetchable under the allowlist. Never recoverable."""


class SourceFetchFailed(Exception):
    """The host was allowed but the fetch did not succeed."""


@dataclass(frozen=True, slots=True)
class FetchedSource:
    """A downloaded original. Immutable once written to ``sources/``."""

    url: str
    host: str
    sha256: str
    content_type: str
    fetched_at: datetime
    path: Path
    body: bytes


def host_of(url: str) -> str:
    """Return the lowercase hostname, or '' if the URL has none."""
    return (urlsplit(url).hostname or "").lower().rstrip(".")


def is_allowed_host(host: str) -> bool:
    """True if ``host`` is an allowlisted host or a subdomain of one."""
    host = host.lower().rstrip(".")
    if not host:
        return False
    return any(host == allowed or host.endswith("." + allowed) for allowed in ALLOWED_HOSTS)


def check_url(url: str) -> str:
    """Validate ``url`` and return its host, or raise :class:`SourceRefused`.

    Rejects anything that could be used to reach a host other than the one the
    URL appears to name: non-HTTPS schemes, embedded credentials, IP literals,
    non-443 ports, and any host outside the allowlist.
    """
    parts = urlsplit(url)

    if parts.scheme != "https":
        raise SourceRefused(f"refused: scheme {parts.scheme or '(none)'!r} is not https ({url})")
    if parts.username or parts.password:
        raise SourceRefused(f"refused: URL carries credentials ({url})")

    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        raise SourceRefused(f"refused: URL has no host ({url})")

    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise SourceRefused(f"refused: IP literal {host} is not an allowlisted host ({url})")

    if parts.port not in (None, 443):
        raise SourceRefused(f"refused: port {parts.port} is not 443 ({url})")

    if not is_allowed_host(host):
        raise SourceRefused(
            f"refused: host {host!r} is not on the allowlist "
            f"({', '.join(sorted(ALLOWED_HOSTS))}) ({url})"
        )

    return host


def log_attempt(
    *,
    url: str,
    host: str,
    outcome: str,
    reason: str = "",
    sha256: str | None = None,
) -> None:
    """Append one line to the ingestion log.

    The log is written before the database row exists, so a refusal is recorded
    even when nothing is ingested.
    """
    record = {
        "at": datetime.now(UTC).isoformat(),
        "url": url,
        "host": host,
        "outcome": outcome,
        "reason": reason,
        "sha256": sha256,
    }
    path = settings().ingestion_log
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def fetch(url: str, *, client: httpx.Client | None = None) -> FetchedSource:
    """Fetch ``url`` from an allowlisted host and store the original bytes.

    The sha256 is computed over the bytes as downloaded, before any parsing.
    """
    try:
        host = check_url(url)
    except SourceRefused as exc:
        log_attempt(url=url, host=host_of(url), outcome="refused", reason=str(exc))
        raise

    owns_client = client is None
    client = client or httpx.Client(
        timeout=TIMEOUT_SECONDS,
        follow_redirects=False,
        headers={"User-Agent": "Michael/0.1 (internal legal research tool)"},
    )
    try:
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            response = client.get(current)
            if response.is_redirect:
                location = response.headers.get("location", "")
                if not location:
                    raise SourceFetchFailed(f"redirect without Location header ({current})")
                current = str(response.next_request.url) if response.next_request else location
                # Each hop is validated in full; an allowed host may not
                # redirect us off the allowlist.
                try:
                    host = check_url(current)
                except SourceRefused as exc:
                    log_attempt(
                        url=current,
                        host=host_of(current),
                        outcome="refused",
                        reason=f"redirect from {url}: {exc}",
                    )
                    raise
                continue
            break
        else:
            raise SourceFetchFailed(f"too many redirects (> {MAX_REDIRECTS}) starting at {url}")

        if response.status_code != 200:
            raise SourceFetchFailed(f"HTTP {response.status_code} for {current}")

        body = response.content
        if len(body) > MAX_BYTES:
            raise SourceFetchFailed(f"response exceeds {MAX_BYTES} bytes ({current})")
    except SourceRefused:
        raise
    except (httpx.HTTPError, SourceFetchFailed) as exc:
        log_attempt(url=url, host=host, outcome="failed", reason=str(exc))
        raise SourceFetchFailed(str(exc)) from exc
    finally:
        if owns_client:
            client.close()

    digest = hashlib.sha256(body).hexdigest()
    path = store_original(body=body, host=host, sha256=digest)
    log_attempt(url=current, host=host, outcome="allowed", sha256=digest)

    return FetchedSource(
        url=current,
        host=host,
        sha256=digest,
        content_type=response.headers.get("content-type", ""),
        fetched_at=datetime.now(UTC),
        path=path,
        body=body,
    )


def store_original(*, body: bytes, host: str, sha256: str) -> Path:
    """Write the original bytes under ``sources/`` and return the path.

    Content-addressed, so the same bytes are stored once and never rewritten.
    """
    directory = settings().sources_dir / host
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{sha256}.bin"
    if not path.exists():
        path.write_bytes(body)
    return path
