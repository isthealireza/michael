"""The account store.

Thin adapter over Postgres. Every decision this module could make lives in
passwords.py or ratelimit.py instead, so the rules are unit-tested without a
database and this file stays readable as plain SQL.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from michael.db import writable
from michael.gate.passwords import hash_password
from michael.gate.ratelimit import WINDOW, Attempt

ROLES = ("admin", "chat")


@dataclass(frozen=True)
class User:
    id: int
    email: str
    display_name: str
    password_hash: str
    role: str
    disabled_at: datetime | None


_COLUMNS = "id, email, display_name, password_hash, role, disabled_at"


def _row_to_user(row: dict[str, object]) -> User:
    return User(
        id=int(row["id"]),  # type: ignore[call-overload]
        email=str(row["email"]),
        display_name=str(row["display_name"]),
        password_hash=str(row["password_hash"]),
        role=str(row["role"]),
        disabled_at=row["disabled_at"],  # type: ignore[arg-type]
    )


def create_user(email: str, display_name: str, password: str, role: str) -> User:
    """Create an account. Raises ValueError on an unknown role or short password."""
    if role not in ROLES:
        raise ValueError(f"role must be one of {ROLES}, not {role!r}")
    password_hash = hash_password(password)
    with writable() as conn, conn.cursor() as cur:
        cur.execute(
            f"INSERT INTO gate.users (email, display_name, password_hash, role) "
            f"VALUES (lower(%s), %s, %s, %s) RETURNING {_COLUMNS}",
            (email, display_name, password_hash, role),
        )
        row = cur.fetchone()
    assert row is not None
    return _row_to_user(row)


def find_by_email(email: str) -> User | None:
    with writable() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT {_COLUMNS} FROM gate.users WHERE email = lower(%s)", (email,))
        row = cur.fetchone()
    return _row_to_user(row) if row else None


def list_users() -> list[User]:
    with writable() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT {_COLUMNS} FROM gate.users ORDER BY email")
        return [_row_to_user(row) for row in cur.fetchall()]


def disable_user(email: str) -> bool:
    """Mark the account disabled. False when there was no such account."""
    with writable() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE gate.users SET disabled_at = now() "
            "WHERE email = lower(%s) AND disabled_at IS NULL",
            (email,),
        )
        return cur.rowcount > 0


def record_attempt(account_key: str, address: str, outcome: str) -> None:
    with writable() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO gate.login_attempts (account_key, address, outcome) "
            "VALUES (lower(%s), %s, %s)",
            (account_key, address, outcome),
        )


def recent_attempts(account_key: str, address: str) -> tuple[list[Attempt], list[Attempt]]:
    """Attempts inside the rate-limit window, as (for this account, for this address)."""
    with writable() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT account_key, address, at, outcome FROM gate.login_attempts "
            "WHERE at > now() - %s AND (account_key = lower(%s) OR address = %s)",
            (WINDOW, account_key, address),
        )
        rows = cur.fetchall()
    account = [
        Attempt(at=r["at"], outcome=str(r["outcome"]))
        for r in rows
        if str(r["account_key"]) == account_key.lower()
    ]
    by_address = [
        Attempt(at=r["at"], outcome=str(r["outcome"]))
        for r in rows
        if str(r["address"]) == address
    ]
    return account, by_address
