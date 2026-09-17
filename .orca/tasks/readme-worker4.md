README TROUBLESHOOTING NOTE — Docker Desktop crash-at-startup recovery.
You are WORKER-4. Small, documentation only.

Read .orca/WORKER.md and .orca/WORKER-4.md.

## Your last task is closed — the owner solved it himself
You reported it correctly. You did not loop, you escalated with evidence, and
your diagnosis was sharper than the working theory: the GUI processes were up
but com.docker.service was Stopped and Start-Service was refused for lack of
privilege. That was right and it is why this note is worth writing.

## What was actually wrong
Docker Desktop was **crashing at startup, not failing to start** — which is
why "restart Docker" and retrying `docker compose` could never have worked,
and why the error points at the wrong thing.

Orphaned AF_UNIX socket reparse points, left behind by a hard kill on
15 September 2026, could not be renamed or deleted. Windows reported
**"The file cannot be accessed by the system"** to Docker and to the owner
alike. Two directories carried them:

- `AppData\Local\Docker\run`
- `AppData\Local\docker-secrets-engine`

The individual files were undeletable, so the owner **rotated both
directories** and Docker recreated them clean. It also needed
`com.docker.service` started, which **required elevation**.

Result: engine 29.8.0, `docker-desktop` WSL distro Running, michael-postgres
Up and healthy on 127.0.0.1:5433, michael_postgres_data intact with nothing
lost, and documents/provisions unchanged at 204 / 8756.

## The task
Add a short note to the README's troubleshooting material — the "Known
limitations" section is where this project records traps of this kind; put it
wherever it actually belongs alongside the existing Docker/Hermes notes, and
say where you put it and why.

What the note must convey, briefly and in the README's existing voice:
- the symptom as it presents (compose and `docker info` cannot reach the
  engine pipe; `com.docker.service` Stopped; nothing listening on 5433);
- that the real cause is a startup crash from orphaned socket reparse points,
  **not** a compose or configuration problem, so the error message points at
  the wrong thing;
- the two directories, and that rotating them is the fix because the
  individual files cannot be deleted;
- that starting `com.docker.service` needs elevation;
- that this recurs after any hard kill of Docker Desktop.

Keep it tight. This project's README is dense and factual; match it. Do not
pad it into a tutorial, and do not restructure surrounding sections.

## Constraints — these bind you
- **README.md only.** No code, no config, no compose changes.
- MICHAEL.md is read-only to you.
- Do not touch the RETRIEVAL_MIN_SCORE narrative in the README. There is a
  known stale-0.65 vs calibrated-0.60 discrepancy in "Known limitations" and
  it belongs to WORKER-2 in a later, currently-held stage. Leave it alone
  even though you will read past it.
- Do not touch Railway, and do not use the Railway MCP tools.
- No git push. No commit without my approval.

## Observable acceptance
- The diff, exactly as applied.
- A sentence on where you placed it and why that location.
- `uv run pytest` and `uv run mypy .` — paste both. A README edit should move
  neither, and I want the confirmation it did not.
