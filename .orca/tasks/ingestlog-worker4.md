INVESTIGATE — the ingestion file log is incomplete. Report before changing code.
You are WORKER-4, Platform & Release Engineer. Orca restarted; your terminal
is fresh and you hold no memory of earlier work.

Read first, in full:
  D:\Projects\michael\.orca\WORKER.md      (rules binding every worker)
  D:\Projects\michael\.orca\WORKER-4.md    (your role, your files, your traps)
  D:\Projects\michael\MICHAEL.md           (the safety rules)
  D:\Projects\michael\.orca\RAILWAY.md     (the operator path - new)

## The defect, and what the owner has already confirmed

The Privacy Act 1988 (Cth) was ingested into both environments. It IS recorded
in the `ingestion_log` DATABASE table — the owner confirmed the production row
directly: timestamp 2026-09-17 01:55:34, host `www.legislation.gov.au`,
outcome `allowed`, sha256 `877b0f71...`.

It is **NOT** in `sources/ingestion.log.jsonl`, which still holds three
entries, the newest dated 15 September.

You do not need to re-establish any of that. It is verified. Start from it.

## The narrow question

1. Do `ingest_file` and `ingest_url` BOTH write the file log, or does the file
   log only receive URL fetches and refusals while file ingests reach the
   database alone? Answer from the code path, and show the code.
2. If the file log is incomplete by construction, is the README's claim that
   it is the durable record simply **wrong**?

Keep it to that. Do not audit the whole ingestion module.

## Why this matters more than a logging nit

The README already records this related fact: **purging documents truncates
the database `ingestion_log` through a foreign key** — `TRUNCATE documents
CASCADE` takes the log with it. The durable file log at
`sources/ingestion.log.jsonl` is the stated reason that is survivable.

So if the file log does not receive file ingests, that safety story has a
hole: a purge would erase the only record of a file-ingested document. Say
plainly in your report whether that hole is real, and how wide. That is the
part the owner needs, not the logging mechanics.

## Deliverables
- The answer to question 1, with the relevant code quoted and the file and
  line references.
- The answer to question 2: is the README's durable-record claim wrong, and
  in exactly which sentence.
- A plain statement of whether the purge/foreign-key safety hole is real.
- Your RECOMMENDATION for the fix — what should change, in code and/or in the
  README — as a proposal. **Recommend; do not implement.**

## Constraints — these bind you
- **REPORT BEFORE CHANGING ANYTHING.** No code change, no README change, no
  migration in this task. It is an investigation. If you find yourself
  editing, stop.
- **READ-ONLY against data.** No ingest, no purge, no TRUNCATE, no DELETE, no
  schema change, in either environment.
- **Do not delete any data or any log.** Anything that deletes escalates to
  me and then to the owner.
- Do not touch Railway service config, variables, volumes or deployments.
  Reading over `railway ssh` per RAILWAY.md is fine; changing is not.
- Never open the public Postgres proxy. The database stays private.
- MICHAEL.md is read-only to you. Propose wording to me instead.
- Do not touch the agent's MCP tool profile: exactly three tools,
  `classify_request`, `search_provisions`, `draft_document`, and no write
  access. Nothing in this task goes near it.
- No git push — the owner's alone. No commit without my approval.
- Never print a secret. Confirm a value exists without echoing it.

## Observable acceptance
The four deliverables, each grounded in quoted code or real observed output,
with file and line references. If something is ambiguous in the code, say so
rather than resolving it by assumption. Report what you ran and what you saw.
