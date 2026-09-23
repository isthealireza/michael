"""Login rate limiting.

A pure decision over already-loaded attempts, so the thresholds are tested by
arithmetic rather than against a database.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

ACCOUNT_LIMIT = 5
ADDRESS_LIMIT = 20
WINDOW = timedelta(minutes=15)

#: A success is not evidence of an attack. Counting it would let an active
#: reader lock themselves out by using the service normally.
FAILURE_OUTCOMES = frozenset({"bad_password", "no_such_user"})


@dataclass(frozen=True)
class Attempt:
    at: datetime
    outcome: str


def _recent_failures(attempts: Sequence[Attempt], now: datetime) -> int:
    cutoff = now - WINDOW
    return sum(1 for a in attempts if a.outcome in FAILURE_OUTCOMES and a.at > cutoff)


def is_locked_out(
    account_attempts: Sequence[Attempt],
    address_attempts: Sequence[Attempt],
    *,
    now: datetime,
) -> bool:
    """True when this login must be refused without checking the password."""
    return (
        _recent_failures(account_attempts, now) >= ACCOUNT_LIMIT
        or _recent_failures(address_attempts, now) >= ADDRESS_LIMIT
    )
