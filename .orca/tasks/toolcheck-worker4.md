STRUCTURAL CHECK — confirm the LIVE agent holds exactly three tools.
READ-ONLY. REPORT ONLY. You are WORKER-4, Platform & Release Engineer.

Read .orca/WORKER.md, .orca/WORKER-4.md, MICHAEL.md, .orca/RAILWAY.md.

## Why this matters more than it looks
This project once had a deployed agent use `ingest_source_url` mid-answer,
pull a headings-only page into the corpus, and cite it in the same turn. The
three-tool profile is the structural guarantee that it cannot happen again.
`tests/test_runtime_config.py::test_the_agent_profile_grants_no_write_tool`
guards the repo. **This task guards the thing that is actually running.**

## THE CENTRAL INSTRUCTION
**Read the RENDERED config INSIDE THE CONTAINER. Not the template in the repo.
The repo is not what is running.**

`hermes/config.template.yaml` is the source, and `render_config.py` writes the
rendered config at boot. A check against the template proves only what SHOULD
have been rendered. The failure mode this project has already suffered is
`agent.disabled_toolsets` being silently reverted at boot, which restored the
full toolset to the web chat while the CLI stayed restricted — the template
was right and the running config was not.

## Deliverables
1. The rendered config as it exists in the running `michael-hermes` container:
   confirm the `mcp_servers.michael.tools` block grants exactly
   **`classify_request`, `search_provisions`, `draft_document`** and nothing
   else, and that `apply_schema`, `ingest_source_url`, `ingest_local_file` and
   `seed_corpus` are excluded. Quote the block. **Redact any secret** — confirm
   a key is PRESENT without printing its value.
2. Confirm `--allow-writes` is not on the MCP server's argv in the running
   config.
3. Confirm **no ingestion tool is reachable from the chat surface.** The CLI
   and the chat have diverged before, so check the surface, not just the file:
   report what `agent.disabled_toolsets` actually contains at runtime.
4. Report whether the running config matches
   `hermes/config.template.yaml` at commit `3264af9`. If it does NOT, that is
   the finding — quote both and do not reconcile them.

## Also available to WORKER-3
WORKER-3 is running a six-scenario behaviour acceptance test against the
deployed agent in parallel. If it hits something Railway-side it cannot reach,
I may route it to you. Do not go looking for that work; wait for me.

## Constraints — these bind you
- **READ-ONLY. REPORT ONLY. FIX NOTHING.** The owner wants the state of the
  deployed system before it changes. If the running config is wrong, that is
  the finding — do not correct it, do not redeploy to "refresh" it.
- No writes anywhere. No ingest, no schema change, no DELETE, no TRUNCATE.
- **Do not restart, redeploy, or change any Railway service, variable, volume
  or deployment.** Do not open the public Postgres proxy.
- Do not delete data. Anything that deletes escalates to me, then the owner.
- MICHAEL.md is read-only to you.
- Nothing committed. No push.
- **Never print a secret.** The rendered config carries database passwords and
  the embedding key. Confirm presence, never content. If you must show the
  block, redact the values.
- Two shell traps from RAILWAY.md: base64-encode any Python payload, because
  your local shell parses `railway ssh` arguments first; and run `railway ssh`
  from bash, not PowerShell 5.1, which swallows a native command's stderr so a
  real failure looks like empty output.

## Observable acceptance
The quoted, secret-redacted tools block from the RUNNING container; the argv;
the runtime `disabled_toolsets`; and a plain statement of whether running
matches template at `3264af9`. Say which command you ran to read it, so the
provenance of the evidence is on the record.
