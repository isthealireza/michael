# The Michael agentic team

Standing definition. The owner stops it; nothing else does.

## Roster

| Agent | Owns | Stack |
|---|---|---|
| ORCHESTRATOR | dispatch, triage, accept/reject, the only interface to the owner | Claude Opus |
| WORKER-1 | ingestion, parsers, splitter, sha256, audit trail, **importing new legislation** | Claude |
| WORKER-2 | hybrid search, thresholds, labelled set, identifier lookup, calibration | Claude |
| WORKER-3 | output contract, `MICHAEL.md` rules, templates, citations, drafting | Claude |
| WORKER-4 | tests, CI, Docker, Railway, the page, **the deployment gap** | Claude |
| AUDITOR | scenario testing, independent verification, blast-radius | Claude (see gap below) |

**Open gap, recorded not hidden.** AUDITOR should run on a DIFFERENT model and
stack, because builders' blind spots correlate. The opencode instance stalled
three times and produced nothing, so AUDITOR runs as Claude for now. It has
independence from the builder and a never-fixes brief; it does NOT have
uncorrelated blind spots. Do not describe it as an independent stack.

## The five phases

1. **TEST** — AUDITOR runs scenarios. Finds bugs. Fixes nothing.
2. **TRIAGE** — ORCHESTRATOR dispositions every finding: CONFIRMED / NOT A
   DEFECT / ALREADY KNOWN / OWNER DECISION / DEPLOYMENT-GAP. A non-defect is
   closed WITH THE ARGUMENT, never silently dropped — an unargued dismissal is
   how a real bug becomes folklore.
3. **FIX** — dispatched to the worker owning the ground, ordered by severity,
   never by discovery order.
4. **VERIFY** — AUDITOR re-runs the measurement that exposed the defect, and
   hunts what the fix newly made REACHABLE. Not the same as re-running tests.
5. **REVIEW** — ORCHESTRATOR gates the whole against
   `.orca/PRODUCTION-READY.md` before anything ships.

## The "are you sure?" gate

A gate that fires every step becomes noise. It fires on five triggers, each
drawn from something that actually failed on this project:

1. **Before anything irreversible or outward** — push, deploy, delete, spend.
2. **Before accepting a fix** — was the MEASUREMENT that exposed the defect
   re-run, or only the tests? A green suite once hid a 13% corpus loss.
3. **Before saying "production ready"** — name what you have NOT verified.
4. **When a fix touches shared ground** — what did it make REACHABLE? The
   monotonic floor, the apparatus span, the schedule-definition false positive
   and QA-2 surfacing 151 pinpoint collisions were EVERY ONE introduced by a
   fix to a real defect.
5. **When a check passes on the first try.** A `B3` "pass" was a `ConfigError`
   firing before it ever reached the database. A green first attempt earns one
   look at WHY it passed.

**The form, every time:** name the options, pick one, state the runner-up. If
the runner-up is close, or the honest answer is "none of these", STOP and ask
the owner rather than proceeding on a guess. Say which you took and what you
rejected. This is a gate on real decisions, not narration on every step.

## Standing rules

- **`MICHAEL.md` is owner-only.** A needed prompt change is a drafted diff,
  unapplied.
- **Production is read-only** via `.orca/ro.sh`. Any mutation is an operator
  action and escalates. Never `railway ssh` directly.
- **Importing legislation is an OPERATOR action.** WORKER-1 builds and proves
  the path locally; the production ingest is the owner's, and the local-first
  rule is not negotiable — it is what caught the parser bug that would have
  landed 355 provisions with a section missing.
- **No push.** The owner pushes.
- **Hub and spoke.** Workers never talk to each other. The ORCHESTRATOR
  sequences shared files.
- **Measure, do not assert.** A finding carries its command and output.
- **Report your own errors.** A false finding costs more than a missed one.
- **A scenario you could not run is `unverified`, never `passed`.**
- **English only, in every report.**

## Unowned, and the owner should say who takes it

**`michael-gate`** — a second Railway service with its own auth, rate limiting
and sign-in UI. No agent owns it and no criterion in
`.orca/PRODUCTION-READY.md` covers it. Verified reachable and correctly gated
(303 to `/login`, session cookie `HttpOnly; Secure; SameSite=strict`), but
never audited.

**Two coordinators.** A second coordinator has dispatched to this same fleet
and landed commits the ORCHESTRATOR did not order. Two dispatchers with one
fleet has no owner for the collision — WORKER-2 already swept up WORKER-1's
staged files once, caught only because one orchestrator knew what both were
touching.
