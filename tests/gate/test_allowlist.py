import pytest

from michael.gate.allowlist import ALLOWED_METHODS, decide

OWNED = frozenset({"sess-mine"})


def test_the_allowlist_is_exactly_the_four_methods() -> None:
    assert ALLOWED_METHODS == frozenset(
        {"session.create", "session.resume", "session.status", "prompt.submit"}
    )


def test_session_create_is_allowed_and_owns_nothing_yet() -> None:
    d = decide({"id": "c", "method": "session.create", "params": {}}, owned_session_ids=OWNED)
    assert d.allowed and d.session_id is None


@pytest.mark.parametrize("method", ["session.resume", "session.status", "prompt.submit"])
def test_owned_session_is_allowed(method: str) -> None:
    frame = {"id": "x", "method": method, "params": {"session_id": "sess-mine"}}
    assert decide(frame, owned_session_ids=OWNED).allowed


@pytest.mark.parametrize("method", ["session.resume", "session.status", "prompt.submit"])
def test_another_users_session_is_refused(method: str) -> None:
    """Without this, per-user accounts are cosmetic: a session id would be the
    only thing between one reader and another reader's legal questions."""
    frame = {"id": "x", "method": method, "params": {"session_id": "sess-theirs"}}
    d = decide(frame, owned_session_ids=OWNED)
    assert not d.allowed and d.reason == "not_your_session"


@pytest.mark.parametrize(
    "method",
    ["session.list", "session.delete", "tools.call", "file.read", "cron.create", "", "prompt"],
)
def test_every_other_method_is_refused(method: str) -> None:
    frame = {"id": "x", "method": method, "params": {"session_id": "sess-mine"}}
    d = decide(frame, owned_session_ids=OWNED)
    assert not d.allowed and d.reason == "method_not_allowed"


def test_a_method_bearing_session_without_a_session_id_is_refused() -> None:
    d = decide({"id": "x", "method": "prompt.submit", "params": {}}, owned_session_ids=OWNED)
    assert not d.allowed and d.reason == "missing_session_id"


@pytest.mark.parametrize("frame", [None, [], "text", 7, {"params": {}}, {"method": 7}])
def test_a_frame_that_is_not_a_well_formed_call_is_refused(frame: object) -> None:
    """Refuse by default. An unparseable frame must never reach the gateway."""
    d = decide(frame, owned_session_ids=OWNED)
    assert not d.allowed and d.reason == "malformed_frame"


def test_params_that_are_not_an_object_are_refused() -> None:
    d = decide({"method": "prompt.submit", "params": []}, owned_session_ids=OWNED)
    assert not d.allowed and d.reason == "malformed_frame"


def test_a_non_string_session_id_is_refused() -> None:
    frame = {"method": "prompt.submit", "params": {"session_id": {"$ne": None}}}
    d = decide(frame, owned_session_ids=OWNED)
    assert not d.allowed and d.reason == "missing_session_id"
