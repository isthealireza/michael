"""Enforce this suite's boundary instead of describing it.

tests/conftest.py has always said that no test reaches the network or a
database unless it is marked ``integration`` or ``network``. Nothing enforced
it, so the claim drifted:
``test_status_never_reaches_for_a_writable_connection`` opened a real
connection to Postgres from the default suite for as long as it existed, and
passed everywhere only because a database happened to be running - the
developer's container locally, the service container in CI. Run where one is
not, it failed on a password mismatch, which is a mis-marked test reporting
itself rather than a defect in the code.

A test that reaches out is also invisible while the thing it reaches is up.
It announces itself as a hang, at the worst possible moment, in the run you
least want to lose.

Two boundaries, guarded at the two layers where they are actually crossed:

* the database, at ``psycopg.connect`` - the one call both ``readonly`` and
  ``writable`` funnel through, so it catches a caller whatever name it
  imported them under, which patching ``michael.db`` would not;
* the network, at the socket - refusing to resolve or connect to anything
  that is not loopback.

Loopback stays open at the socket layer deliberately. On Windows asyncio
builds its self-pipe from a connected socket pair on 127.0.0.1, so blocking
loopback would fail every test using starlette's TestClient for reasons that
have nothing to do with this boundary. On loopback the database is the thing
worth catching, and ``psycopg.connect`` catches it exactly.
"""

from __future__ import annotations

import socket
from collections.abc import Iterable
from typing import Any

#: Names that mean "this machine". A test may talk to itself.
LOOPBACK = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0", ""})


class CrossedTheLine(RuntimeError):
    """A test went somewhere its own markers do not allow."""


def may_use_database(markers: Iterable[str]) -> bool:
    """Only an integration test may open a database connection."""
    return "integration" in set(markers)


def may_use_network(markers: Iterable[str]) -> bool:
    """Only a network test may leave this machine."""
    return "network" in set(markers)


def _is_loopback(host: object) -> bool:
    return str(host) in LOOPBACK


def install(monkeypatch: Any, *, database: bool, network: bool) -> None:
    """Close whichever boundaries this test has not earned the right to cross.

    Everything is installed through ``monkeypatch`` so it comes back down with
    the test, including when the test fails.
    """
    if not database:
        import psycopg

        def _no_database(*args: object, **kwargs: object) -> object:
            raise CrossedTheLine(
                "this test opened a database connection. Mark it "
                "@pytest.mark.integration if it needs one, or stub the read "
                "path the way test_a_missing_ledger_reads_as_nothing_applied"
                "_not_as_an_error does."
            )

        monkeypatch.setattr(psycopg, "connect", _no_database)

    if not network:
        real_getaddrinfo = socket.getaddrinfo
        real_connect = socket.socket.connect

        def _no_resolving(host: Any, port: Any, *args: Any, **kwargs: Any) -> Any:
            if _is_loopback(host):
                return real_getaddrinfo(host, port, *args, **kwargs)
            raise CrossedTheLine(
                f"this test resolved {host!r}. Mark it @pytest.mark.network if "
                "it must reach an allowlisted host, or give it an "
                "httpx.MockTransport."
            )

        def _no_connecting(self: Any, address: Any) -> Any:
            host = address[0] if isinstance(address, tuple) else address
            if _is_loopback(host):
                return real_connect(self, address)
            raise CrossedTheLine(
                f"this test connected to {host!r}. Mark it @pytest.mark.network "
                "if it must reach an allowlisted host, or give it an "
                "httpx.MockTransport."
            )

        monkeypatch.setattr(socket, "getaddrinfo", _no_resolving)
        monkeypatch.setattr(socket.socket, "connect", _no_connecting)
