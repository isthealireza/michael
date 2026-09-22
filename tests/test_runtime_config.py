"""The container-side config renderer.

A silent failure here is the dangerous one: a half-rendered config could drop
the disabled-toolset list, the dashboard password or the read-only database
role, and the service would still start.
"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest

SPEC = importlib.util.spec_from_file_location(
    "render_runtime_config",
    pathlib.Path(__file__).resolve().parents[1] / "hermes" / "render_runtime_config.py",
)
assert SPEC and SPEC.loader
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)

TEMPLATE = pathlib.Path(__file__).resolve().parents[1] / "hermes" / "config.template.yaml"

FULL_ENV = {
    "EMBEDDING_API_KEY": "sk-test-embedding",
    "MICHAEL_DATABASE_URL": "postgresql://michael:pw@postgres.railway.internal:5432/railway",
    "MICHAEL_RO_DATABASE_URL": "postgresql://michael_ro:pw@postgres.railway.internal:5432/railway",
    "DASHBOARD_USERNAME": "michael",
    "DASHBOARD_PASSWORD_HASH": "scrypt$16384$8$1$abc",
}


def body() -> str:
    text = TEMPLATE.read_text(encoding="utf-8")
    return text.split("\n", 3)[3] if text.startswith("# TEMPLATE") else text


def test_a_full_environment_renders_with_no_placeholders_left() -> None:
    out = mod.render(body(), FULL_ENV)
    assert "${" not in out


def test_an_unset_variable_fails_loudly_rather_than_rendering_blank() -> None:
    incomplete = {k: v for k, v in FULL_ENV.items() if k != "DASHBOARD_PASSWORD_HASH"}
    with pytest.raises(mod.RenderError, match="DASHBOARD_PASSWORD_HASH"):
        mod.render(body(), incomplete)


def test_an_empty_value_counts_as_unset() -> None:
    env = {**FULL_ENV, "EMBEDDING_API_KEY": ""}
    with pytest.raises(mod.RenderError, match="EMBEDDING_API_KEY"):
        mod.render(body(), env)


def test_railway_database_url_is_used_when_michael_url_is_absent() -> None:
    env = {k: v for k, v in FULL_ENV.items() if not k.startswith("MICHAEL_")}
    env["DATABASE_URL"] = "postgresql://postgres:pw@postgres.railway.internal:5432/railway"
    write, read = mod.database_urls(env)
    assert write == env["DATABASE_URL"]
    # No separate role configured: it must fall back, not invent one.
    assert read == write


def test_no_database_url_at_all_is_an_error() -> None:
    env = {k: v for k, v in FULL_ENV.items() if not k.startswith("MICHAEL_")}
    with pytest.raises(mod.RenderError, match="no database URL"):
        mod.database_urls(env)


def test_the_rendered_config_keeps_every_guarantee() -> None:
    import yaml

    out = yaml.safe_load(mod.render(body(), FULL_ENV))

    # Write/execute toolsets stay disabled.
    disabled = set(out["agent"]["disabled_toolsets"])
    assert {"terminal", "file", "code_execution", "computer_use"} <= disabled
    # Indirect routes back to execution stay disabled too.
    assert {"skills", "cronjob", "delegation"} <= disabled
    # Michael's MCP server is present and apply_schema stays excluded.
    michael = out["mcp_servers"]["michael"]
    assert michael["command"] == "/opt/michael/.venv/bin/python"
    assert "apply_schema" in michael["tools"]["exclude"]
    # The answering path gets the read-only role.
    assert "michael_ro" in michael["env"]["MICHAEL_RO_DATABASE_URL"]
    # The dashboard is password protected.
    assert out["dashboard"]["basic_auth"]["password_hash"]
    # The benchmarked model is pinned.
    assert out["model"]["default"] == "deepseek/deepseek-v4-pro"


def test_the_agent_profile_grants_no_write_tool() -> None:
    """The incident this guards against.

    A deployed agent called ingest_source_url mid-answer, pulled a
    headings-only page into the corpus, and cited it in the same turn. The
    read-only database role could not stop it: ingestion runs under the
    read/write URL by design. So the write tools must not reach the agent at
    all, and --allow-writes must not be passed on the answering path.
    """
    import yaml

    out = yaml.safe_load(mod.render(body(), FULL_ENV))
    michael = out["mcp_servers"]["michael"]

    assert "--allow-writes" not in michael["args"], (
        "the answering path must not enable Michael's writing tools"
    )

    granted = set(michael["tools"]["include"])
    assert granted == {
        "classify_request",
        "search_provisions",
        "draft_document",
        "validate_output",
    }

    writers = {"ingest_source_url", "ingest_local_file", "seed_corpus", "apply_schema"}
    assert not (granted & writers), f"write tools granted: {sorted(granted & writers)}"
    assert writers <= set(michael["tools"]["exclude"]), "write tools must also be excluded by name"


def test_michael_still_refuses_writers_without_allow_writes() -> None:
    """Defence in depth: the dispatcher refuses even if the transport slips."""
    from michael import tools

    for name in ("ingest_source_url", "ingest_local_file", "seed_corpus", "apply_schema"):
        with pytest.raises(PermissionError):
            tools.dispatch(name, {})
