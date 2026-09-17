STAGE 3 — Hermes auxiliary lane must not spend money. You are WORKER-4.

Read .orca/WORKER.md and .orca/WORKER-4.md before you start, and MICHAEL.md.

## Why
The Railway deploy log carries this warning:
  "auxiliary client PAID lane engaged, OpenRouter fallback model
   google/gemini-3.6-flash is not a :free SKU and may incur real spend"
Michael's cost is benchmarked at $0.01171 per run. A background auxiliary
lane routing to a paid fallback spends outside that number, unmeasured.

## Target
hermes/config.template.yaml — the ONLY file you change for the config itself.
That file is the source. render_config.py rewrites hermes/config.yaml from it
at boot, so anything written straight into config.yaml is overwritten and is
not a fix. The template currently has NO auxiliary block at all; this is an
addition, not an edit. It is at _config_version: 39.

## Change
Constrain the auxiliary/background lane so it cannot select a paid model.
The intended shape is auxiliary.free_only: true.

## PHASE ONE — verify before you write. This is the whole point of the task.
Do NOT write the key first and check later.

Confirm that `auxiliary.free_only` is a key that Hermes v0.21.0 (image
nousresearch/hermes-agent:v2026.8.31) actually recognises. Check the image's
own config schema, `hermes --help`/config docs, or the binary's schema dump —
whatever gives real evidence from that version, not from documentation for a
different one.

Three outcomes, all acceptable:
 - Recognised: proceed to phase two.
 - Not recognised, and a different key does the same job: STOP. Report the
   correct key and your evidence. Do not write it without coming back to me.
 - Not recognised, and no equivalent exists: STOP and report that.

An unrecognised key that silently does nothing is the exact failure mode that
has already bitten this project twice — agent.disabled_toolsets being reverted
at boot, and --insecure being a no-op that exits 0 and restart-loops. A
silently-ignored key would leave the paid lane live while looking fixed.

## PHASE TWO — only if phase one confirms the key
Add the block to hermes/config.template.yaml with a short comment saying why
it is there (unmeasured spend outside the benchmarked $0.01171/run), matching
the file's existing commenting style. Decide and state whether _config_version
needs to move; say which you did and why.

Then verify the template still renders: run the render script and confirm it
produces valid YAML containing the new key. Do not print any secret value —
confirm presence, never content.

## Constraints — these bind you
- No git push. No deploy. No Railway change of any kind in this task.
- MICHAEL.md is read-only to you.
- Do not touch the mcp_servers.michael tools block. The answering agent keeps
  exactly three tools: classify_request, search_provisions, draft_document.
- Do not touch the RETRIEVAL_MIN_SCORE value in the env: block. Another task
  owns that number and will land in this same file after you. Leave it alone.
- Do not use the Railway MCP tools for anything. Their view of this account is
  a decoy project and is wrong. If you need Railway facts, use the Railway CLI
  (railway status / variables / logs / volume list) — but this task needs none.
- No local commit until I approve it.

## Observable acceptance
1. Evidence, quoted, that auxiliary.free_only is (or is not) a real key in
   Hermes v0.21.0. Name where the evidence came from.
2. The diff to hermes/config.template.yaml, exactly as applied.
3. The render script run, with output showing the key present in the rendered
   config and the YAML parsing clean.
4. `uv run pytest` — paste the result. 149 tests are green and stay green.
5. `uv run mypy .` — paste the result. Clean before you report done.
Report what you changed file by file, and what you ran with its real output.
Do not claim a result you did not see.
