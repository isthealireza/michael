import pytest

from michael.gate.passwords import hash_password, verify_password


def test_round_trips() -> None:
    assert verify_password("correct horse battery staple",
                           hash_password("correct horse battery staple"))


def test_rejects_the_wrong_password() -> None:
    assert not verify_password("wrong-password-x",
                               hash_password("right-password-x"))


def test_uses_argon2id_not_scrypt() -> None:
    """Operator-provisioned hashes may be scrypt; user-chosen ones may not."""
    assert hash_password("a-long-enough-password").startswith("$argon2id$")


def test_salts_so_two_identical_passwords_differ() -> None:
    assert hash_password("a-long-enough-password") != hash_password("a-long-enough-password")


def test_a_malformed_stored_hash_is_false_not_an_exception() -> None:
    """A corrupted row must fail the login, not 500 the service."""
    assert not verify_password("anything", "not-a-hash")


@pytest.mark.parametrize("bad", ["", " ", "short"])
def test_refuses_to_hash_a_too_short_password(bad: str) -> None:
    with pytest.raises(ValueError, match="at least 12 characters"):
        hash_password(bad)
