import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from michael.gate.app import build_app
from michael.gate.upstream import UpstreamConfig

UPSTREAM = UpstreamConfig(base_url="http://upstream.invalid", username="u", password="p")


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    (tmp_path / "michael.html").write_text("<html>michael</html>", encoding="utf-8")
    (tmp_path / "michael.js").write_text("// chat client script\n", encoding="utf-8")
    return TestClient(build_app(secret="s" * 64, upstream=UPSTREAM, web_root=tmp_path))


def test_the_chat_page_requires_a_session(client: TestClient) -> None:
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_the_login_page_is_public(client: TestClient) -> None:
    assert client.get("/login").status_code == 200


def test_the_websocket_refuses_an_unauthenticated_client(client: TestClient) -> None:
    with pytest.raises(WebSocketDisconnect) as caught:
        with client.websocket_connect("/api/ws"):
            pass
    assert caught.value.code == 4401


def test_a_bad_login_does_not_reveal_whether_the_account_exists(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Enumerating valid addresses must not be possible from the response.

    _authenticate is monkeypatched to None here rather than exercised for
    real: the real function reaches Postgres via user_store.recent_attempts,
    and this suite (unlike Task 6's integration tests) must run with no
    database available. This test only proves the route's response shape does
    not distinguish failure causes; the real credential path is covered by
    Task 6.
    """
    import michael.gate.app as app_module

    monkeypatch.setattr(app_module, "_authenticate", lambda email, password, address: None)
    absent = client.post(
        "/auth/login", json={"email": "nobody@x.com", "password": "wrong-password"}
    )
    assert absent.status_code == 401
    assert absent.json() == {"detail": "invalid email or password"}


def test_the_ws_ticket_endpoint_is_not_proxied(client: TestClient) -> None:
    """The gate fetches its own ticket. Exposing this would hand a client a
    credential-backed handle on the gateway."""
    assert client.post("/api/auth/ws-ticket", json={}).status_code == 404


@pytest.mark.parametrize("path", ["/settings", "/api/sessions", "/assets/index.js", "/health/../"])
def test_no_other_dashboard_route_is_reachable(client: TestClient, path: str) -> None:
    assert client.get(path, follow_redirects=False).status_code in (303, 404)


def test_session_cookie_is_hardened(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    import michael.gate.app as app_module

    monkeypatch.setattr(app_module, "_authenticate", lambda email, password, address: (1, "chat"))
    response = client.post(
        "/auth/login", json={"email": "reader@x.com", "password": "a-long-enough-password"}
    )
    assert response.status_code == 204
    cookie = response.headers["set-cookie"]
    # Compared case-insensitively throughout: Starlette's exact attribute
    # casing (e.g. "HttpOnly" vs "httponly") is not contractual, so pinning the
    # assertion to one particular casing would be depending on an
    # implementation detail rather than the constraint itself.
    lowered = cookie.lower()
    assert "httponly" in lowered
    assert "secure" in lowered
    assert "samesite=strict" in lowered


def test_static_mount_serves_files_by_path(client: TestClient) -> None:
    assert client.get("/static/michael.html").status_code == 200


def test_signed_in_request_for_michael_js_succeeds(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """web/michael.html references michael.js with a page-relative path, so a
    browser requests it at the site root, not under /static. It must resolve
    or the chat page renders blank with a console 404."""
    import michael.gate.app as app_module

    monkeypatch.setattr(app_module, "_authenticate", lambda email, password, address: (1, "chat"))
    login_response = client.post(
        "/auth/login", json={"email": "reader@x.com", "password": "a-long-enough-password"}
    )
    assert login_response.status_code == 204

    assert client.get("/michael.js").status_code == 200


def test_unauthenticated_root_still_redirects_once_michael_js_is_reachable(
    tmp_path: Path,
) -> None:
    """Making /michael.js reachable must not accidentally make '/' public too
    (e.g. by placing a catch-all static mount ahead of the guarded route)."""
    (tmp_path / "michael.html").write_text("<html>michael</html>", encoding="utf-8")
    (tmp_path / "michael.js").write_text("// chat client script\n", encoding="utf-8")
    anon = TestClient(build_app(secret="s" * 64, upstream=UPSTREAM, web_root=tmp_path))

    assert anon.get("/michael.js").status_code == 200

    redirect = anon.get("/", follow_redirects=False)
    assert redirect.status_code == 303
    assert redirect.headers["location"] == "/login"


def test_claim_new_session_claims_both_session_identifiers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """session.create returns a live session_id AND a stored_session_id, and
    session.resume later sends the *stored* id under the parameter name
    session_id (web/michael.js lines ~95-120). Claiming only the live id
    would make decide() refuse every resume as "not_your_session"."""
    import michael.gate.app as app_module

    claimed: list[tuple[int, str, str]] = []
    monkeypatch.setattr(
        app_module.session_store,
        "claim",
        lambda user_id, hermes_session_id, title: claimed.append(
            (user_id, hermes_session_id, title)
        ),
    )
    frame = json.dumps(
        {"result": {"session_id": "live-1", "stored_session_id": "stored-1", "title": "t"}}
    )

    app_module._claim_new_session(frame, 42)

    assert claimed == [(42, "live-1", "t"), (42, "stored-1", "t")]


def test_claim_new_session_ignores_empty_or_missing_identifiers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import michael.gate.app as app_module

    claimed: list[tuple[int, str, str]] = []
    monkeypatch.setattr(
        app_module.session_store,
        "claim",
        lambda user_id, hermes_session_id, title: claimed.append(
            (user_id, hermes_session_id, title)
        ),
    )
    frame = json.dumps({"result": {"session_id": "", "stored_session_id": None, "title": "t"}})

    app_module._claim_new_session(frame, 42)

    assert claimed == []
