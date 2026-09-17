# Plan review — contract comparison and completeness

Date: 2026-09-17
Reviewer: ORCHESTRATOR
Reviewing: `docs/superpowers/plans/2026-09-17-contract-comparison.md`
and `docs/superpowers/specs/2026-09-17-contract-comparison-design.md`

Read before dispatch, as the record of what was wrong with the plan and what
was changed before any worker touched it. Five findings, all accepted by the
owner.

---

## 1. The definition of done contains a criterion no worker can satisfy

**Finding.** "Definition of done" requires *"The live Railway config shows five
tools included and four excluded."*

Railway deploys from `main` on push. **Push is the owner's alone.** No worker
can make that true, so the plan would close with that box unticked through no
fault of the workers — or, worse, with a worker asserting it on the strength of
the repo rather than the deployment.

**Why it matters beyond this plan.** This is the same shape as the endnote
regression of 2026-09-17: an acceptance criterion was written that could only be
verified by a full rebuild, which is an owner-only operator action. The worker
proved what it could with fixtures, reported green in good faith, and the
criterion proved nothing while 1,199 provisions were lost. The rule that came
out of that is section 5a of `.orca/ORCHESTRATOR.md`: *an acceptance criterion a
worker cannot test is the orchestrator's error, not the worker's.*

**Change.** The live-deployment check is split out of the workers' definition of
done. WORKER-4 labels its Task 9 result **UNVERIFIED AGAINST THE LIVE
DEPLOYMENT** and states what it expects the deployed config to show. The
verification is sequenced after the owner pushes.

---

## 2. Task 3's fixtures undercut Task 3's own purpose

**Finding.** The spec requires fixtures *"each from a real contract, not
invented"*. The plan's Task 3 Step 1 instead specifies:

- `templates/employment/casual_employment_contract.md` — this repository's own
  template;
- *"one contract from `sources/` if present"* — **there is none.** `sources/`
  holds legislation only: the Fair Work Act volumes and
  `www.legislation.gov.au`. That leg yields nothing;
- *"one synthetic-but-realistic third-party contract written by hand"* —
  written by the same person running the test.

So the "real-document proof" would rest on one in-house template plus one
self-authored document.

**Why it matters.** Task 3 exists precisely because author-written fixtures
prove nothing: five green hand-cut fixtures hid a 13% corpus loss. A fixture
written by the person testing the splitter shares that person's assumptions
about what a contract looks like, and that assumption is the thing under test.
Fixtures drawn from one drafting house prove a splitter works on one drafting
house.

**Change.** The third document must be a genuine third-party contract in a
numbering style nobody on this project chose — a published standard-form or
government template. If WORKER-3 cannot source one, it reports that plainly
rather than hand-writing a substitute.

---

## 3. A stated dependency is wrong

**Finding.** The plan and the dispatch brief both state *"Task 10 depends on
4."*

`normalise()` is produced by Task 4. But `SIMILARITY_THRESHOLD` is defined in
**Task 5** (Alignment and the comparison report), and Task 10 both imports it
and rewrites its comment in Step 4.

**Change. Task 10 depends on Task 5.** Dispatching it after Task 4 would send
WORKER-2 at a constant that does not yet exist.

---

## 4. Two stale task pointers survive a renumber, one inside a protected comment

**Finding.**

- Plan line 50: *"the guard test in Task 8 forbids any answering-path handler
  importing `michael.ingest`"* — the guard test is **Task 9**.
- The `SIMILARITY_THRESHOLD` comment written in Task 5: *"A GUESS. Task 9
  measures it against a labelled set"* — **Task 10** measures it. Task 9 is MCP
  registration.

**Why it matters.** The second pointer sits inside the very comment that must be
protected from quiet deletion. It misdirects to the wrong task, and WORKER-4
reading it during Task 9 could reasonably conclude the measurement is its job.

**Change.** Pointers corrected to Task 10 in the dispatches and in the committed
comment. The words "A GUESS" are not touched.

---

## 5. The threshold inherits a target that does not transfer

**Finding.** Task 10 copies the rule used for `RETRIEVAL_MIN_SCORE`: *take the
lowest threshold with zero false positives.* That rule does not transfer, and
the reason is about which failures are visible.

**For retrieval, a false positive is silent.** Michael answers from a corpus
that cannot answer the question, and nothing on the page tells the reader
anything went wrong. When the failure is invisible, zero false positives is the
only defensible target.

**For clause similarity, neither direction is silent:**

- threshold too **low** — two unrelated clauses are paired and a nonsense diff
  is printed. Visible.
- threshold too **high** — a real pairing is missed and the clause is reported
  as ADDED plus REMOVED. Verbose, but visible, and true.

So this is a **readability parameter, not a safety parameter**, and the
retrieval rule optimises for the wrong thing.

**Change — the replacement target.**

> Maximise correct pairings across the labelled set. Report **both error counts
> separately** at every threshold — wrong pairings and missed pairings — never a
> single score that hides which is which. Where two thresholds tie, **prefer the
> higher one.**

The tie-break is not arbitrary. A wrong pairing prints a diff between two
clauses that were never versions of each other, and a reader can mistake that
for an edit that actually happened. A missed pairing prints ADDED and REMOVED:
verbose but true. **Prefer verbose and true.**

WORKER-2 must state in its report, in its own words, which failure it optimised
for and why. If it reports "lowest threshold with zero false positives" it has
imported the rule without examining it and the task is not done.

---

## Carried into the dispatches, on the owner's instruction

**Task 2 — a failure mode the plan implies but never names.** The `CAPS_CLAUSE`
pattern will match an all-capitals line *inside* a clause body — a defined term
in capitals, a heading inside a schedule. That produces a spurious clause rather
than a lost one, so it fails in the safe direction, but Task 3's evidence is
where it surfaces. WORKER-3 is told to look for it specifically rather than
discover it.

**Task 8 — existing outline tests will fail on exact text.** The outline gets
materially longer. Those assertions are to be **UPDATED, never weakened.** A
worker that deletes an assertion rather than correcting its expected string has
failed the task and it is rejected.
