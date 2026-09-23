import pytest

pytestmark = pytest.mark.integration

from michael.gate import sessions, users  # noqa: E402


@pytest.fixture
def two_users(gate_tables: None) -> tuple[int, int]:
    a = users.create_user("owner-a@example.com", "A", "a-long-enough-password", "chat")
    b = users.create_user("owner-b@example.com", "B", "a-long-enough-password", "chat")
    return a.id, b.id


def test_claimed_session_is_owned(two_users: tuple[int, int]) -> None:
    a, _ = two_users
    sessions.claim(a, "sess-1", "MICHAEL web — 23 Sep")
    assert "sess-1" in sessions.owned_by(a)


def test_one_users_session_is_not_owned_by_another(two_users: tuple[int, int]) -> None:
    a, b = two_users
    sessions.claim(a, "sess-1", "t")
    assert "sess-1" not in sessions.owned_by(b)


def test_owned_by_is_empty_for_a_user_with_no_sessions(two_users: tuple[int, int]) -> None:
    _, b = two_users
    assert sessions.owned_by(b) == frozenset()


def test_claiming_the_same_session_twice_is_idempotent(two_users: tuple[int, int]) -> None:
    a, _ = two_users
    sessions.claim(a, "sess-dup", "t")
    sessions.claim(a, "sess-dup", "t")
    assert len(sessions.list_for_user(a)) == 1


def test_a_session_cannot_be_stolen_by_a_second_claim(two_users: tuple[int, int]) -> None:
    """Ownership is first-writer-wins; a later claim must not reassign it."""
    a, b = two_users
    sessions.claim(a, "sess-contested", "t")
    sessions.claim(b, "sess-contested", "t")
    assert "sess-contested" in sessions.owned_by(a)
    assert "sess-contested" not in sessions.owned_by(b)


def test_claiming_both_identifiers_makes_both_owned(two_users: tuple[int, int]) -> None:
    """web/michael.js: session.create yields a live session_id AND a
    stored_session_id, and session.resume later sends the stored id under the
    ``session_id`` parameter. A caller must claim both identifiers for one
    conversation; this proves owned_by() then recognises either of them.
    """
    a, _ = two_users
    sessions.claim(a, "sess-live", "t")
    sessions.claim(a, "sess-stored", "t")
    owned = sessions.owned_by(a)
    assert "sess-live" in owned
    assert "sess-stored" in owned
