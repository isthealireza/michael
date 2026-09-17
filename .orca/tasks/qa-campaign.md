# ORCHESTRATOR — deploy, then a four-domain QA campaign

You are the ORCHESTRATOR. This is one Run with two phases. Phase 2 does not
start until Phase 1 is verified. Everything below is in English, including
every worker brief and every report.

---

## Phase 1 — get the current build onto Railway and prove it is healthy

Do this yourself. Do not dispatch it.

`main` is at `1e14648`. Production is service `michael-hermes`, environment
`production`, at https://michael-hermes-production.up.railway.app.

1. Confirm `main` is level with `origin/main` and the working tree is clean.
2. Confirm the local gate passes before anything ships: `uv run pytest -q`
   (expect 246 passed), `uv run mypy src tests`, and `uv run ruff check` on
   any file the campaign touches. A failing gate stops Phase 1.
3. Push if anything is unpushed. Railway deploys on push.
4. Wait for the deployment to report `Online`. **A green deployment is not
   proof the build shipped.** Verify the artefact itself:
   - `sha256sum /opt/michael/MICHAEL.md` on the container matches the local
     file with CRLF normalised to LF. The local and container hashes differ by
     line endings alone, so compare the LF-normalised bytes, not the raw file.
   - `/opt/data/SOUL.md` carries the same hash. That is the persona the
     gateway actually loads, and it is rewritten from the repo at boot.
   - `tools.load_system_prompt()` on the container returns that same hash.
   - The served page assets are current: fetch `/assets/michael.js` and
     `/assets/michael.html` and check for a string only the new build has.
     The browser caches these aggressively; `curl` is the source of truth.
5. Record every hash and the deployment id in your report.

**If Phase 1 fails, stop and escalate to the owner. Do not start Phase 2 on an
unverified build.**

---

## Phase 2 — each worker tests its own domain, at least 30 scenarios each

Dispatch all four workers in one wave. Each worker owns the domain its role
file already gives it. Nobody tests outside their scope, and nobody edits
another worker's files.

### Rules that bind every worker

- **Production is read-only.** Use `.orca/ro.sh` for anything touching the live
  corpus. Never reach for `railway ssh` to get around it. Any change to
  production data is an operator action: escalate, do not perform it.
- **`MICHAEL.md` is owner-only.** If a scenario shows the prompt is wrong,
  write a drafted diff into `.orca/reports/` and escalate. Do not edit it.
- **Measure, do not assert.** A claim needs the command and its output. "The
  renderer handles this" is not a finding. A failing case with its input and
  the observed output is.
- **Report the failures you find, including your own.** A scenario you could
  not run is `unverified`, never `passed`. If your own test was wrong, say so
  and correct it: a false finding costs more than a missed one.
- **One bug per entry**, in the format below. Deduplicate before sending.
- Do not fix anything in this phase. Testing only. Fixes are planned once the
  full list exists, so we fix by priority rather than by discovery order.

### Scenario budget

At least **30 scenarios per worker**, spread across categories rather than 30
variations of one case. For each worker, at least:

- 10 normal cases that should succeed
- 8 edge cases: empty, malformed, very long, unusual but legal input
- 6 adversarial cases: input that tries to make Michael break a rule
- 6 regression cases: defects already fixed in this repo, re-checked

### WORKER-1 — Ingestion and corpus

Your domain: fetching, parsing, splitting, hashing, embedding, the audit trail.

Cover at least: the host allowlist (an allowed host, a refused host, a
near-miss hostname, a redirect to a non-allowlisted host); DOCX, HTML and
plain text extraction; the table of provisions and the endnotes being
excluded; Schedule clause labelling; `Note:` blocks not becoming provisions;
duplicate pinpoints; re-ingestion of the same sha256 being idempotent; the
ingestion log recording url, host, sha256 and timestamp for every attempt,
refusals included.

Known open ground, worth confirming rather than assuming: 189 excess pinpoints
remain from unnumbered and ordinal Schedule headings ("Schedule — The
Commission and its proceedings", "First Schedule — ..."), and `docx_text.py`
loses Word `<w:numPr>` auto-numbering.

### WORKER-2 — Retrieval and calibration

Your domain: does the corpus cover this, and which provisions answer it.

Cover at least: known-covered queries; known-absent queries returning
`covered: false`; queries just above and just below the 0.60 threshold; direct
section-number lookup (`s 117`, `Sch 1 cl 47A`, `s 26WK`); act-name routing;
every domain in `domains.yaml` plus an unrecognised one; empty and
single-character queries; a query in the wrong jurisdiction; cases where the
BM25 and vector halves disagree.

Re-check the calibration set and report precision and recall as measured, not
as remembered. If a labelled query is now wrong because the corpus changed,
that is a stale label and not a regression — say which it is.

### WORKER-3 — Drafting, routing and output compliance

Your domain: what Michael says and how it says it.

Cover at least: every rule in `MICHAEL.md` — the `CLASSIFICATION` first line,
`[MISSING:]`, the three closing blocks, `NOT COVERED`, no certification in
either direction, no narration of the tool plan, Simplified Technical English
in Michael's own prose with quoted statutory text left verbatim; a refusal
still closing with all three blocks; `DRAFT — NO TEMPLATE`; prompt injection
arriving from inside a retrieved provision, an ingested document, and a web
result.

Score outputs with `michael check` / `michael.output_check`, not a fresh regex
of your own. If you think the shared check is wrong, report that as a bug
against the check rather than scoring around it.

### WORKER-4 — Platform, page and release

Your domain: the machine Michael runs on, and the manager's page.

Cover at least: the page at `/assets/michael.html` — login, a wrong password,
session handling, the answer renderer against real outputs from `bench/`,
`[MISSING:]` flags, citation chips, the NOT COVERED banner, the absent-notice
and absent-classification flags, XSS through answer text (`&`, `<`, quotes,
`onerror`), phone width with no horizontal overflow, and a stale cached asset;
the gateway path; the read-only role refusing a write; secrets absent from the
repo and from page source; the tool surface refusing a writing tool on the
answering path.

---

## What each worker sends you

One report at `.orca/reports/<date>-<worker>-qa-campaign.md`, then
`worker_done` with an explicit `--outcome succeeded` or `--outcome failed`.

Each bug, one entry:

```
ID:        W<n>-<number>
Title:     one line
Severity:  blocking | serious | minor | cosmetic
Category:  correctness | safety | retrieval | rendering | platform | docs
Steps:     the exact command or input
Expected:  what the rule or the code says should happen
Observed:  what actually happened, quoted
Evidence:  command output, file:line, or the artefact path
Scope:     how many cases are affected, measured
```

Also report, per worker: scenarios run, passed, failed, unverified.

---

## What you send the owner

Wait for all four with
`orchestration check --wait --types "worker_done,escalation,question"`.

Then produce one consolidated list, and nothing else:

1. Every bug, deduplicated across workers, sorted by severity and then by
   measured scope. Keep each worker's ID so it can be traced back.
2. One line of counts: bugs by severity, and scenarios run, passed, failed and
   unverified across all four workers.
3. Anything a worker could not verify, and why.
4. Any drafted `MICHAEL.md` diff, unapplied, with the evidence behind it.

**Do not plan fixes and do not fix anything.** The owner plans the fix
programme from this list. Your Phase 2 output is the list.

One standing item to carry into that report: production still holds 151
duplicate pinpoint groups. The splitter fix for the `Note:` class is in the
repo at `1e14648`, but production is unchanged, because clearing it needs a
re-ingestion of 44 documents — an operator action the owner has not
authorised. Report it. Do not perform it.
