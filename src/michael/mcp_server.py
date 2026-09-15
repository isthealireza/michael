"""Expose Michael's tools over MCP, so any agent runtime can attach.

Hermes remains the orchestrator. This module is transport only: it registers
the tool definitions from :mod:`michael.tools` and forwards calls to
:func:`michael.tools.dispatch`. It contains no planning and no model call.

Run with::

    uv run --extra mcp python -m michael.mcp_server

By default only the answering tools are served. Pass ``--allow-writes`` to also
serve the ingestion tools; ingestion is normally run from the CLI instead.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

from michael import tools
from michael.cli import load_dotenv


def _serve(allow_writes: bool) -> None:
    try:
        from mcp.server import Server
        from mcp.server.stdio import stdio_server
        from mcp.types import TextContent, Tool
    except ImportError as exc:  # pragma: no cover - optional extra
        raise SystemExit("The MCP server needs the 'mcp' extra: uv sync --extra mcp") from exc

    definitions = tools.TOOL_SCHEMAS if allow_writes else tools.ANSWERING_TOOLS
    server: Any = Server("michael")

    @server.list_tools()  # type: ignore[untyped-decorator]
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name=d["name"],
                description=d["description"],
                inputSchema=d["input_schema"],
            )
            for d in definitions
        ]

    @server.call_tool()  # type: ignore[untyped-decorator]
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        result = tools.dispatch(name, arguments, allow_writes=allow_writes)
        text = (
            result
            if isinstance(result, str)
            else json.dumps(result, indent=2, ensure_ascii=False, default=str)
        )
        return [TextContent(type="text", text=text)]

    async def run() -> None:
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())

    asyncio.run(run())


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(prog="michael-mcp", description=__doc__)
    parser.add_argument(
        "--allow-writes",
        action="store_true",
        help="also serve the ingestion tools, which write to the database",
    )
    args = parser.parse_args(argv)
    _serve(args.allow_writes)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
