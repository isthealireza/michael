STAGE 1 PHASE B — ingest the Privacy Act 1988 (Cth). You are WORKER-1.
The database is back. This is the real ingest. Spend is approved.

Read .orca/WORKER.md and .orca/WORKER-1.md.

## The blocker is gone
Docker was crashing at startup, not failing to start: orphaned AF_UNIX socket
reparse points from a hard kill could not be renamed or deleted, and Windows
reported "The file cannot be accessed by the system". The owner rotated the
two directories holding them and started com.docker.service with elevation.
Engine 29.8.0, docker-desktop WSL distro Running. **Do not go looking for
this in docker-compose.yml — it was never a compose problem.**

Owner-verified state, read-only: michael-postgres Up and healthy on
127.0.0.1:5433; michael_postgres_data intact, nothing lost; documents 204,
provisions 8756, unchanged from before the outage; and no document with
"privacy" in its title — so the gap is still open and this ingest is still
needed.

## Authorisation
The owner approved this specific ingest as a deliberate operator action, and
approved the embedding spend at the estimated ~$0.011. That approval stands
and does not need re-asking. It is NOT a licence to put ingest_source_url,
ingest_local_file or seed_corpus on the answering agent's MCP profile, and
--allow-writes is never passed on that path.

**LOCAL ONLY.** Do not touch Railway. Do not use the Railway MCP tools. The
owner runs the production ingest himself, separately, later.

## The task
Ingest the Privacy Act 1988 (Cth) from the Federal Register DOCX volume you
identified in Phase A — compilation 104, in force 2026-06-04, sha256
877b0f717e01d69b071ac3a93a48019bf8a9073322b751fb9b8553f1583bdf7d — using
`ingest-file` with `--source-url` intact so the allowlist check still runs.
Not the /latest HTML page, which is headings only.

Use the FIXED parser now in the tree. Your Step 1 fix is accepted and the
owner verified it independently.

## Acceptance — unchanged from Phase A, plus one
1. Subsection markers such as (1) and (2) appear in provision BODIES, not
   only headings. Show real excerpts.
2. Every provision has a parent `documents` row.
3. sha256 is computed over the ORIGINAL downloaded bytes, before parsing.
4. The ingestion log records url, host, sha256, timestamp.
5. **NEW AND EXPLICIT: 26WD is present in the ingested provisions.** Query it
   back out of the database by section number and paste its stored heading and
   the opening of its operative text. This is the section the old parser
   silently dropped and it is the point of the whole exercise.
6. Run `refresh_corpus_stats()` after the write. BM25 once read N=7,071
   against an 8,756-provision corpus because this was missed on a non-seed
   write path.
7. Report the real spend if you can observe it, against the ~$0.011 estimate.

Also report the before/after document and provision counts, so the delta is
on the record.

## Constraints — these bind you
- This write is authorised. **Nothing else is.** Do not delete, truncate,
  re-ingest, or modify any existing corpus row. Deleting data is the owner's
  call alone and you escalate to me.
- Do not touch retrieval scoring, thresholds, calibration/ or bench/. That is
  WORKER-2's ground and a later, held stage.
- Do not fix Defect A or Defect B. Your scoping proposal is with me and the
  owner decides. Not now, not opportunistically.
- No git push. No commit without my approval.
- Keep the repo root clean — no scratch files left behind this time. Work in
  your temp report directory.
- Secrets stay in .env. Never print a key.

## Observable acceptance
All seven points above, each with real pasted output from the real database,
not a claim. If the ingest fails or lands incomplete, say so with the output
and stop; do not retry blind or paper over a partial write.
