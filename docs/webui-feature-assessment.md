# What to take from Hermes WebUI and Open WebUI — assessment

Date: 2026-09-23
Status: assessment, nothing implemented

Sources read: `github.com/nesquena/hermes-webui` README (feature list, `master`),
Open WebUI docs (`/features/`, `/features/chat-conversations/chat-features`).

## The constraint that sorts everything

Michael's gate allowlists **exactly four** JSON-RPC methods:

    session.create · session.resume · session.status · prompt.submit

with a per-method params key allowlist. That is the control the entire
"chat-only" guarantee rests on, and it is the only reason a reader cannot
enumerate another reader's sessions. So the useful question about any feature
is not "is it nice" but **what does it cost at that boundary**:

- **Free** — client-side only, or served from data the gate already holds.
- **A gate route** — new HTTP route on the gate, no change to the allowlist.
- **A wider allowlist** — needs a fifth upstream method. This is a security
  decision, not a feature decision, and should be argued on its own.
- **Incompatible** — depends on a Hermes toolset this deployment disables, or
  contradicts a safety property Michael is built around.

`hermes/config.yaml` disables `terminal`, `file`, `code_execution`,
`computer_use`, `skills`, `cronjob`, `delegation`, `browser`, `image_gen` and
`tts`. A large share of both products' feature lists is unreachable here by
design, not by omission.

---

## Free — worth doing

| Feature | Why it earns its place in a legal tool |
|---|---|
| **Copy answer / copy code block** | The actual workflow is getting a researched answer into a memo. Today that means selecting 3,000 characters by hand. |
| **Download transcript as Markdown** | Same reason, and it carries the citations and the closing notice with it. |
| **Stop button** | `michael.js` waits up to 300s. A reader who has spotted a bad question has no way out but a reload, which loses the turn. |
| **Message timestamps** | A research answer is evidence of what the corpus said *at a time*. The snapshot date covers the law; the timestamp covers the answer. |
| **Search within the conversation** | Long answers, and the reader is usually hunting a specific section number. |
| **Send-key preference** (Enter vs Ctrl+Enter) | Enter-to-send punishes anyone drafting a long factual question, which is the normal case here. |
| **Token/cost per turn** | `session.usage` frames **already arrive** on the socket — counted 3–4 per turn in production. The README costs Michael at $0.01171/run; surfacing it needs no new method, only rendering what is already relayed. |
| **Voice input** | Already scoped in the phase 2–4 plan. Self-contained, Web Speech API, no server surface. |
| **Streaming markdown** (tables, lists, code) | Already scoped. Must preserve citation chips and stay XSS-safe on model output. |

## A gate route — highest value of the lot

**Session history sidebar.** The gate has been recording this since Task 7 and
nothing reads it:

- `gate.user_sessions(user_id, hermes_session_id, title, created_at)` is
  written on every `session.create` and `session.resume` reply.
- `michael.gate.sessions.list_for_user()` is written, tested, and called by
  **no production code**.

So listing a reader's own past conversations costs one authenticated gate
route and no allowlist change. It is also the safest possible version of the
feature: the list comes from the gate's own ownership table, never from a
Hermes session-enumeration method, so it cannot leak another reader's
sessions even if the upstream would allow it.

**The open question, and it decides how much this is worth.** `session.resume`
restores the conversation *server-side*. It is not established that it replays
the prior transcript to the client — `web/michael.js` rebuilds the thread from
an empty page on every load. If resume does not return history, a sidebar
reopens a conversation whose context Michael still holds but whose text the
reader cannot see, which is worse than no sidebar. **Resolve this before
building**: resume a known session and inspect the frames.

If history is not replayed, the options are (a) show the list for continuity
of context only, labelled as such, (b) have the gate store transcripts itself
— a real decision, since it means the gate holds the legal questions and
answers rather than just who owns which session, or (c) widen the allowlist.

## A wider allowlist — argue separately, do not bundle

Each of these needs a fifth method and therefore a security argument:

- **Retry / regenerate the last answer** — plausible and genuinely useful when
  Michael returns NOT COVERED on a badly-phrased question.
- **Edit a past question and re-run from there** — Open WebUI's fork/branch.
- **Server-side cancel** — the free Stop button above only drops the client
  socket; the upstream turn keeps running and keeps costing.
- **Transcript fetch** — the dependency of the sidebar above.

None should be added quietly alongside a UI change. Widening the allowlist is
the one thing that makes the gate less true.

## Incompatible — and one that is actively wrong

Unreachable because the toolset is disabled: workspace file browser, shell
approval cards, subagent delegation cards, cron/Tasks panel, Skills panel,
image generation, TTS.

Contrary to Michael's design: multi-provider model dropdown (the model is
pinned by a 42-run benchmark; switching invalidates both the benchmark and the
cost figure), profiles (one by design), memory editor.

Needs a decision nobody has made: **public share links**. Hermes WebUI offers a
sanitized read-only transcript at a public URL. For legal research about real
matters this is a disclosure surface, not a convenience.

**Actively contrary to a safety property: collapsible thinking/reasoning
cards.** Hermes WebUI renders extended thinking in gold cards. `michael.js`
filters `reasoning.delta` and `thinking.delta` *on purpose* — its comment says
rendering them "shows the reasoning trace as though it were the advice." A
collapsible card still renders it, one click away, on a tool whose every
output must end with "not legal advice". Measured in production: **1,800
reasoning frames in a single turn**, none displayed. This is the one item on
either feature list that should be refused rather than deferred.

---

## Recommended order

1. Copy answer, download transcript, timestamps, Stop, in-conversation search
   — free, small, and they serve the actual memo-writing workflow.
2. Token/cost per turn — free, and the data is already on the wire.
3. Resolve the `session.resume` history question, then build the sidebar.
4. Streaming markdown and voice — already planned, unchanged.
5. Leave everything in the allowlist-widening section alone until someone
   wants one badly enough to argue for it on its own.
