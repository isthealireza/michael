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
import subprocess
import time
import urllib.request

OUT = pathlib.Path("/opt/data/bench")
RUNS = 3
TIMEOUT = 900
SETTLE = 6  # seconds to let OpenRouter usage catch up before reading it back

MODELS = [
    "anthropic/claude-opus-5",
    "anthropic/claude-sonnet-5",
    "anthropic/claude-haiku-4.5",
    "deepseek/deepseek-v4-pro",
    "deepseek/deepseek-v4-flash",
    "qwen/qwen3.7-flash",
    "openai/gpt-oss-120b",
]

PROMPTS = {
    "contract": (
        "I need a casual employment contract between Company X and Mr Y, with conditions A and B"
    ),
    "uncovered": (
        "What are the notification requirements for an eligible data breach "
        "under the Privacy Act 1988 (Cth)?"
    ),
}


def key_usage() -> float:
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/key",
        headers={"Authorization": "Bearer " + os.environ["OPENROUTER_API_KEY"]},
    )
    with urllib.request.urlopen(req, timeout=40) as response:
        return float(json.load(response)["data"]["usage"])


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    results = OUT / "results.jsonl"
    done = set()
    if results.exists():
        for line in results.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                done.add((r["model"], r["prompt"], r["run"]))

    for model in MODELS:
        for prompt_name, prompt in PROMPTS.items():
            for run in range(1, RUNS + 1):
                if (model, prompt_name, run) in done:
                    continue
                slug = f"{model.replace('/', '_')}__{prompt_name}__{run}"
                usage_file = OUT / f"{slug}.usage.json"
                before = key_usage()
                started = time.time()
                try:
                    proc = subprocess.run(
                        # --provider openrouter is mandatory: Hermes routes some
                        # model ids elsewhere (sonnet-5 resolves to 'gmi'), which
                        # fails with no credentials and would also put the spend
                        # outside the key whose usage is being measured.
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
                    )
                    stdout, rc, stderr = proc.stdout, proc.returncode, proc.stderr
                except subprocess.TimeoutExpired:
                    stdout, rc, stderr = "", -1, "TIMEOUT"
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
