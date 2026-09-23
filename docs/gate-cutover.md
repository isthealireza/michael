# michael-gate cutover runbook

This is the procedure for standing up `michael-gate` in Railway and moving
the public domain from `michael-hermes` to it. It is written to be followed
literally, in order. The order is not advisory: skipping ahead can leave the
Hermes dashboard unreachable (see Step 6).

## Precondition — read this before starting

At the time of writing, **the database-backed gate code has never been run
against a real Postgres.** `tests/gate/test_users.py` and
`tests/gate/test_sessions.py` are both marked `pytestmark =
pytest.mark.integration` and are excluded from the default `uv run pytest`
run (see `pyproject.toml`'s `addopts`, which passes `-m "not integration and
not network"`). No Postgres was reachable while this task was done, so those
two files have never executed, not even once, against a live database.

In particular: `michael.gate.users.recent_attempts` builds the query

```sql
SELECT account_key, address, at, outcome FROM gate.login_attempts
WHERE at > now() - %s AND (account_key = lower(%s) OR address = %s)
```

and binds a Python `datetime.timedelta` (`ratelimit.WINDOW`, 15 minutes)
directly as the `%s` for `now() - %s`. psycopg3 is expected to adapt a
`timedelta` to a Postgres `interval` and produce the intended "attempts in
the last 15 minutes" query, but **this has not been verified against a real
server.** If the adaptation is wrong — wrong type, wrong sign, silent cast
failure — the failure mode is not a crash, it is silent: `recent_attempts`
would return the wrong window (possibly all rows, possibly none), which
feeds directly into `is_locked_out`. The entire login and rate-limit path
(`_authenticate` in `app.py`) sits on top of this one unverified query.

**Do not proceed past Step 6 (removing the `michael-hermes` domain) until
`uv run pytest -m integration` has been run and has passed against a real
Postgres instance carrying the `gate` schema.** Passing the default,
non-integration suite is not sufficient evidence that login or rate limiting
works.

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

## Known gap — the "admin" role enforces nothing

Say this plainly, because it is easy to read the code and assume otherwise:
**`role` is decorative.** `gate.users.role` is `CHECK`-constrained to
`'admin'` or `'chat'` at the database level, it is carried in `User`, and it
is minted into the session cookie by `michael.gate.cookies.mint`. That is the
entire extent of what it does. It is not read, checked, or branched on by any
route, any authorization decision, or any part of `michael.gate.app` — an
`admin` cookie and a `chat` cookie authorize identically everywhere in this
service. There is no admin-only capability in `michael-gate` for the role to
gate access to.

Consequently: **Step 5 above proves an admin account can chat. It proves
nothing about administration**, because there is no administrative surface
here to exercise — no user management, no session management, no dashboard
access — through the gate. If a future plan treats "an admin account signed
in successfully" as evidence that admin-specific access controls work, that
plan's premise is false and must be corrected before it is acted on, not
worked around by adding checks to this runbook.

Designing and implementing real authorization for `admin` is out of scope
for a fix wave and deliberately not attempted here (see the review this
document responds to, finding I-2). Anyone relying on `role` for anything
beyond bookkeeping should treat that as an open design task, not a shipped
control.
