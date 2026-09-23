import pytest

pytestmark = pytest.mark.integration

from michael.gate import users  # noqa: E402
from michael.gate.schema import apply_gate_schema  # noqa: E402


@pytest.fixture(autouse=True)
def gate_schema() -> None:
    apply_gate_schema()


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
