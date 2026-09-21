# Operating Michael on Railway

This is the operator path. It replaces "start Docker, then work locally" for
everything that is an operation rather than a code change.

## Why this exists

Docker Desktop on this machine crashes at startup after any hard kill, leaving
orphaned `AF_UNIX` socket reparse points that Windows refuses to delete. It has
cost two working sessions. Operations no longer depend on it.

## The connection

```
railway ssh --service michael-hermes --environment production <command>
```

This reaches the running container over Railway's **private** network. It does
not open the public Postgres proxy, which the project's security rules forbid.

The repo at `D:\Projects\michael` is already linked to project `michael`,
environment `production`, service `michael-hermes`.

Michael's operator CLI lives at `/opt/michael/.venv/bin/michael` inside the
container and carries every subcommand:

```
schema  domains  hosts  prompt  classify  search  draft  seed  ingest  ingest-file
```

The CLI uses the read/write database URL. That is separate from the agent's
MCP profile, which still holds exactly three tools and no write access. Running
an operator command by hand does **not** put write tools back in the agent's
hands, and nothing here may change that.

## Two paths, and who gets which

**Workers get `.orca/ro.sh`.** It points both `MICHAEL_DATABASE_URL` and
`MICHAEL_RO_DATABASE_URL` at the `michael_ro` role, so every statement runs in
a session with `default_transaction_read_only = on`. A write is refused by
Postgres, not by a promise: `michael ingest` through the helper fails with
`ReadOnlySqlTransaction: cannot execute INSERT in a read-only transaction`.

**The operator gets `railway ssh` directly.** That reaches the read/write role
and is how ingestion, restores and schema work are done.

The boundary is real but not complete, and it is worth being honest about the
limit: the Railway CLI session is machine-wide, so a worker that ignores the
protocol and calls `railway ssh` itself still reaches the write role. The only
full boundary is a separate environment with its own database. Until then,
`ro.sh` is the documented path and using anything else is a protocol breach to
be escalated, not a shortcut.

## What runs where

| Activity | Where | Why |
|---|---|---|
| Writing code | Local | The repo is here |
| The test suite, mypy | Local | Tests touch the schema; production data is not a fixture |
| First ingest of any new source | Local | The gate that catches parser faults |
| Production ingest, once proven locally | Railway | Operator action |
| Corpus inspection, counts, spot checks | Railway | Read-only, no Docker needed |
| Schema work | Railway | Idempotent |
| Recalibration | Local, then apply the number to Railway | Needs the labelled set |

**The local-first rule for new sources is not negotiable.** It exists because a
parser bug was caught exactly there: `_is_contents_entry` was dropping every
section heading that ended in a cited Act's year, and a production-first ingest
would have landed 355 provisions with a section missing and no symptom beyond
an occasional wrong NOT COVERED.

## Deployment

Railway builds from the GitHub repo, branch `main`. A push deploys.

**Always push a parser or schema change before ingesting on Railway.** The
container runs the code from the image, not from your working tree; ingesting
before the deploy lands runs the old parser against the new source.

```
railway status                      # service state, volumes
railway logs --service michael-hermes
railway redeploy --service michael-hermes --environment production --yes
railway volume list                 # check headroom before an ingest
```

## Reading the corpus

```
railway ssh --service michael-hermes --environment production \
  /opt/michael/.venv/bin/michael search "<query>"
```

For raw SQL, connect from inside the container rather than exposing the
database.

## Rules that do not change here

1. Ingestion stays an operator action. It never returns to the answering path.
2. The host allowlist is unchanged: `legislation.wa.gov.au`,
   `legislation.gov.au`, `fairwork.gov.au`, `austlii.edu.au`.
3. Postgres stays on the private network. Never create a TCP proxy for it.
4. Deleting data on Railway is the owner's decision. Escalate.
5. No real client data, in any environment.
6. Secrets stay in Railway environment variables. Never print one.

## Rollback

F3 in `.orca/PRODUCTION-READY.md`. There is no dedicated `railway rollback`
CLI subcommand (`railway --help` lists `redeploy`, `restart`, `down`, `up` and
`deployment`, and nothing else that touches a past deployment) - that part of
the original finding (W4-6) still holds. There IS a real rollback mechanism,
verified below against this project's actual linked service, not guessed.

### 1. Identify the last known-good deployment

```
railway status                        # current deployment ID, confirms the linked service
railway deployment list --limit 20     # id | status | timestamp, newest first
```

Every deployment superseded by a newer one shows `REMOVED`, not `SUCCESS` -
that is Railway's normal history bookkeeping, not a sign the deployment
failed. Read the deployment's commit before trusting it as "known-good":

```
railway deployment list --json --limit 20   # includes meta.commitHash, meta.commitMessage
```

Match `commitHash` against `git log --oneline` in this repo to confirm the
target was a commit that had a green `uv run pytest` / `uv run mypy src tests`
gate (and, once `.github/workflows/ci.yml` exists, a green CI run) at the time
it was deployed - a rollback to a red commit is not a rollback to "good", it
is a rollback to a different, differently-broken state.

**Not every past deployment can be rolled back to.** Railway's own schema
carries a `canRollback` field per deployment (see the Public API note below);
old build artefacts can age out. Check before promising a specific target:

```
railway api 'query($id: String!) { deployment(id: $id) { id status canRollback meta } }' \
  --var id=<deployment-id>
```

### 2. Roll back

**Dashboard (primary path):** open the project, the `michael-hermes` service,
its Deployments tab; find the target deployment by the commit identified in
step 1; use its rollback action. `railway open` opens the project dashboard
in a browser from this machine.

**CLI/API (no dashboard needed):** the Public API exposes a real
`deploymentRollback` mutation
(https://docs.railway.com/integrations/api/manage-deployments#rollback-to-a-deployment),
reachable from this machine without a browser via the CLI's own GraphQL
passthrough:

```
railway api 'mutation($id: String!) { deploymentRollback(id: $id) { id status } }' \
  --var id=<deployment-id-from-step-1>
```

This is a production mutation - a deploy, functionally - so it is an
**operator action**: an escalation and the owner's call, exactly like a
forward deploy, not something a worker runs unprompted. Read-only inspection
(`railway status`, `railway deployment list`, the `deployment{...}` query
above) stays fine for anyone to run.

### 3. Verify - by artefact, not by the green light

A rollback that only checks "is the service Online" repeats this project's
own mistake of trusting a status light over a measurement (see
`.orca/PRODUCTION-READY.md`'s own framing, and W4-7). After the rollback
reports success:

```
railway status                                                    # new deployment ID matches the target
curl -sI https://michael-hermes-production.up.railway.app/assets/michael.html
curl -sI https://michael-hermes-production.up.railway.app/assets/michael.js
```

Compare the served bytes' sha256 (LF-normalised - see finding W4-1) against
`git show <target-commit>:web/michael.html` / `:web/michael.js` from this
repo at the commit identified in step 1. A matching hash is the proof the
rollback actually landed the intended build, not merely that Railway reports
a green status - the same distinction D1-D3 already draw for a forward
deploy. Re-check `.orca/ro.sh cli hosts` (or another cheap read) to confirm
the container is actually serving, not just reporting `Online` while still
booting.

## Known trap

`railway ssh` arguments are parsed by your local shell first. A quoted Python
one-liner through PowerShell needs care; prefer passing the command as separate
arguments rather than one quoted string.
