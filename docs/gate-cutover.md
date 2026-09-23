# michael-gate cutover runbook

This is the procedure for standing up `michael-gate` in Railway and moving
the public domain from `michael-hermes` to it. It is written to be followed
literally, in order. The order is not advisory: skipping ahead can leave the
Hermes dashboard unreachable (see Step 6).

## Current state — the chat page is DOWN

`https://michael-hermes-production.up.railway.app/assets/michael.html`
returns **404**. `hermes/Dockerfile` no longer copies `michael.html` and
`michael.js` into the Hermes image — the gate serves them now — and that
change reached production the moment `main` was pushed, because Railway
auto-deploys from that branch. The gate was not deployed to take over.

Michael is therefore reachable only through the raw Hermes dashboard at
`/`, behind the single shared `dashboard.basic_auth` credential. Deploying
the gate is what **restores** the reader-facing page; it is not an optional
improvement to a working surface.

If the page is needed before the gate is ready, restore the
`COPY web/michael.html web/michael.js` block and the `RUN` block that
follows it in `hermes/Dockerfile`, and push. Having the page in both images
is harmless — the gate serves its own copy from its own container.

## Precondition — integration tests

`tests/gate/test_users.py` and `tests/gate/test_sessions.py` are marked
`pytest.mark.integration` and are excluded from the default `uv run pytest`
run (see `pyproject.toml`'s `addopts`, `-m "not integration and not
network"`). **A green default run is not evidence that login, rate limiting
or session ownership works.**

### Where to run them

A dedicated Railway Postgres exists for this. Do **not** point these tests
at the production database: the `gate_tables` fixture issues
`TRUNCATE gate.login_attempts, gate.user_sessions, gate.users RESTART
IDENTITY CASCADE` before every test.

| | |
|---|---|
| Service | `Postgres-CWDF`, id `f96cc37b-6904-4554-9d3b-ae0a1385885c` |
| Database | `michael_gate_test` (PostgreSQL 18.6) |
| Public endpoint | `iriguchi.proxy.rlwy.net:37258` (TCP proxy) |
| Production Postgres — NOT this one | id `0703ec04-bcf9-484d-9dc7-9fca111dc9b2` |

```bash
V=$(railway variables --service Postgres-CWDF --environment production --json)
URL=$(printf '%s' "$V" | python -c "
import json,sys
d=json.load(sys.stdin)
print(f\"postgresql://{d['POSTGRES_USER']}:{d['POSTGRES_PASSWORD']}@{d['RAILWAY_TCP_PROXY_DOMAIN']}:{d['RAILWAY_TCP_PROXY_PORT']}/{d['POSTGRES_DB']}\")
")
MICHAEL_DATABASE_URL="$URL" uv run pytest tests/gate -m integration -q
```

`tests/conftest.py` sets `MICHAEL_DATABASE_URL` with `os.environ.setdefault`,
so an exported value wins and no file needs editing. Expect roughly seven
minutes: `michael.db.writable()` opens a fresh unpooled connection per call,
and over the public internet with SSL that dominates the runtime.

### What has been verified, and when

**2026-09-23 — two consecutive clean runs**, back to back against the same
database with no manual cleanup between them:

| run | result | duration |
|---|---|---|
| 1 | 20 passed, 131 deselected | 433.78s |
| 2 | 20 passed, 131 deselected | 450.96s |

The first execution of these tests against any live database. Two runs
matter rather than one: the suite was previously non-idempotent — the same
fixed-email user was created in every test against a `NOT NULL UNIQUE`
column, so a second run could not have passed. Two clean runs is the
evidence that the `gate_tables` truncation fixture actually works.

The headline result: `test_recent_attempts_excludes_an_attempt_older_than_
the_window` **passes**. `michael.gate.users.recent_attempts` binds a Python
`datetime.timedelta` (`ratelimit.WINDOW`) as the `%s` in `now() - %s`, and
psycopg3's adaptation of it to a Postgres `interval` was the largest
unverified assumption in this build — the entire login and rate-limit path
sits on that one query, and a wrong adaptation fails silently rather than
crashing. It is now evidence. `test_five_in_window_failures_lock_out_
through_the_real_query` pins the opposite direction, so a predicate matching
every row or no row would fail.

Two fixes also proved themselves under real conditions: the `gate_tables`
truncation fixture (no `UniqueViolation` across 20 tests — the defect that
previously made this suite unpassable) and `apply_gate_schema`'s `pg_roles`
guard (`michael_ro` does not exist on the test database; without the guard
the DDL would have rolled back and every test would have errored).

**Re-run both files and confirm they pass before Step 6.** The code has
changed since; passing once is not a standing guarantee.

## Precondition — verify the client address is real

`michael.gate.app.client_address` derives the rate-limit key from the
`X-Real-IP` header. Whether that is the caller's own address cannot be settled
by the test suite: `TestClient` verifies the parsing rule, not Railway's
behaviour, so the header has to be checked against production.

Two ways it can be wrong, and both matter:

- the value is shared infrastructure rather than the caller → `ADDRESS_LIMIT`
  becomes a near-global counter, and failed logins from anyone lock out
  accounts for everyone;
- the value is caller-supplied → the per-address limit is bypassable by
  sending a header.

**Done — 2026-09-23. The check found a real defect, which is the argument for
running it before the domain moves rather than after.**

The first implementation read the rightmost `X-Forwarded-For` hop. Railway
does not document XFF at all; its published header spec names **`X-Real-IP`**
as the header "for identifying client's remote IP". The XFF that arrives
carries Railway's own internal chain, so the rightmost hop is an edge POP —
one of a handful of `152.233.33.x` addresses shared by every caller on the
internet. That is the global lockout the fix was meant to remove, spread over
three buckets instead of one.

Measured, not argued. Five probes from a machine whose real public address is
`124.148.255.130`:

| probe | headers sent | address recorded |
|---|---|---|
| a | none (XFF implementation) | `152.233.33.164` — edge |
| b | spoofed `X-Forwarded-For` (XFF impl.) | `152.233.33.162` — edge |
| c | none (`X-Real-IP` implementation) | `124.148.255.130` — real |
| d | spoofed `X-Real-IP: 203.0.113.99` | `124.148.255.130` — spoof ignored |
| e | both headers spoofed | `124.148.255.130` — both ignored |

Both halves therefore hold: the recorded value is the caller's own address,
so two callers on different networks cannot share a rate-limit bucket; and
Railway's edge overwrites a client-supplied `X-Real-IP`, so the value is not
attacker-controlled.

**Re-run this after any change to `client_address`, and after any Railway
edge change.** From two different networks submit one failed login each, then
against the production database:

```sql
SELECT DISTINCT address FROM gate.login_attempts
WHERE at > now() - interval '15 minutes';
```

Two distinct values, each matching the real client IP, means it still holds.
One value, or an address in a private range, means it does not — stop.

## Rollback

If anything below goes wrong after the domain is removed: re-generate the
`michael-hermes` service's Railway domain (Railway project settings →
`michael-hermes` service → Networking → Generate Domain). This restores
direct public access to the Hermes dashboard while the gate issue is
diagnosed. The gate service itself does not need to be torn down to do this
— the two domains can coexist.

## Procedure

1. **Create the `michael-gate` service.**
   In Railway project `michael` (id `693389ce-128e-469f-ab3b-81901cdc4d8a`),
   production environment `e1752aeb-18db-4cdb-ad6b-ff7bcbdb932f`, create a
   new service named `michael-gate` built from `gate/Dockerfile` at the
   repository root (build context: repo root, not `gate/`, since the
   Dockerfile's `COPY` lines are written relative to the repo root).

2. **Set its five variables.**

   ```
   GATE_SECRET=<64 random hex characters>
   HERMES_BASE_URL=http://michael-hermes.railway.internal:9119
   HERMES_USERNAME=michael
   HERMES_PASSWORD=<the michael-hermes dashboard/basic-auth password>
   MICHAEL_DATABASE_URL=<the production read/write Postgres URL>
   ```

   `MICHAEL_DATABASE_URL` is required by every gate route that touches an
   account or a session (login, the WebSocket relay's ownership check) — not
   just by Step 3's `gate-schema` command below. Without it, the container
   starts cleanly, serves `/login`, and 500s on the first real login attempt.

   Generate `GATE_SECRET` with, e.g., `openssl rand -hex 32`. It signs
   session cookies (see `michael/gate/cookies.py`). **Rotating it
   invalidates every existing session** — every signed-in user is logged
   out and must sign in again. Treat it like any other credential: do not
   commit it, do not log it, do not paste it anywhere but the Railway
   variable editor.

3. **Apply the gate schema to the production database.**
   **Note:** `uv run michael schema` applies only the main retrieval schema
   (`michael.schema.apply_schema`, via `michael/cli.py`'s `schema`
   subcommand). It does **not** create the `gate` schema. Use the dedicated
   subcommand instead, with `MICHAEL_DATABASE_URL` set to the production
   read/write URL:

   ```bash
   uv run michael gate-schema
   ```

   This must run before Step 4 (`gate.users` must exist first) and creates
   `gate.users`, `gate.user_sessions`, `gate.login_attempts`, and the REVOKEs
   that keep `michael_ro` off the `gate` schema.

4. **Create an admin account.**

   ```bash
   uv run michael user add <you@example.com> --name "<Your Name>" --role admin
   ```

   (The CLI prompts for the password twice, interactively; it is never
   accepted as an argv argument, so it never lands in shell history.)

5. **Verify sign-in AND one complete end-to-end answer through the gate.**
   Using the new `michael-gate` service's Railway-provided URL (not yet the
   custom domain): sign in as the admin account just created, then ask one
   real question and confirm a complete answer streams back through
   `/api/ws`. This exercises the full path this runbook is worried about —
   login, rate-limit check, the WebSocket relay, and the upstream Hermes
   call — before anything is made irreversible.

   **This step's evidence does not extend to admin capability, because there
   is none to test.** `role` (`admin` vs `chat`) is minted into the cookie and
   stored in `gate.users`, and that is *all* it currently does — it is
   checked in zero authorization decisions anywhere in this codebase. Signing
   in "as admin" and asking a question here exercises exactly the same CHAT
   path a `chat`-role account would take; nothing about this step is
   admin-specific, and nothing in the gate distinguishes the two roles at
   runtime. If a future plan's Step 6 (or any later step) is conditioned on
   "a working admin account has signed in and done admin things," this step
   does not provide that evidence — do not read it as though it does.

6. **Only then remove the `michael-hermes` service domain
   `michael-hermes-production.up.railway.app`.**
   Do not do this until Step 5 has actually succeeded. Removing the public
   Hermes domain before a working admin account has signed in through the
   gate and received a complete answer leaves the Hermes dashboard
   unreachable from anywhere but the Railway private network, with no
   confirmed way back in through the gate. If Step 5 is not yet working,
   stop here and fix it — do not remove the domain "to see if it helps."

7. **Verify the gate still answers and the old domain is gone.**
   Confirm the `michael-gate` domain (and/or `michael-hermes-production.up.railway.app`
   once repointed, per however the custom domain is assigned) answers sign-in
   and chat as in Step 5, and confirm
   `https://michael-hermes-production.up.railway.app` no longer resolves to
   the Hermes service (e.g. it 404s at the Railway edge, or DNS/proxy no
   longer routes there).

## What is deliberately not done here

Administrator access to the Hermes dashboard after cutover is expected to go
over Railway's private network (`michael-hermes.railway.internal`), not
through `michael-gate` — the gate's UI is the chat page, not the dashboard.
This is a known, accepted gap for a later plan, not an oversight in this
one.

## Known residual gap — disabling an account has a lag

`michael user disable <email>` takes effect immediately for anyone signing in
afterward, and the gate now also re-checks live account state whenever a
WebSocket is opened (`michael.gate.app.relay`), so a disabled account cannot
open a NEW `/api/ws` connection even on a cookie minted before it was
disabled. Two gaps remain, both accepted rather than fixed here:

- A plain HTTP request to `/` (the chat page shell itself) still trusts the
  cookie alone, with no re-check. A disabled account's existing cookie
  continues to load that page — though it cannot open a working socket —
  until the cookie's own 12-hour lifetime (`michael.gate.cookies.LIFETIME`)
  expires.
- A WebSocket that was already open at the moment an account is disabled is
  not re-checked again for the rest of that connection. Disabling an account
  stops its NEXT connection attempt, not a conversation already in progress.

If an account must be cut off immediately and completely (compromised
credential, urgent access removal), the reliable action is rotating
`GATE_SECRET`, which invalidates every session for every user at once — a
blunter tool, used deliberately for that reason.

## The administrator role

`role` is enforced. `gate.users.role` is `CHECK`-constrained to `'admin'` or
`'chat'`, carried in the session cookie, and read by the gate's administrative
routes:

| Route | Who |
|---|---|
| `GET /admin` | admin only — the accounts console |
| `POST /admin/users/enabled` | admin only — disable or re-enable one account |

A signed-in **chat** user gets `404`, not `403`: telling them the route exists
and is forbidden discloses the shape of the administrative surface to exactly
the population the gate exists to keep away from it. An anonymous caller is
redirected to `/login`. The role is trusted only because the cookie is signed
— editing `chat` to `admin` in a cookie fails signature verification and the
caller is treated as anonymous, which is asserted by test.

An administrator cannot disable their own account. The console would
otherwise offer a one-click way to lock the last door from the inside, and
re-opening it needs shell access to the container.

**What the role still does NOT grant: access to the Hermes dashboard.** After
Step 6 the dashboard is reachable only on Railway's private network. If you
need it, the recovery path is the Rollback section above — re-generate the
`michael-hermes` domain from Railway's own control plane, which takes about
ten seconds and does not depend on this service being healthy. That is the
real escape hatch, and it is why dashboard proxying was not built into the
gate: it would re-expose, to a signed-in administrator over the public
internet, the whole surface this service exists to close.

So Step 5 should now exercise both paths: sign in as the admin account, ask a
question, **and** open `/admin` and confirm the accounts list renders.

