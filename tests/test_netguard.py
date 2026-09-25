"""The boundary guard, held to its own standard.

Each test here crosses the line for real and expects to be stopped, rather
than asserting on the guard's internals. The one that checks a marker OPENS a
boundary undoes the guard it was given first, so it can prove the permissive
direction as well as the forbidding one - a guard that refuses everything
would pass every test but the last.
"""

from __future__ import annotations

import socket

import psycopg
import pytest

from tests import netguard


def test_an_unmarked_test_cannot_open_a_database_connection() -> None:
    """The defect this exists for: a default-suite test reaching Postgres."""
    with pytest.raises(netguard.CrossedTheLine, match="pytest.mark.integration"):
        psycopg.connect("postgresql://michael_ro@127.0.0.1:5433/michael_test")


def test_an_unmarked_test_cannot_resolve_a_host_it_does_not_own() -> None:
    with pytest.raises(netguard.CrossedTheLine, match="pytest.mark.network"):
        socket.getaddrinfo("openrouter.ai", 443)


def test_an_unmarked_test_cannot_connect_to_an_address_off_this_machine() -> None:
    """Resolution is not the only way out: a bare IP skips getaddrinfo."""
    sock = socket.socket()
    try:
        with pytest.raises(netguard.CrossedTheLine, match="pytest.mark.network"):
            sock.connect(("93.184.216.34", 80))
    finally:
        sock.close()


def test_loopback_stays_open() -> None:
    """Not politeness - a requirement.

    On Windows asyncio builds its self-pipe from a connected socket pair on
    127.0.0.1, so closing loopback here would fail every test that uses
    starlette's TestClient for reasons unrelated to this boundary. The
    database is what is worth catching on loopback, and psycopg.connect
    catches it above.
    """
    assert socket.getaddrinfo("127.0.0.1", 0)


def test_the_markers_decide_and_nothing_else() -> None:
    assert netguard.may_use_database({"integration"})
    assert not netguard.may_use_database({"network"})
    assert not netguard.may_use_database(set())

    assert netguard.may_use_network({"network"})
    assert not netguard.may_use_network({"integration"})
    assert not netguard.may_use_network(set())


def test_a_marker_opens_the_boundary_it_names_and_only_that_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A guard that refuses everything would pass every test above.

    ``monkeypatch`` is one instance per test, shared with the autouse fixture
    that installed the guard, so undoing it here hands back the real functions
    to guard again under different permissions.
    """
    monkeypatch.undo()
    real_connect = psycopg.connect

    netguard.install(monkeypatch, database=True, network=False)

    assert psycopg.connect is real_connect, "database=True must leave psycopg alone"
    with pytest.raises(netguard.CrossedTheLine):
        socket.getaddrinfo("openrouter.ai", 443)
