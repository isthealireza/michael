"""CLI behaviour around gate account management.

I1: `michael user add` on a duplicate email used to let
`psycopg.errors.UniqueViolation` escape as a raw traceback, because only
`ValueError` was caught. And there was no password-reset command at all,
though the spec says reset happens "via CLI". Both are covered here by
monkeypatching `michael.gate.users` so no database is needed.
"""

from __future__ import annotations

import getpass as getpass_module

import psycopg
import pytest

from michael import cli
from michael.gate import users as gate_users


def _fixed_getpass(monkeypatch: pytest.MonkeyPatch, value: str = "a-long-enough-password") -> None:
    monkeypatch.setattr(getpass_module, "getpass", lambda prompt="": value)


def test_user_add_duplicate_email_prints_a_friendly_message_not_a_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _fixed_getpass(monkeypatch)

    def _boom(email: str, display_name: str, password: str, role: str) -> gate_users.User:
        raise psycopg.errors.UniqueViolation("duplicate key value violates unique constraint")

    monkeypatch.setattr(gate_users, "create_user", _boom)

    code = cli.main(["user", "add", "dup@example.com", "--name", "Dup", "--role", "chat"])

    assert code == 1
    assert "already exists" in capsys.readouterr().err


def test_user_password_resets_an_existing_account(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _fixed_getpass(monkeypatch)
    monkeypatch.setattr(gate_users, "set_password", lambda email, password: True)

    code = cli.main(["user", "password", "reader@example.com"])

    assert code == 0
    assert "reader@example.com" in capsys.readouterr().out


def test_user_password_on_an_unknown_account_reports_it_and_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _fixed_getpass(monkeypatch)
    monkeypatch.setattr(gate_users, "set_password", lambda email, password: False)

    code = cli.main(["user", "password", "nobody@example.com"])

    assert code == 1
    assert "no such account" in capsys.readouterr().err


def test_user_password_requires_the_two_entries_to_match(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    values = iter(["password-one-long", "password-two-long"])
    monkeypatch.setattr(getpass_module, "getpass", lambda prompt="": next(values))
    called = False

    def _fail_if_called(email: str, password: str) -> bool:
        nonlocal called
        called = True
        return True

    monkeypatch.setattr(gate_users, "set_password", _fail_if_called)

    code = cli.main(["user", "password", "reader@example.com"])

    assert code == 1
    assert not called
    assert "did not match" in capsys.readouterr().err
