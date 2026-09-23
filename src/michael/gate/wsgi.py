"""Uvicorn entry point. Reads the environment, builds the app."""

from __future__ import annotations

import os
from pathlib import Path

from starlette.applications import Starlette

from michael.gate.app import build_app
from michael.gate.upstream import UpstreamConfig


def app() -> Starlette:
    return build_app(
        secret=os.environ["GATE_SECRET"],
        upstream=UpstreamConfig(
            base_url=os.environ["HERMES_BASE_URL"],
            username=os.environ["HERMES_USERNAME"],
            password=os.environ["HERMES_PASSWORD"],
        ),
        web_root=Path(os.environ.get("GATE_WEB_ROOT", "/opt/michael/web")),
    )
