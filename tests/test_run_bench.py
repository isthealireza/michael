"""Tests for bench/run_bench.py's provider-pinning helpers.

bench/ is a set of scripts, not a package under src/, so it is reached the
same way bench/analyse.py reaches ``michael``: by putting its directory on
sys.path before importing it. Nothing here touches Docker, Hermes, or the
network - render_pinned_config() is pure text in/out, and provider_pin_home()
only touches a tmp_path fixture, never the real /opt/data/config.yaml.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

BENCH_DIR = str(Path(__file__).resolve().parents[1] / "bench")
if BENCH_DIR not in sys.path:
    sys.path.insert(0, BENCH_DIR)

import run_bench  # noqa: E402

BASE_CONFIG_YAML = """\
model:
  default: deepseek/deepseek-v4-pro
mcp_servers:
  michael:
    command: /opt/michael/.venv/bin/python
agent:
  max_turns: 16
  disabled_toolsets:
    - terminal
    - file
"""


def test_render_pinned_config_adds_only_list_for_the_named_model() -> None:
    out = run_bench.render_pinned_config(BASE_CONFIG_YAML, "anthropic/claude-sonnet-5", "anthropic")
    config = yaml.safe_load(out)
    assert config["provider_routing"]["models"]["anthropic/claude-sonnet-5"] == {
        "only": ["anthropic"]
    }


def test_render_pinned_config_leaves_the_rest_of_the_config_untouched() -> None:
    out = run_bench.render_pinned_config(BASE_CONFIG_YAML, "anthropic/claude-sonnet-5", "anthropic")
    config = yaml.safe_load(out)
    assert config["model"] == {"default": "deepseek/deepseek-v4-pro"}
    assert config["mcp_servers"]["michael"]["command"] == "/opt/michael/.venv/bin/python"
    assert config["agent"]["disabled_toolsets"] == ["terminal", "file"]


def test_render_pinned_config_preserves_an_existing_pin_for_another_model() -> None:
    base = run_bench.render_pinned_config(BASE_CONFIG_YAML, "deepseek/deepseek-v4-pro", "gmicloud")
    out = run_bench.render_pinned_config(base, "anthropic/claude-sonnet-5", "anthropic")
    models = yaml.safe_load(out)["provider_routing"]["models"]
    assert models["deepseek/deepseek-v4-pro"] == {"only": ["gmicloud"]}
    assert models["anthropic/claude-sonnet-5"] == {"only": ["anthropic"]}


def test_render_pinned_config_rejects_a_non_mapping_base() -> None:
    with pytest.raises(ValueError):
        run_bench.render_pinned_config("- just\n- a\n- list\n", "some/model", "anthropic")


def test_provider_pin_home_is_none_when_the_model_is_not_pinned(tmp_path: Path) -> None:
    base_config = tmp_path / "config.yaml"
    base_config.write_text(BASE_CONFIG_YAML, encoding="utf-8")
    result = run_bench.provider_pin_home(
        "anthropic/claude-sonnet-5", pins={}, base_config=base_config
    )
    assert result is None


def test_provider_pin_home_writes_an_overlay_without_touching_the_base_file(
    tmp_path: Path,
) -> None:
    base_config = tmp_path / "config.yaml"
    base_config.write_text(BASE_CONFIG_YAML, encoding="utf-8")

    overlay = run_bench.provider_pin_home(
        "anthropic/claude-sonnet-5",
        pins={"anthropic/claude-sonnet-5": "anthropic"},
        base_config=base_config,
    )

    assert overlay is not None
    written = yaml.safe_load((overlay / "config.yaml").read_text(encoding="utf-8"))
    assert written["provider_routing"]["models"]["anthropic/claude-sonnet-5"] == {
        "only": ["anthropic"]
    }
    # The real, container-rendered config is read, never written.
    assert base_config.read_text(encoding="utf-8") == BASE_CONFIG_YAML


def test_run_slug_is_unchanged_when_unpinned() -> None:
    assert run_bench.run_slug("deepseek/deepseek-v4-pro", None, "contract", 1) == (
        "deepseek_deepseek-v4-pro__contract__1"
    )


def test_run_slug_carries_the_provider_when_pinned() -> None:
    assert run_bench.run_slug("anthropic/claude-sonnet-5", "anthropic", "uncovered", 2) == (
        "anthropic_claude-sonnet-5__anthropic__uncovered__2"
    )
