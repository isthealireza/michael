"""Password hashing for gate accounts.

argon2id, not the scrypt used for `dashboard.basic_auth`. That hash is
provisioned once by an operator; these are chosen by users and reachable by an
attacker, which is a different threat and deserves a memory-hard KDF.
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error, InvalidHashError

#: Short passwords are the single largest contributor to a guessable account,
#: and this gate is on the open internet. Enforced at hash time so no code path
#: can store one.
MINIMUM_LENGTH = 12

_HASHER = PasswordHasher()


def hash_password(plain: str) -> str:
    """Hash a new password. Raises ValueError if it is too short."""
    if len(plain) < MINIMUM_LENGTH:
        raise ValueError(f"a password must be at least {MINIMUM_LENGTH} characters")
    return _HASHER.hash(plain)


def verify_password(plain: str, stored: str) -> bool:
    """True when ``plain`` matches ``stored``.

    A malformed stored hash is a failed login, not an exception: a corrupted
    row must not take the service down or leak its state through a 500.
    """
    try:
        return _HASHER.verify(stored, plain)
    except (Argon2Error, InvalidHashError):
        return False
