# Wave 3 rollback verification

Date: 2026-09-22 (AWST)
Scope: read-only Railway inspection and public artefact/health checks. No rollback,
redeploy, restart, variable, volume, or database operation was performed.

## Documented procedure

`.orca/RAILWAY.md`, section **Rollback**, documents the reproducible path:

1. Confirm the linked service with `railway status`; list history with
   `railway deployment list --json --limit 20`; match `meta.commitHash` to the
   repository and a green test/type-check gate; query `deployment.canRollback`
   before selecting a target.
2. An owner/operator performs the rollback from the service Deployments tab,
   or via the production mutation
   `deploymentRollback(id: $id)`. Workers are limited to the read-only checks.
3. Verify the resulting deployment ID, then compare served asset hashes against
   the target commit (LF-normalised where applicable), and make a cheap read
   through `.orca/ro.sh` to prove the container is serving.

The procedure is consistent with `.orca/PRODUCTION-READY.md` F3 and D1-D3.

## Railway deployment evidence

Commands run (read-only):

```text
railway status --project 693389ce-128e-469f-ab3b-81901cdc4d8a --environment production --json
railway deployment list --project 693389ce-128e-469f-ab3b-81901cdc4d8a --environment production --service michael-hermes --json --limit 20
railway api 'query($id: String!) { deployment(id: $id) { id status canRollback meta } }' --var id=<deployment-id>
```

The active deployment is known-good for this verification:

| deployment | status | created (UTC) | commit | canRollback |
|---|---|---|---|---|
| `bab235f5-33c8-4081-adca-fb49aac52690` | `SUCCESS` | 2026-09-22T04:01:49.045Z | `45a62a944812242cc75e9700a13d8fd7293ee95d` (`fix(retrieval): detect abbreviated jurisdictions`) | `true` |

The immediately previous deployment is a viable rollback target:

| deployment | status | created (UTC) | commit | canRollback |
|---|---|---|---|---|
| `945f1d26-3fd1-420b-992d-803a2c2661d8` | `REMOVED` | 2026-09-22T03:24:45.409Z | `d54f55901b582d5020c1cf0626c320b9ac804daa` (`docs(config): document required deployment variables`) | `true` |

`REMOVED` is Railway's history state for a superseded deployment, not a failed
build. The selected target's commit is present in this checkout's history; the
current checkout gate also passed `276 passed, 5 deselected` (`uv run pytest -q`)
and `Success: no issues found in 41 source files` (`uv run mypy src tests`), both
exit 0. Selecting a different target requires repeating the commit/gate and
`canRollback` checks.

## Health and artefact evidence

Public read-only checks at `https://michael-hermes-production.up.railway.app`
returned:

```text
GET /api/health -> HTTP 200
{"ok":true,"version":"0.21.0","auth_required":true}

GET /assets/michael.html -> HTTP 200, Content-Length: 9405,
  ETag: "681aa3773d9d552618e9c3815e2e9222"
deployed build marker: michael-build=c6cc4e57dcc6

GET /assets/michael-c6cc4e57dcc6.js -> HTTP 200, Content-Length: 18686,
  ETag: "610387a093fac54f6cfe3f3ef4109e27"
```

The downloaded deployed JS SHA-256 is
`c6cc4e57dcc6df43df93be1fb97c2d95fd9de440e8ae8ec083213514c8cffc0f`, exactly
matching the checkout's `web/michael.js` SHA-256. The deployed HTML is a
Docker build-time rewrite (`dev` becomes the JS content hash), so its raw hash
is expected to differ from the source `web/michael.html`; the embedded marker
and referenced content-hashed JS provide the artefact comparison. `/assets/michael.js`
correctly returned 404 because the built asset is content-hashed.

## Result

PASS: rollback procedure is documented and executable, a concrete target is
known (`945f1d26-3fd1-420b-992d-803a2c2661d8`, `canRollback: true`), and the
current service has reproducible health and served-byte evidence. No production
mutation was attempted; an owner must perform any actual rollback and rerun
the post-rollback checks against the target commit.
