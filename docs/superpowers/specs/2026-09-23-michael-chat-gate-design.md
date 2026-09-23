# Per-user chat access to Michael — design

Date: 2026-09-23
Status: approved, not implemented

## Why

Two requirements, one boundary.

**Named people need to ask Michael legal questions.** Today there is exactly one
credential. `hermes/config.template.yaml` renders `dashboard.basic_auth` with a
single `username` and a single `password_hash`, and the Hermes gateway accepts
no second identity. Everyone who can reach Michael is the same person as far as
the system is concerned. There is no record of who asked what, and a credential
cannot be revoked from one reader without locking out all of them.

**That single credential is on the open internet.** The Railway project
`michael` exposes `michael-hermes-production.up.railway.app` with no custom
domain. Behind it sits the Hermes dashboard: sessions, settings, provider
configuration, and `/api/ws`. Whoever holds the shared password holds all of it.

The ask was for a reader who can *only* chat. That word — only — is the whole
design. It cannot be satisfied by choosing a nicer front end, because every
front end in play authenticates to the same all-or-nothing gateway.

## Scope

In scope: per-user accounts; a gate that is the sole public surface; restriction
of chat users to the four JSON-RPC methods a conversation needs; isolation of
each user's sessions from every other user's; and the merged chat interface
those users are given.

Out of scope, deliberately: self-serve password reset (an administrator resets
via CLI); SSO or federated identity; organisations, groups or hierarchical roles
beyond `admin` and `chat`; billing or usage quotas; and any change to how
Michael answers.

Also out of scope: Laya. It is a text classifier — an ~807 MB PyTorch checkpoint
that returns probabilities over described options — not a reasoning aid, and it
cannot be applied to an agent's own deliberation. The one honest place for it in
this project is a bench against the existing `classify_request` over
`domains.yaml`. Its tutorial states its thresholds have "not been calibrated or
validated for a real deployment", so adopting it into an answering path
optimised for correctness requires evidence this design does not gather. It
belongs in its own spike.

## Prior art: hermes-webui, and what was rejected

The request named `github.com/nesquena/hermes-webui` (MIT, Python plus vanilla
JavaScript). The repository is real, active and popular. Its **interface** is
worth taking. Its **backend is rejected**, on three findings from its own
documentation.

**It runs the full toolset, and says so.** `ARCHITECTURE.md:753` reads
`CLI_TOOLSETS = cfg.get('platform_toolsets', {}).get('cli', [...])`. Michael's
hardening lives under a different key — `agent.disabled_toolsets` — which
hermes-webui never reads. It would fall back to its hardcoded default:

    browser, clarify, code_execution, cronjob, delegation, file,
    image_gen, memory, session_search, skills, terminal, todo, tts, vision, web

That is every toolset `hermes/config.yaml` deliberately turns off.
`ARCHITECTURE.md:759` is explicit: "The web UI always runs with the full CLI
toolset. There is no per-session toolset restriction from the UI yet." Michael's
own config comment records that this failure has happened before — "a redeploy
silently restores the full toolset - which is exactly what happened to the web
chat once." Granting `terminal`, `file` and `code_execution` to a chat reader on
a host whose `config.yaml` carries an OpenRouter key and both Postgres passwords
in plaintext is not a risk to be mitigated. It is the opposite of the ask.

**It is single-user by construction.** `ARCHITECTURE.md:167` warns that the
per-request environment variables `HERMES_HOME`, `HERMES_SESSION_KEY` and
`TERMINAL_CWD` are process-global: "Two concurrent chat requests will clobber
each other. This is safe only for single-user, single-concurrent-request use."
The corresponding debt item TD1 is rated **Critical** and remains PARTIAL —
"Process-level env still written as fallback." Two readers asking questions at
the same moment is the ordinary case for this feature, and on that runtime it is
a confidentiality defect.

**It has no users and no roles.** Authentication is one shared password, or
passkeys, or OIDC with an `allow_claim` allowlist. All three are a door, not a
role: everyone admitted gets the workspace file browser with editing, Settings
including provider credentials, profiles, cron, skills and memory. The only
read-only surface is a share link — a sanitised static snapshot of one past
conversation, not live chat.

What is taken instead is the interface: the dark theme, the mobile layout, the
session sidebar pattern, streaming markdown, and voice input. These are
re-implemented against Michael's gateway, not copied. The frontend is roughly
6 MB of unbundled JavaScript in which the chat view is entangled with
`workspace.js`, `terminal.js`, `onboarding.js` and `panels.js`; lifting it
wholesale would mean maintaining a permanent 6 MB fork with no upstream path.

## What `web/michael.js` already gets right

The existing page is not a placeholder to be replaced. It filters
`reasoning.delta` and `thinking.delta` on purpose, because "rendering them shows
the reasoning trace as though it were the advice" — a substantive property for a
tool that must not appear to give legal advice. It renders pinpoint citations,
the `[MISSING]` rule and the closing practitioner notice. It opens one socket per
turn and gives each conversation its own titled session. hermes-webui's chat view
has no equivalent for any of this and would silently discard all of it.

The merged interface therefore *extends* this file. It does not replace it.

## Architecture

`michael-hermes` loses its public domain and becomes reachable only over
Railway's private network. A new service, `michael-gate`, becomes the sole public
surface. Without that move the gate is bypassed by addressing the Railway domain
directly with the shared credential, and every control below is theatre.

    Internet ──▶ michael-gate (public)          ──▶ michael-hermes (private only)
                  ├─ serves the merged chat UI       ├─ Hermes gateway + dashboard
                  ├─ /auth/*   login, logout         ├─ MCP: michael (4 tools)
                  ├─ /api/ws   proxy + filter        └─ agent.disabled_toolsets UNCHANGED
                  └─ holds the Hermes credential
                          │
                          └──▶ Postgres (schema: gate)

`michael-gate` is written in Python, matching the repository, and deployed as a
third Railway service in the existing `michael` project.

### The gate's three jobs

**Identity.** A `gate` schema in the existing Postgres, with no grant to
`michael_ro`, so the answering path cannot read credentials even by mistake —
the same reasoning that produced the read-only role. Passwords are hashed with
argon2id — not the scrypt already used for `dashboard.basic_auth`, because that
hash is provisioned once by an operator whereas these are user-chosen and
attacker-reachable. Sessions are signed HMAC cookies, `HttpOnly`, `Secure`,
`SameSite=Strict`, expiring 12 hours after issue and not renewed on activity, so
a stolen cookie has a bounded life. Login is rate-limited to 5 failures per
account per 15 minutes and 20 per address per 15 minutes.

**Method allowlist.** `/api/ws` is a JSON-RPC socket, and `web/michael.js`
records that `prompt.submit` on it is "the single server-side choke point every
dashboard submit passes through." A route-level proxy is therefore insufficient:
allowing the path allows every method the dashboard uses, including session
enumeration. The gate parses each client frame and permits exactly four methods:

    session.create · session.resume · session.status · prompt.submit

Any other method closes the socket and is logged. This control is what makes
"chat-only" a fact rather than a description.

**Session ownership.** `gate.user_sessions(user_id, hermes_session_id)` records
the owner on `session.create`, and every subsequent `session.resume`,
`session.status` and `prompt.submit` is checked against it. Without this,
per-user accounts are cosmetic: session identifiers are the only thing standing
between one reader and another reader's legal questions.

Upstream, the gate presents the single Hermes basic_auth credential. Chat users
never hold it.

### Data model

    gate.users          id · email · display_name · password_hash
                        role ('admin' | 'chat') · disabled_at · created_at
    gate.user_sessions  user_id · hermes_session_id · title · created_at
    gate.login_attempts account_key · address · at · outcome

Provisioning is `uv run michael user add | list | disable`, matching the shape of
the existing CLI.

### The interface

`web/michael.js` and `web/michael.html` move out of the Hermes image and are
served by the gate. That removes the `hermes/Dockerfile` asset-injection step
and, with it, the `?v=<hash>` cache-busting workaround that exists only because
the dashboard marks `/assets/` immutable for a year.

Retained unchanged: citation chips, `[MISSING]`, the closing notice, the
reasoning-trace filter, one socket per turn.

Added: the dark theme and mobile layout; a sidebar listing the signed-in user's
own sessions, sourced from `gate.user_sessions` rather than from any Hermes
session-listing method; streaming markdown for tables, lists and code, which must
preserve citation and notice rendering and must remain XSS-safe against model
output; and voice input via the Web Speech API.

## What must not change

`agent.disabled_toolsets`; the MCP `tools.include`/`exclude` allowlist; the
`michael_ro` read-only answering path; `MICHAEL.md`; the three closing blocks.
No part of hermes-webui's `api/` layer is imported.

## Risks accepted

**Hand-rolled internet-facing authentication.** A managed identity layer in front
was offered and declined; the gate is written in-house. Mitigations: argon2id,
per-account and per-address rate limiting, no self-serve reset, short cookie TTL,
and no rendering of user-supplied HTML anywhere in the gate. This component
warrants a security review before the domain is cut over.

**Credential store shares a database with the corpus.** Confined to the `gate`
schema with no grant to `michael_ro`.

**The domain cutover is one-way and removes administrator access.** The gate must
carry a working `admin` role before `michael-hermes`'s public domain is removed,
or the dashboard becomes unreachable.

## Testing

The gate's tests are the deliverable, not a formality. Every JSON-RPC method
outside the four is rejected. A user cannot resume, inspect or submit to a
session owned by another user. A forged or expired cookie is refused. Login rate
limiting triggers. An end-to-end test asserts that a `chat` user reaches no
dashboard route. `web/michael.test.js` is extended to cover the new rendering,
including that a `reasoning.delta` frame still renders nothing.

## Phases

1. `michael-gate`: accounts, method allowlist, session ownership, admin role.
   Serves today's `michael.html` unchanged. Domain cut over at the end.
2. Theme and mobile layout.
3. Session sidebar — depends on `gate.user_sessions` from phase 1.
4. Streaming markdown and voice input.

## Open items

Whether `session.status` is needed by the sidebar or only by the reconnect path,
which decides if it stays in the allowlist once phase 3 lands. Whether an
administrator reaching the Hermes dashboard does so through the gate or through a
Railway private-network route.
