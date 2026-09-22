# Michael production Railway configuration audit — 2026-09-22

## Target identity

The authorized helper `.orca/ro.sh` identifies service `michael-hermes` and environment `production`. The public target checked was `https://michael-hermes-production.up.railway.app`; Railway edge responses included `Server: railway-hikari` and Railway request IDs. No deployment or product code was changed.

## Exact commands and redacted results

* `Get-Content .orca/ro.sh` — helper reviewed; it hard-codes `SERVICE=michael-hermes`, `ENVIRONMENT=production`, and sets `MICHAEL_DATABASE_URL="$MICHAEL_RO_DATABASE_URL"` for its read-only CLI/Python paths.
* `railway status` and `railway status --json` — **blocked**: `No linked project found. Run railway link to connect to a project`.
* `bash .orca/ro.sh cli hosts` — **blocked by local shell prerequisites**: `python: command not found`; a temporary PATH shim then reached Railway but the CLI still reported no linked project. No direct writable Railway command was used.
* `curl.exe -sS -D - https://michael-hermes-production.up.railway.app/` — `302 Found`, `location: /login`.
* `curl.exe` GET `/login` — `200 OK`; login form declares provider `basic`.
* POST `/auth/password-login` with sentinel invalid credentials only (`provider=basic`, username/password `__audit_invalid__`) — `401 Unauthorized`, `{"detail":"Invalid credentials"}`. No real password or hash was printed or submitted.
* GET `/api/auth/ws-ticket` without cookies — `401 Unauthorized`, reason `no_cookie`, login URL `/login`.
* GET `/assets/michael.html` — `200 OK`, 9,405 bytes; GET `/assets/michael-c6cc4e57dcc6.js` (the hash referenced by served HTML) — `200 OK`, 18,686 bytes, SHA-256 `C6CC4E57DCC6DF43DF93BE1FB97C2D95FD9DE440E8AE8EC083213514C8CFFC0F`.
* Secret scan over served HTML/JS for `password|secret|token|api[_-]?key|bearer|DATABASE_URL|BEGIN .*PRIVATE|railway|MICHAEL_|system.prompt|localhost|127.0.0.1` — no credential/secret material found. Matches were limited to the expected password input, auth UI, and an internal `mcp__michael__` code comment / basic login call.

## Configuration/auth/secret-exposure verdict

* `DASHBOARD_USERNAME`: **not verifiable** because the read-only Railway session could not be reached from this worktree (CLI has no linked project); no value exposed.
* `DASHBOARD_PASSWORD_HASH`: **not verifiable**; deliberately not exposed or printed.
* `MICHAEL_DOMAINS_FILE`: **not verifiable** remotely; local template documents `/opt/michael/domains.yaml`, but this is not proof of production runtime configuration.
* `MICHAEL_SYSTEM_PROMPT`: **not verifiable** remotely; local template documents `/opt/michael/MICHAEL.md`, but this is not proof of production runtime configuration.
* Dashboard authentication: **gate verified**, because unauthenticated `/` redirects to `/login`, invalid sentinel credentials receive 401, and protected `/api/auth/ws-ticket` rejects requests with no cookie. **Successful credential verification remains blocked** because the real password is not available and must not be disclosed.
* Served page/source secret exposure: **no evidence found** in the checked public HTML/hashed JS; this is a source scan, not proof about server environment variables.

## Blocker

The current worktree is not linked to Railway project `michael`, so `railway status`, variable inspection, and `.orca/ro.sh` remote checks cannot establish the four runtime variable values. An operator with the existing project link should rerun `.orca/ro.sh` (or equivalent read-only Railway tooling explicitly targeting `michael-hermes`/`production`) and record presence/path semantics without printing secrets.
