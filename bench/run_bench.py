"""Benchmark runner. Executes inside the Hermes container.

Actual cost comes from diffing this API key's cumulative OpenRouter usage
around each run, because the --usage-file figure is marked "estimated" and is
computed from the models API rather than billed. The estimate is recorded too,
so the two can be compared and billing lag spotted.

Runs are strictly sequential: a concurrent run would corrupt the usage diff.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import time
import urllib.request

import yaml

OUT = pathlib.Path("/opt/data/bench")
RUNS = 3
TIMEOUT = 900
SETTLE = 6  # seconds to let OpenRouter usage catch up before reading it back

# The config Hermes actually runs with in this container (rendered from
# hermes/config.template.yaml at boot; see hermes/render_runtime_config.py).
# provider_pin_home() reads this as the base for a pinned overlay - it is
# never written to.
BASE_CONFIG = pathlib.Path("/opt/data/config.yaml")

MODELS = [
    "anthropic/claude-opus-5",
    "anthropic/claude-sonnet-5",
    "anthropic/claude-haiku-4.5",
    "deepseek/deepseek-v4-pro",
    "deepseek/deepseek-v4-flash",
    "qwen/qwen3.7-flash",
    "openai/gpt-oss-120b",
]

# Pin a shortlisted model to a specific OpenRouter provider slug here (see
# `openrouter.ai/api/v1/providers` for slugs, e.g. "anthropic", "gmicloud")
# to re-benchmark it with the exact routing a production decision would use -
# see docs/decisions/model-for-client-data.md. Empty by default: every model
# keeps today's unpinned, multi-provider OpenRouter routing.
PROVIDER_PINS: dict[str, str] = {}

PROMPTS = {
    "contract": (
        "I need a casual employment contract between Company X and Mr Y, with conditions A and B"
    ),
    # Migration law: outside every domain in domains.yaml, and nothing in the
    # corpus reaches the threshold for it. The previous prompt asked about
    # eligible data breaches under the Privacy Act, which WAS uncovered when
    # the benchmark was written. The Privacy Act has since been ingested, so
    # that prompt scored 0.7332 and every model answered it correctly - and the
    # uncovered arm silently stopped testing NOT COVERED for 21 of 21 runs.
    # check_uncovered_is_uncovered() below exists so that cannot recur quietly.
    "uncovered": (
        "What labour market testing must an employer complete before "
        "nominating an employee for a skilled visa?"
    ),
}


def check_uncovered_is_uncovered() -> None:
    """Refuse to run if the uncovered prompt is no longer uncovered.

    The uncovered arm tests one thing: that a model replies NOT COVERED
    instead of answering from its own knowledge. Once the corpus covers the
    topic, answering is the correct behaviour and the arm proves nothing. It
    fails open - the runs still pass - so nothing catches it but this.
    """
    from michael import tools

    result = tools.search_provisions(PROMPTS["uncovered"])
    if result["covered"]:
        raise SystemExit(
            "the uncovered prompt is covered by the corpus "
            f"(best_score {result.get('best_score')}); the uncovered arm would "
            "prove nothing. Choose a new topic and record its score here."
        )


def key_usage() -> float:
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/key",
        headers={"Authorization": "Bearer " + os.environ["OPENROUTER_API_KEY"]},
    )
    with urllib.request.urlopen(req, timeout=40) as response:
        return float(json.load(response)["data"]["usage"])


def run_slug(model: str, provider: str | None, prompt_name: str, run: int) -> str:
    """The filename stem for one run.

    A pinned run gets the provider in its own slug, so it lands beside the
    unpinned baseline for the same model rather than overwriting or being
    silently deduplicated against it.
    """
    base = model.replace("/", "_")
    if provider:
        base = f"{base}__{provider}"
    return f"{base}__{prompt_name}__{run}"


def render_pinned_config(base_config_yaml: str, model: str, provider: str) -> str:
    """Return `base_config_yaml` with an OpenRouter provider pin for `model`.

    Pure text in, text out - no filesystem, no Hermes, no Docker - so this is
    the part unit tests exercise directly. Everything else in the base config
    (the model default, the MCP server block, disabled toolsets, ...) is
    carried through unchanged, so a pinned run stays otherwise identical to
    production. This mirrors Hermes' own `provider_routing.models.<id>.only`
    key (see `hermes-agent.nousresearch.com/docs/user-guide/features/provider-routing`);
    it does not touch `hermes/config.template.yaml`.
    """
    config = yaml.safe_load(base_config_yaml) or {}
    if not isinstance(config, dict):
        raise ValueError("base config did not parse to a mapping")
    routing = config.setdefault("provider_routing", {})
    models = routing.setdefault("models", {})
    models[model] = {"only": [provider]}
    return yaml.safe_dump(config, sort_keys=False)


def provider_pin_home(
    model: str, pins: dict[str, str] | None = None, base_config: pathlib.Path = BASE_CONFIG
) -> pathlib.Path | None:
    """Build a HERMES_HOME overlay that pins `model`, or ``None`` if unpinned.

    Hermes resolves its config from ``${HERMES_HOME}/config.yaml``, so
    pointing HERMES_HOME at a fresh directory for one subprocess call is
    enough to pin that call's routing; the container's own config.yaml (and
    every other model's routing) is read, never written. The process still
    inherits OPENROUTER_API_KEY and everything else from its normal
    environment - only the config file location changes.

    The caller is responsible for removing the returned directory once the
    subprocess has finished with it.
    """
    pins = PROVIDER_PINS if pins is None else pins
    provider = pins.get(model)
    if not provider:
        return None
    base_text = base_config.read_text(encoding="utf-8")
    pinned = render_pinned_config(base_text, model, provider)
    overlay = pathlib.Path(tempfile.mkdtemp(prefix="michael-bench-provider-"))
    (overlay / "config.yaml").write_text(pinned, encoding="utf-8")
    return overlay


def main() -> int:
    check_uncovered_is_uncovered()
    OUT.mkdir(parents=True, exist_ok=True)
    results = OUT / "results.jsonl"
    done = set()
    if results.exists():
        for line in results.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                done.add((r["model"], r.get("provider"), r["prompt"], r["run"]))

    for model in MODELS:
        provider = PROVIDER_PINS.get(model)
        for prompt_name, prompt in PROMPTS.items():
            for run in range(1, RUNS + 1):
                if (model, provider, prompt_name, run) in done:
                    continue
                slug = run_slug(model, provider, prompt_name, run)
                usage_file = OUT / f"{slug}.usage.json"
                pin_dir = provider_pin_home(model)
                run_env = {**os.environ, "HERMES_HOME": str(pin_dir)} if pin_dir else None
                before = key_usage()
                started = time.time()
                try:
                    proc = subprocess.run(
                        # --provider openrouter is mandatory: Hermes routes some
                        # model ids elsewhere (sonnet-5 resolves to 'gmi'), which
                        # fails with no credentials and would also put the spend
                        # outside the key whose usage is being measured. Pinning
                        # a specific upstream (PROVIDER_PINS) narrows routing
                        # further, within OpenRouter, via HERMES_HOME above.
                        [
                            "hermes",
                            "-z",
                            prompt,
                            "-m",
                            model,
                            "--provider",
                            "openrouter",
                            "--usage-file",
                            str(usage_file),
                        ],
                        capture_output=True,
                        text=True,
                        timeout=TIMEOUT,
                        env=run_env,
                    )
                    stdout, rc, stderr = proc.stdout, proc.returncode, proc.stderr
                except subprocess.TimeoutExpired:
                    stdout, rc, stderr = "", -1, "TIMEOUT"
                finally:
                    if pin_dir is not None:
                        shutil.rmtree(pin_dir, ignore_errors=True)
                elapsed = time.time() - started
                time.sleep(SETTLE)
                after = key_usage()

                (OUT / f"{slug}.txt").write_text(stdout, encoding="utf-8")
                if stderr:
                    (OUT / f"{slug}.err.txt").write_text(stderr, encoding="utf-8")
                usage = {}
                if usage_file.exists():
                    try:
                        usage = json.loads(usage_file.read_text(encoding="utf-8"))
                    except json.JSONDecodeError:
                        usage = {}

                record = {
                    "model": model,
                    "provider": provider,
                    "prompt": prompt_name,
                    "run": run,
                    "returncode": rc,
                    "seconds": round(elapsed, 1),
                    "actual_cost_usd": round(after - before, 8),
                    "estimated_cost_usd": usage.get("estimated_cost_usd"),
                    "input_tokens": usage.get("input_tokens"),
                    "output_tokens": usage.get("output_tokens"),
                    "api_calls": usage.get("api_calls"),
                    "session_id": usage.get("session_id"),
                    "transcript": f"{slug}.txt",
                    "chars": len(stdout),
                    "stderr_head": (stderr or "")[:300],
                }
                with results.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(record) + "\n")
                print(
                    f"{slug}: rc={rc} {elapsed:.0f}s "
                    f"${record['actual_cost_usd']:.5f} chars={len(stdout)}",
                    flush=True,
                )
    print("BENCH COMPLETE", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
