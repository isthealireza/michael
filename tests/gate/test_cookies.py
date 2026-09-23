import base64
import hmac
import json
from datetime import UTC, datetime, timedelta
from hashlib import sha256

from michael.gate.cookies import LIFETIME, CookiePayload, mint, verify

SECRET = "a" * 64
NOW = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)


def test_round_trips() -> None:
    payload = verify(mint(7, "chat", secret=SECRET, now=NOW), secret=SECRET, now=NOW)
    assert payload == CookiePayload(user_id=7, role="chat", expires_at=NOW + LIFETIME)


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
        base64.urlsafe_b64encode(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode())
        .decode("ascii")
        .rstrip("=")
    )
    assert forged_body != body
    assert verify(f"{forged_body}.{signature}", secret=SECRET, now=NOW) is None


def test_garbage_is_rejected_without_raising() -> None:
    for junk in ["", ".", "no-dot", "a.b.c", "!!!.???"]:
        assert verify(junk, secret=SECRET, now=NOW) is None


def test_overflow_in_expiry_claim_is_rejected() -> None:
    """OverflowError from absurd timestamps must not raise—return None instead.

    Regression test for uncaught OverflowError in verify(): float(10**400) and
    datetime.fromtimestamp(1e300) both raise OverflowError, which was not caught.
    """

    # Create a forged token with an absurd exp value that will pass signature
    # check but raise OverflowError when parsed. We build it the same way mint()
    # does so the HMAC check passes.
    def _b64encode(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    def _sign(body: str, secret: str) -> str:
        return _b64encode(hmac.new(secret.encode(), body.encode(), sha256).digest())

    claims = {"uid": 7, "role": "chat", "exp": 10**400}
    body = _b64encode(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode())
    signature = _sign(body, SECRET)
    token = f"{body}.{signature}"
    assert verify(token, secret=SECRET, now=NOW) is None
