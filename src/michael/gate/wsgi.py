"""Uvicorn entry point. Reads the environment, builds the app."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from starlette.applications import Starlette

from michael.gate.app import build_app
from michael.gate.upstream import UpstreamConfig


def app() -> Starlette:
    # T9: without this, michael.gate.app's logger.info/warning calls have no
    # configured handler and INFO records are dropped by the root logger's
    # default level — there would be no record of who signed in when, exactly
    # the gap the review flagged. basicConfig is a no-op if something else in
    # the process already configured logging, so this is safe to call here.
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    return build_app(
        secret=os.environ["GATE_SECRET"],
        upstream=UpstreamConfig(
            base_url=os.environ["HERMES_BASE_URL"],
            username=os.environ["HERMES_USERNAME"],
            password=os.environ["HERMES_PASSWORD"],
        ),
        web_root=Path(os.environ.get("GATE_WEB_ROOT", "/opt/michael/web")),
    )
