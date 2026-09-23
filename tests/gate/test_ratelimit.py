from datetime import UTC, datetime, timedelta

from michael.gate.ratelimit import (
    ACCOUNT_LIMIT,
    ADDRESS_LIMIT,
    WINDOW,
    Attempt,
    is_locked_out,
)

NOW = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)


def failures(count: int, *, ago: timedelta = timedelta(seconds=1)) -> list[Attempt]:
    return [Attempt(at=NOW - ago, outcome="bad_password") for _ in range(count)]


def test_limits_match_the_spec() -> None:
    assert (ACCOUNT_LIMIT, ADDRESS_LIMIT, WINDOW) == (5, 20, timedelta(minutes=15))


def test_allows_below_the_account_limit() -> None:
    assert not is_locked_out(failures(4), [], now=NOW)


def test_locks_out_at_the_account_limit() -> None:
    assert is_locked_out(failures(5), [], now=NOW)


def test_locks_out_at_the_address_limit() -> None:
    assert is_locked_out([], failures(20), now=NOW)


def test_failures_outside_the_window_do_not_count() -> None:
    old = failures(10, ago=WINDOW + timedelta(seconds=1))
    assert not is_locked_out(old, old, now=NOW)


def test_successful_logins_do_not_count_toward_the_limit() -> None:
    """Otherwise an active reader locks themselves out by using the service."""
    ok = [Attempt(at=NOW, outcome="ok") for _ in range(50)]
    assert not is_locked_out(ok, ok, now=NOW)


def test_unknown_account_failures_still_count() -> None:
    """Enumerating addresses must cost the attacker the same as guessing."""
    probes = [Attempt(at=NOW, outcome="no_such_user") for _ in range(20)]
    assert is_locked_out([], probes, now=NOW)


def test_unknown_outcome_counts_as_failure() -> None:
    """New failure outcomes must count by default, not be silently ignored.

    This is a fail-safe default: if a new outcome is added (e.g. "mfa_failed")
    without updating the deny-list, it still counts toward lockout.
    """
    unknown = [Attempt(at=NOW, outcome="mfa_failed") for _ in range(5)]
    assert is_locked_out(unknown, [], now=NOW)
