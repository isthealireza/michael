"""Render Hermes config inside the container, from the environment.

On a developer machine `hermes/render_config.py` reads ../.env and writes
hermes/config.yaml before the image is built. On Railway there is no such step:
the container starts with secrets in its environment and nothing on disk. This
module renders the same template at boot instead, so both paths use one source
of truth and the deployed config cannot drift from the local one.

It also copies MICHAEL.md over SOUL.md on every boot. The prompt is baked into
the image, so the persona cannot lag behind the prompt under test.
"""

from __future__ import annotations

import hashlib
import os
import pathlib
import re
import shutil
import sys
from collections.abc import Mapping

TEMPLATE = pathlib.Path("/opt/michael/hermes/config.template.yaml")
PROMPT = pathlib.Path("/opt/michael/MICHAEL.md")
DATA = pathlib.Path("/opt/data")

PLACEHOLDER = re.compile(r"\$\{(\w+)\}")


class RenderError(RuntimeError):
    """A placeholder had no value. Never render a half-populated config."""


def database_urls(env: Mapping[str, str]) -> tuple[str, str]:
    """Return the read/write and read-only URLs.

    Prefers explicit MICHAEL_* values. Falls back to Railway's injected
    DATABASE_URL, which is the private URL: Railway's database templates ship
    with no TCP proxy, so this never reaches the public internet.
    """
    write = env.get("MICHAEL_DATABASE_URL", "").strip()
    read = env.get("MICHAEL_RO_DATABASE_URL", "").strip()
    injected = env.get("DATABASE_URL", "").strip()

    if not write:
        if not injected:
            raise RenderError(
                "no database URL: set MICHAEL_DATABASE_URL, or attach a Postgres "
                "service so Railway injects DATABASE_URL"
            )
        write = injected
    # Without a separate read-only role the answering path would hold write
    # access. Fall back, but say so loudly rather than silently downgrading.
    if not read:
        read = write
    return write, read


def render(template: str, env: Mapping[str, str]) -> str:
    """Substitute ${VAR} from ``env``. Raise if any placeholder is unset."""
    write, read = database_urls(env)
    values = dict(env)
    values["MICHAEL_DATABASE_URL"] = write
    values["MICHAEL_RO_DATABASE_URL"] = read

    missing: list[str] = []

    def substitute(match: re.Match[str]) -> str:
        name = match.group(1)
        value = values.get(name, "")
        if not value:
            missing.append(name)
            return ""
        return value

    out = PLACEHOLDER.sub(substitute, template)
    if missing:
        raise RenderError(f"unset environment variables: {sorted(set(missing))}")
    return out


def main() -> int:
    body = TEMPLATE.read_text(encoding="utf-8")
    if body.startswith("# TEMPLATE"):
        body = body.split("\n", 3)[3]

    try:
        config = render(body, os.environ)
    except RenderError as exc:
        print(f"config render failed: {exc}", file=sys.stderr)
        return 1

    DATA.mkdir(parents=True, exist_ok=True)
    target = DATA / "config.yaml"
    target.write_text(config, encoding="utf-8")
    target.chmod(0o600)

    shutil.copyfile(PROMPT, DATA / "SOUL.md")
    digest = hashlib.sha256(PROMPT.read_bytes()).hexdigest()

    read_only = os.environ.get("MICHAEL_RO_DATABASE_URL", "").strip()
    print(f"rendered {target} ({len(config)} bytes)")
    print(f"SOUL.md from MICHAEL.md (sha256 {digest[:16]}...)")
    print(f"database host: {database_urls(os.environ)[0].rsplit('@', 1)[-1]}")
    if not read_only:
        print(
            "WARNING: MICHAEL_RO_DATABASE_URL is unset, so the answering path "
            "is using the read/write role. Create michael_ro and set it.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
