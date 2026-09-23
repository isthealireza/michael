import base64
import json
from datetime import UTC, datetime, timedelta

from michael.gate.cookies import LIFETIME, CookiePayload, mint, verify

SECRET = "a" * 64
NOW = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)


def test_round_trips() -> None:
    payload = verify(mint(7, "chat", secret=SECRET, now=NOW), secret=SECRET, now=NOW)
    assert payload == CookiePayload(user_id=7, role="chat",
                                    expires_at=NOW + LIFETIME)


def test_lifetime_is_twelve_hours() -> None:
    assert LIFETIME == timedelta(hours=12)


def test_expired_token_is_rejected() -> None:
    token = mint(7, "chat", secret=SECRET, now=NOW)
    assert verify(token, secret=SECRET, now=NOW + LIFETIME + timedelta(seconds=1)) is None


def test_token_valid_one_second_before_expiry() -> None:
    token = mint(7, "chat", secret=SECRET, now=NOW)
    assert verify(token, secret=SECRET, now=NOW + LIFETIME - timedelta(seconds=1)) is not None


def test_a_different_secret_is_rejected() -> None:
    token = mint(7, "chat", secret=SECRET, now=NOW)
    assert verify(token, secret="b" * 64, now=NOW) is None


def test_tampering_with_the_role_is_rejected() -> None:
    """Privilege escalation by cookie edit is the attack this signature stops."""
    token = mint(7, "chat", secret=SECRET, now=NOW)
    body, _, signature = token.partition(".")
    claims = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    # Guard: if these two assertions ever stop holding, the "tampering" below has
    # silently become a no-op and the test would pass without proving anything.
    assert claims["role"] == "chat"
    claims["role"] = "admin"
    forged_body = (
        base64.urlsafe_b64encode(
            json.dumps(claims, separators=(",", ":"), sort_keys=True).encode()
        )
        .decode("ascii")
        .rstrip("=")
    )
    assert forged_body != body
    assert verify(f"{forged_body}.{signature}", secret=SECRET, now=NOW) is None


def test_garbage_is_rejected_without_raising() -> None:
    for junk in ["", ".", "no-dot", "a.b.c", "!!!.???"]:
        assert verify(junk, secret=SECRET, now=NOW) is None
