"""What a chat user may send over /api/ws.

`web/michael.js` records that prompt.submit on this socket is "the single
server-side choke point every dashboard submit passes through". Allowing the
PATH therefore allows every method the dashboard uses, session enumeration
included. The gate reads each frame and permits four methods and no others.

Refusal is the default: anything not positively recognised is refused.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

_NEEDS_OWNED_SESSION = frozenset({"session.resume", "session.status", "prompt.submit"})

#: Per-method params key allowlist. Unknown keys are refused.
_ALLOWED_PARAMS_KEYS = {
    "session.create": frozenset({"title"}),
    "session.resume": frozenset({"session_id"}),
    "session.status": frozenset({"session_id"}),
    "prompt.submit": frozenset({"session_id", "text"}),
}

#: session.create opens a conversation. The other three act on one that already
#: exists, and so must be checked against ownership. Derived from
#: `_ALLOWED_PARAMS_KEYS` rather than listed separately: `decide()` subscripts
#: `_ALLOWED_PARAMS_KEYS[method]` unguarded once `method in ALLOWED_METHODS` is
#: true, so the two sets must never drift apart. Deriving one from the other
#: makes that impossible instead of merely intended.
ALLOWED_METHODS = frozenset(_ALLOWED_PARAMS_KEYS)

#: The only top-level keys a client frame may carry. `web/michael.js` sends
#: exactly `{id, method, params}` (`id` optional in principle, always present
#: in practice). `decide()` used to inspect only `method` and `params`, so an
#: extra top-level key rode along unexamined and was relayed verbatim upstream.
_ALLOWED_TOP_LEVEL_KEYS = frozenset({"id", "method", "params"})


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: Literal[
        "ok",
        "malformed_frame",
        "method_not_allowed",
        "missing_session_id",
        "not_your_session",
    ]
    session_id: str | None = None


def decide(frame: object, *, owned_session_ids: frozenset[str]) -> Decision:
    """Decide whether one client frame may be relayed upstream."""
    if not isinstance(frame, dict):
        return Decision(False, "malformed_frame")

    if not set(frame.keys()).issubset(_ALLOWED_TOP_LEVEL_KEYS):
        return Decision(False, "malformed_frame")

    method = frame.get("method")
    if not isinstance(method, str):
        return Decision(False, "malformed_frame")

    params = frame.get("params", {})
    if not isinstance(params, dict):
        return Decision(False, "malformed_frame")

    if method not in ALLOWED_METHODS:
        return Decision(False, "method_not_allowed")

    # Check that params contains only allowed keys
    allowed_keys = _ALLOWED_PARAMS_KEYS[method]
    if not set(params.keys()).issubset(allowed_keys):
        return Decision(False, "malformed_frame")

    if method not in _NEEDS_OWNED_SESSION:
        return Decision(True, "ok")

    session_id = params.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        return Decision(False, "missing_session_id")

    if session_id not in owned_session_ids:
        return Decision(False, "not_your_session")

    return Decision(True, "ok", session_id=session_id)
