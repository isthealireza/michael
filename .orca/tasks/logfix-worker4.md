FIX — make the durable ingestion record actually durable. You are WORKER-4.

Your investigation is ACCEPTED. I verified it myself: `log_attempt` is called
only from inside `sources.py`; `fetch()` has exactly one call site,
`ingest.py:443`, the URL path; `ingest_file` calls bare `check_url()` at
ingest.py:516 and never reaches the file log. README.md:222-225 is wrong as
written. You found it precisely and you did not change anything first. Good.

## What changed since you reported — this makes it urgent, not theoretical

The owner has APPROVED and is RUNNING a full re-ingest of all 204 legacy
documents. That meant `TRUNCATE documents CASCADE`, which he has now
EXERCISED. The cascade wiped the database `ingestion_log`, which held **205
rows**. The file log holds **3**.

So the defect is not 'incomplete'. It is **inverted**: the README calls the
file log the durable record, and justifies that precisely because a purge
cascades the database table away — yet the record that survives the purge is
the empty one, and the record that held the real history is the one the purge
destroys. Lead with that framing; it is the finding.

The owner took `backups/ingestion_log-<stamp>.csv` before the purge for exactly
this reason.

## The fix

1. **Every ingestion path that writes a `documents` row must also write the
   file log** — `ingest_file` and `ingest_url` alike, and check whether the
   seed path (`seed_from_corpus`) writes documents without logging too; if it
   does, it is in scope. Each entry carries url, host, sha256, timestamp.
   Your own recommendation — log from `_write()` so both records land
   together — looks right to me, but it is your ground: implement what is
   actually correct, and say why if you diverge from it.
2. **Refusals must keep being logged.** The existing refusal entries are real
   audit history. Do not lose that behaviour while adding the success paths.
3. **The README must match the code afterwards.** If the code cannot make the
   claim true, change the claim instead. Two documents disagreeing is itself
   the defect on this project; do not leave 222-225 half-true.
4. **A test that would have caught this**, carrying real cases — a file
   ingest, a URL ingest, and a refusal — asserting the file log receives all
   of them. Same discipline as the parser fix that preceded this.

## Also, one README note the owner asked for — I am crossing a boundary here
Add a short note that **the corpus is not self-rebuilding**: `documents`
stores metadata and sha256 but NOT the extracted text, and `provisions` carry
`char_range` offsets into text that is not retained. A heading the old
predicate dropped was never stored, so it cannot be recovered from the
database — which is why fixing a splitter bug requires re-fetching the sources
rather than re-splitting in place. That is ingestion ground, normally
WORKER-1's; I am giving it to you because you are already in the README. Do
not touch any other corpus-defect section.

## BACKFILL IS NOT YOURS
Whether the 205 lost rows get reconstructed from the owner's CSV backup is
**the owner's decision**, and I will put it to him. Do not backfill, do not
restore, do not replay, do not read-and-rewrite the backups. Recommend if you
have a view; do not act.

## Constraints — these bind you
- **Do not delete any data or any log.** Anything that deletes escalates to
  me and then the owner.
- **Do not purge, TRUNCATE, re-ingest or seed anything.** The owner is running
  the rebuild right now. Stay out of it. Do not write to the corpus database.
- Do not touch Railway config, variables, volumes or deployments. Never open
  the public Postgres proxy.
- MICHAEL.md untouched. Propose wording to me instead.
- The agent keeps exactly three tools — `classify_request`,
  `search_provisions`, `draft_document` — and no write access. Nothing here
  goes near that profile.
- `uv run pytest` and `uv run mypy .` green BEFORE you report done. No commit
  without my approval. No push — the owner's alone.
- Leave the repo root clean. Never print a secret.

## Observable acceptance
- The diff, exactly as applied, file by file.
- Which call sites now write the file log, and proof that refusals still do.
- The new test, and its run output showing file ingest, URL ingest and refusal
  all landing in the file log.
- The README diff, showing 222-225 now matches the code, plus the
  not-self-rebuilding note.
- `uv run pytest` and `uv run mypy .` output pasted.
