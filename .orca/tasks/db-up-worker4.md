UNBLOCK — bring local Postgres up and report the exact corpus inventory.
You are WORKER-4. Small, bounded, non-destructive.

Read .orca/WORKER.md and .orca/WORKER-4.md.

## Why
WORKER-1 could not reach local Postgres this session: `docker compose ps`
could not reach the Docker Desktop engine (GUI running, API pipe not), and a
TCP probe of 127.0.0.1:5433 timed out. That blocks two things — the Privacy
Act ingest, and an exact measurement of how incomplete the existing corpus is.
Docker and the local database are your ground, not WORKER-1's.

## Deliverables

1. Bring the local Postgres container up (docker-compose.yml, pgvector/pg16,
   loopback-bound, named volume michael_postgres_data). Report what was wrong
   and what you did. If Docker Desktop's engine cannot be started from a
   shell, say so plainly and stop — do not fight it indefinitely, and do not
   reinstall or reconfigure Docker.

2. Once it is up, report the corpus inventory, READ-ONLY:
   - total documents and total provisions;
   - the exact list of the ingested documents — citation, jurisdiction and
     doc_type — written to a file, with the count;
   - confirm whether the count matches the 204 documents / 8,756 provisions
     recorded in the project docs, and say so if it does not.

3. Confirm the database is reachable on 127.0.0.1:5433 and NOT exposed
   beyond loopback.

## Constraints — these bind you
- **READ-ONLY against the data.** SELECT only. Do not ingest, seed, migrate,
  truncate, drop or alter anything. Starting the container is the only state
  change authorised here.
- **Do not delete any data or volume.** Escalate to me if you think anything
  needs removing. That is the owner's call, never yours and never mine.
- Do not touch Railway at all, and do not use the Railway MCP tools — their
  view of this account is a decoy project and is wrong.
- No git push. No commit without my approval. This task should need neither.
- Never print a secret. Confirm a connection works without echoing the URL.

## Observable acceptance
- `docker compose ps` showing the container healthy, pasted.
- The document/provision counts, pasted.
- The path to the document list file, and its line count.
- A plain statement of whether the numbers match 204 / 8,756.
