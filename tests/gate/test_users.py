from datetime import UTC, datetime, timedelta

import pytest

pytestmark = pytest.mark.integration

from michael.db import writable  # noqa: E402
from michael.gate import users  # noqa: E402
from michael.gate.ratelimit import WINDOW, is_locked_out  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_gate_tables(gate_tables: None) -> None:
    """Delegates to the shared `gate_tables` fixture (tests/gate/conftest.py)
    so every test in this file still gets a schema-applied, truncated
    database without each one having to request `gate_tables` by name."""


def test_creates_and_finds_a_user() -> None:
    users.create_user("reader@example.com", "A Reader", "a-long-enough-password", "chat")
    found = users.find_by_email("reader@example.com")
    assert found is not None
    assert found.display_name == "A Reader"
    assert found.role == "chat"


def test_does_not_store_the_password_in_clear() -> None:
    users.create_user("clear@example.com", "X", "a-long-enough-password", "chat")
    found = users.find_by_email("clear@example.com")
    assert found is not None
    assert "a-long-enough-password" not in found.password_hash


def test_email_is_matched_case_insensitively() -> None:
    users.create_user("Mixed@Example.com", "X", "a-long-enough-password", "chat")
    assert users.find_by_email("mixed@example.com") is not None


def test_unknown_email_is_none_not_an_error() -> None:
    assert users.find_by_email("nobody@example.com") is None


def test_disable_marks_the_user_and_is_reported() -> None:
    users.create_user("gone@example.com", "X", "a-long-enough-password", "chat")
    assert users.disable_user("gone@example.com") is True
    found = users.find_by_email("gone@example.com")
    assert found is not None and found.disabled_at is not None


def test_disabling_an_unknown_user_returns_false() -> None:
    assert users.disable_user("nobody@example.com") is False


def test_rejects_an_unknown_role() -> None:
    with pytest.raises(ValueError, match="role must be"):
        users.create_user("bad@example.com", "X", "a-long-enough-password", "superuser")


def test_attempts_are_recorded_and_read_back_per_account_and_address() -> None:
    users.record_attempt("attempts@example.com", "203.0.113.5", "bad_password")
    account, address = users.recent_attempts("attempts@example.com", "203.0.113.5")
    assert len(account) == 1 and len(address) == 1
    assert account[0].outcome == "bad_password"


def test_find_by_id_returns_the_matching_user() -> None:
    """I5: relay() re-checks disabled_at at WebSocket accept via find_by_id,
    keyed by the id carried in the session cookie (not the email)."""
    created = users.create_user("byid@example.com", "By Id", "a-long-enough-password", "chat")
    found = users.find_by_id(created.id)
    assert found is not None
    assert found.email == "byid@example.com"


def test_find_by_id_is_none_for_an_unknown_id() -> None:
    assert users.find_by_id(999_999) is None


def test_set_password_changes_the_stored_hash_and_reports_success() -> None:
    """I1: the store-level half of `michael user password`."""
    created = users.create_user("reset@example.com", "X", "a-long-enough-password", "chat")
    old_hash = created.password_hash

    assert users.set_password("reset@example.com", "a-different-long-password") is True

    found = users.find_by_email("reset@example.com")
    assert found is not None
    assert found.password_hash != old_hash
    assert "a-different-long-password" not in found.password_hash


def test_set_password_on_an_unknown_account_returns_false() -> None:
    assert users.set_password("nobody@example.com", "a-long-enough-password") is False


# --------------------------------------------------------------------------
# C4(b): the runbook's cutover precondition names two ways recent_attempts /
# is_locked_out could be silently wrong against a real Postgres server that
# the pre-existing tests could not have caught:
#   - the timedelta -> interval adaptation matching too MUCH (e.g. all rows,
#     if the sign or type came out wrong) -- the only prior test inserted a
#     row at now() and asserted it came back, which cannot detect this.
#   - is_locked_out never being exercised against the real query at all
#     (test_ratelimit.py only exercises the arithmetic over in-memory
#     Attempt objects it constructs itself).
# These CANNOT be run in this environment (no reachable Postgres); see
# .superpowers/sdd/2026-09-23-michael-gate/final-fix-report.md for the
# injection results that stand in for having actually run them.
# --------------------------------------------------------------------------


def test_recent_attempts_excludes_an_attempt_older_than_the_window() -> None:
    stale_at = datetime.now(UTC) - WINDOW - timedelta(minutes=1)
    with writable() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO gate.login_attempts (account_key, address, at, outcome) "
            "VALUES (lower(%s), %s, %s, %s)",
            ("stale@example.com", "203.0.113.7", stale_at, "bad_password"),
        )

    account, address = users.recent_attempts("stale@example.com", "203.0.113.7")

    assert account == []
    assert address == []


def test_five_in_window_failures_lock_out_through_the_real_query() -> None:
    for _ in range(5):
        users.record_attempt("locked@example.com", "203.0.113.8", "bad_password")

    account, address = users.recent_attempts("locked@example.com", "203.0.113.8")

    assert is_locked_out(account, address, now=datetime.now(UTC))
