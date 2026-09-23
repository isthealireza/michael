import pytest

from michael.gate.allowlist import ALLOWED_METHODS, decide

OWNED = frozenset({"sess-mine"})


def test_the_allowlist_is_exactly_the_four_methods() -> None:
    assert ALLOWED_METHODS == frozenset(
        {"session.create", "session.resume", "session.status", "prompt.submit"}
    )


def test_session_create_is_allowed_and_owns_nothing_yet() -> None:
    d = decide({"id": "c", "method": "session.create", "params": {}}, owned_session_ids=OWNED)
    assert d.allowed and d.session_id is None and d.reason == "ok"


@pytest.mark.parametrize("method", ["session.resume", "session.status", "prompt.submit"])
def test_owned_session_is_allowed(method: str) -> None:
    frame = {"id": "x", "method": method, "params": {"session_id": "sess-mine"}}
    d = decide(frame, owned_session_ids=OWNED)
    assert d.allowed and d.reason == "ok" and d.session_id == "sess-mine"


@pytest.mark.parametrize("method", ["session.resume", "session.status", "prompt.submit"])
def test_another_users_session_is_refused(method: str) -> None:
    """Without this, per-user accounts are cosmetic: a session id would be the
    only thing between one reader and another reader's legal questions."""
    frame = {"id": "x", "method": method, "params": {"session_id": "sess-theirs"}}
    d = decide(frame, owned_session_ids=OWNED)
    assert not d.allowed and d.reason == "not_your_session"


@pytest.mark.parametrize(
    "method",
    [
        "session.list",
        "session.delete",
        "tools.call",
        "file.read",
        "cron.create",
        "",
        "prompt",
        "Session.Create",
        "session.create ",
        "SESSION.CREATE",
        "ｓession.create",
    ],
)
def test_every_other_method_is_refused(method: str) -> None:
    frame = {"id": "x", "method": method, "params": {"session_id": "sess-mine"}}
    d = decide(frame, owned_session_ids=OWNED)
    assert not d.allowed and d.reason == "method_not_allowed"


def test_a_method_bearing_session_without_a_session_id_is_refused() -> None:
    d = decide({"id": "x", "method": "prompt.submit", "params": {}}, owned_session_ids=OWNED)
    assert not d.allowed and d.reason == "missing_session_id"


@pytest.mark.parametrize(
    "frame",
    [
        None,
        [],
        "text",
        7,
        {"params": {}},
        {"method": 7},
        [
            {"method": "session.create", "params": {}},
            {"method": "session.list", "params": {}},
        ],
    ],
)
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


def test_empty_string_session_id_is_refused() -> None:
    """The empty-string guard prevents bypassing ownership check."""
    d = decide({"method": "prompt.submit", "params": {"session_id": ""}}, owned_session_ids=OWNED)
    assert not d.allowed and d.reason == "missing_session_id"


# CRITICAL 1: owned session cannot chaperone unowned one
def test_prompt_submit_with_extra_unowned_session_is_refused() -> None:
    """An owned session_id carrying an unowned stored_session_id must be refused.
    The gateway forwards the original frame, so stored_session_id would reach
    Hermes still naming a session the caller does not own."""
    d = decide(
        {
            "method": "prompt.submit",
            "params": {"session_id": "sess-mine", "stored_session_id": "sess-theirs"},
        },
        owned_session_ids=OWNED,
    )
    assert not d.allowed and d.reason == "malformed_frame"


# CRITICAL 1: each method rejects unknown keys
@pytest.mark.parametrize("method", ["session.resume", "session.status"])
def test_session_methods_with_extra_keys_are_refused(method: str) -> None:
    d = decide(
        {"method": method, "params": {"session_id": "sess-mine", "extra": "key"}},
        owned_session_ids=OWNED,
    )
    assert not d.allowed and d.reason == "malformed_frame"


# CRITICAL 2: session.create with unknown keys is refused
def test_session_create_with_extra_keys_is_refused() -> None:
    d = decide(
        {"method": "session.create", "params": {"title": "t", "session_id": "sess-theirs"}},
        owned_session_ids=OWNED,
    )
    assert not d.allowed and d.reason == "malformed_frame"


# CRITICAL 2: session.create cannot adopt someone else's session
def test_session_create_with_extra_session_id_key_is_refused() -> None:
    """This is the one branch that skips the ownership check, so an off-protocol
    session_id there is an adopt-someone-elses-session shape."""
    d = decide(
        {"method": "session.create", "params": {"session_id": "sess-theirs"}},
        owned_session_ids=OWNED,
    )
    assert not d.allowed and d.reason == "malformed_frame"


def test_prompt_submit_with_extra_keys_is_refused() -> None:
    d = decide(
        {
            "method": "prompt.submit",
            "params": {"session_id": "sess-mine", "text": "hello", "extra": "key"},
        },
        owned_session_ids=OWNED,
    )
    assert not d.allowed and d.reason == "malformed_frame"


# I-3: top-level frame keys are validated too, not just params keys
def test_an_unknown_top_level_key_is_refused() -> None:
    """The real client sends exactly {id, method, params}. decide() used to
    inspect only method and params, so an extra top-level key rode along
    unexamined and was relayed to Hermes verbatim -- e.g. a "context" key
    smuggling data the params allowlist was never asked to check."""
    frame = {
        "id": "x",
        "method": "session.create",
        "params": {},
        "context": {"anything": "at all"},
    }
    d = decide(frame, owned_session_ids=OWNED)
    assert not d.allowed and d.reason == "malformed_frame"


def test_the_three_ordinary_top_level_keys_are_still_allowed() -> None:
    frame = {"id": "x", "method": "session.create", "params": {"title": "t"}}
    d = decide(frame, owned_session_ids=OWNED)
    assert d.allowed and d.reason == "ok"


def test_a_frame_with_no_id_is_still_allowed() -> None:
    frame = {"method": "session.create", "params": {}}
    d = decide(frame, owned_session_ids=OWNED)
    assert d.allowed and d.reason == "ok"


# M-3: cold-start case with empty owned_session_ids
def test_prompt_submit_with_empty_owned_session_ids_is_refused() -> None:
    """A valid-format request is refused when the user owns no sessions."""
    d = decide(
        {"method": "prompt.submit", "params": {"session_id": "sess-mine"}},
        owned_session_ids=frozenset(),
    )
    assert not d.allowed and d.reason == "not_your_session"
