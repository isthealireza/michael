"""Database connections.

Two entry points, deliberately not interchangeable:

* :func:`writable` — ingestion only.
* :func:`readonly`  — the answering path (retrieval, drafting).

:func:`readonly` connects as ``michael_ro``, a role created with
``default_transaction_read_only = on`` and SELECT-only grants, and additionally
sets the transaction read-only on the session. A write attempted on the
answering path fails in Postgres, not merely in a code review.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from psycopg import Connection
from psycopg.rows import DictRow, dict_row

from michael.config import settings

#: Connecting must not hang. A refused ingestion, in particular, has to be
#: reported to the operator whether or not the database is reachable.
CONNECT_TIMEOUT_SECONDS = 10


@contextmanager
def writable(*, connect_timeout: int = CONNECT_TIMEOUT_SECONDS) -> Iterator[Connection[DictRow]]:
    """A read/write connection. Ingestion only.

    The transaction commits on clean exit and rolls back on any exception, so a
    partially-ingested document never lands.
    """
    with psycopg.connect(
        settings().database_url, row_factory=dict_row, connect_timeout=connect_timeout
    ) as conn:
        yield conn


@contextmanager
def readonly() -> Iterator[Connection[DictRow]]:
    """A read-only connection for the answering path."""
    with psycopg.connect(
        settings().readonly_database_url,
        row_factory=dict_row,
        connect_timeout=CONNECT_TIMEOUT_SECONDS,
    ) as conn:
        conn.read_only = True
        yield conn


def vector_literal(values: list[float]) -> str:
    """Render an embedding as a pgvector literal.

    Kept explicit rather than pulling in an adapter package: the cast site in
    SQL is always ``%s::vector``, so the representation stays visible.
    """
    return "[" + ",".join(f"{v:.8f}" for v in values) + "]"
