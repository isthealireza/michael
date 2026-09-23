"""Which gate user owns which Hermes session.

Hermes session identifiers are the only handle a client has on a conversation.
Without an ownership record, one reader holding another's identifier can resume
their conversation, so this table is what makes per-user accounts mean anything.

A NOTE FOR CALLERS — two identifiers, not one:
``web/michael.js`` shows that ``session.create`` returns both a live
``session_id`` and a ``stored_session_id``, and that ``session.resume`` later
sends the *stored* id under the parameter name ``session_id``. A user therefore
legitimately acts on two different identifiers for the same conversation. This
module's ``claim`` records exactly one identifier per call (matching the task
brief's signature); the caller (session creation, Task 9) is responsible for
calling ``claim`` once for each of the two identifiers it receives. If only the
live id is claimed, every resume of a reclaimed session will be refused as
"not your session".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from michael.db import writable


@dataclass(frozen=True)
class OwnedSession:
    hermes_session_id: str
    title: str
    created_at: datetime


def claim(user_id: int, hermes_session_id: str, title: str) -> None:
    """Record ownership. First writer wins; a later claim never reassigns."""
    with writable() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO gate.user_sessions (user_id, hermes_session_id, title) "
            "VALUES (%s, %s, %s) ON CONFLICT (hermes_session_id) DO NOTHING",
            (user_id, hermes_session_id, title),
        )


def owned_by(user_id: int) -> frozenset[str]:
    """Every session identifier this user may act on."""
    with writable() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT hermes_session_id FROM gate.user_sessions WHERE user_id = %s", (user_id,)
        )
        return frozenset(str(row["hermes_session_id"]) for row in cur.fetchall())


def list_for_user(user_id: int) -> list[OwnedSession]:
    with writable() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT hermes_session_id, title, created_at FROM gate.user_sessions "
            "WHERE user_id = %s ORDER BY created_at DESC",
            (user_id,),
        )
        return [
            OwnedSession(
                hermes_session_id=str(row["hermes_session_id"]),
                title=str(row["title"]),
                created_at=row["created_at"],
            )
            for row in cur.fetchall()
        ]
