"""I8: `MICHAEL_DATABASE_URL` is required by every gate route that touches an
account or a session (michael.config.settings() raises if it is absent), but
it was in neither the runbook's "four variables" nor entrypoint.sh's `:?`
guards. Without it the container starts cleanly, serves /login, and 500s on
the first real login attempt.

The static check always runs. The subprocess check additionally proves the
script actually exits non-zero before reaching `exec uvicorn`, but is skipped
where `sh` is not on PATH (e.g. some non-POSIX CI shells) rather than failing
the whole suite over an environment gap unrelated to the fix.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ENTRYPOINT = Path(__file__).resolve().parents[2] / "gate" / "entrypoint.sh"

_OTHER_REQUIRED_VARS = {
    "GATE_SECRET": "s" * 64,
    "HERMES_BASE_URL": "http://hermes.invalid",
    "HERMES_USERNAME": "michael",
    "HERMES_PASSWORD": "irrelevant",
}


def test_entrypoint_guards_michael_database_url() -> None:
    text = ENTRYPOINT.read_text(encoding="utf-8")
    assert "MICHAEL_DATABASE_URL:?" in text


@pytest.mark.skipif(shutil.which("sh") is None, reason="no POSIX sh on PATH")
def test_entrypoint_exits_nonzero_without_michael_database_url() -> None:
    env = {**os.environ, **_OTHER_REQUIRED_VARS}
    env.pop("MICHAEL_DATABASE_URL", None)

    result = subprocess.run(
        ["sh", str(ENTRYPOINT)],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode != 0
    assert "MICHAEL_DATABASE_URL" in result.stderr


@pytest.mark.skipif(shutil.which("sh") is None, reason="no POSIX sh on PATH")
def test_entrypoint_exits_nonzero_without_gate_secret() -> None:
    """Sanity check that the harness above actually discriminates: the
    pre-existing GATE_SECRET guard must still fire the same way."""
    env = {**os.environ, **_OTHER_REQUIRED_VARS, "MICHAEL_DATABASE_URL": "postgresql://x/y"}
    env.pop("GATE_SECRET", None)

    result = subprocess.run(
        ["sh", str(ENTRYPOINT)],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode != 0
    assert "GATE_SECRET" in result.stderr
