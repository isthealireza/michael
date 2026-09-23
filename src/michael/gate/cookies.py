"""Signed session cookies.

The cookie carries the claim; the signature is the only thing that makes it
true. `now` is always a parameter so expiry is tested by arithmetic rather than
by sleeping.
"""

from __future__ import annotations

import base64
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256

COOKIE_NAME = "michael_session"

#: Not renewed on activity: a stolen cookie has a bounded life, and a reader
#: signing in once a day is an acceptable cost for that.
LIFETIME = timedelta(hours=12)


@dataclass(frozen=True)
class CookiePayload:
    user_id: int
    role: str
    expires_at: datetime


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(body: str, secret: str) -> str:
    return _b64encode(hmac.new(secret.encode(), body.encode(), sha256).digest())


def mint(user_id: int, role: str, *, secret: str, now: datetime) -> str:
    """Return a signed token asserting this user and role until expiry."""
    expires_at = now + LIFETIME
    body = _b64encode(
        json.dumps(
            {"uid": user_id, "role": role, "exp": expires_at.timestamp()},
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    )
    return f"{body}.{_sign(body, secret)}"


def verify(token: str, *, secret: str, now: datetime) -> CookiePayload | None:
    """Return the payload when the token is authentic and unexpired, else None.

    Every failure mode returns None. A caller must not be able to tell a forged
    signature from a malformed body from an expired token.
    """
    body, separator, signature = token.partition(".")
    if not separator or not signature:
        return None
    if not hmac.compare_digest(signature, _sign(body, secret)):
        return None
    try:
        claims = json.loads(_b64decode(body))
        expires_at = datetime.fromtimestamp(float(claims["exp"]), tz=UTC)
        user_id = int(claims["uid"])
        role = str(claims["role"])
    except (ValueError, KeyError, TypeError, OverflowError):
        return None
    if now >= expires_at:
        return None
    return CookiePayload(user_id=user_id, role=role, expires_at=expires_at)
