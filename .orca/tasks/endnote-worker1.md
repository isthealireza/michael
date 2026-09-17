FIX THE ENDNOTE JUNK — this is now the CRITICAL PATH. You are WORKER-1.
Not a proposal. A fix, with tests.

Read .orca/WORKER.md, .orca/WORKER-1.md, MICHAEL.md, .orca/RAILWAY.md.

## Your stand-down report was accepted. Your classification test is the seed.
Your density rule — a sustained run of 5+ consecutive SECTION_RE matches each
separated by under ~300 chars is a table, against a ~1,406-char median gap for
real sections — is good work and you will reuse it here. So is your honest
caveat that it can sweep in a short real section sitting next to where a table
begins. That caveat is now a hard requirement, see MUST-SURVIVE below.

## Why this is blocking, measured by the owner on the real rebuild

The local rebuild is DONE. Keyed on citation AND sha256 so a metadata change
cannot hide a content change:
  documents 205 -> 205, zero missing, zero spurious
  provisions 9111 -> 9303
  **192 recovered, 0 lost, 70 documents affected — a third of the corpus**
  headings ending in a cited year: 2 -> 108
  provisions under 80 chars: 367 -> 370; under 200: 1314 -> 1334
    (so the recovered content is substantive: 192 added, only 3 of them short)
  **duplicate pinpoint groups: 618 -> 653, plus 35**

Those +35 are not a new defect. They are YOUR endnote defect, amplified by the
contents fix. The contents fix is right; the endnote rule is its missing half.

Worked example, and this is your test case. In **Bank of Western Australia Act
1995 (WA)**, section 1 now resolves to THREE provisions:
  - `Short title` at char 4916                      <- REAL, MUST STAY
  - `Provision relating to Industry (Advances) Act 1947` at char 48850  <- GO
  - `The provisions in this Act amending...` at char 53762              <- GO
Both of the later two sit in the endnote / amendment-history region. They end
in a cited year, so the fixed predicate correctly stopped treating them as
contents rows — and nothing else rejects them, so they are stored.

**One section number resolving to three provisions produces three competing
pinpoints for one citation.** That is the traceability failure this whole
project exists to prevent. That is why this is ahead of recalibration.

## ACCEPTANCE CRITERIA — the owner measured these; they are the test

1. **Duplicate pinpoint groups must fall WELL BELOW 618** — not merely below
   653. 618 was already a defect; matching it is not success. Report the
   number you achieve.
2. **Provisions must not drop below roughly 9303 minus the junk you remove.**
   State exactly how many you removed, and show WORKED EXAMPLES of each class
   you rejected, quoted from the real corpus.
3. **The three Bank of Western Australia Act 1995 (WA) section 1 rows are the
   worked example to fix:** `Short title` at char 4916 stays; the two past
   char 46000 go. Demonstrate it.
4. **MUST SURVIVE — these are genuine operative sections, real recovered law,
   not junk:**
       `Application of Fair Trading Act 2010`
       `Relationship with Local Government Act 1995`
   If your rule drops either, the rule is wrong. This is exactly the edge your
   own caveat predicted, so guard it deliberately.
5. **A rule about what the structure MEANS.** Not a special case, not a
   hardcoded Act or heading.

## On criterion 5, a disambiguation so you do not over-correct
The owner said "not a character-offset threshold". That forbids an ABSOLUTE
offset rule — "reject anything past char 46000" — which would be a magic
number tied to one document's length and would break on every other Act.

It does NOT forbid a structural rule that happens to measure distance
RELATIVELY. Your density/clustering rule is structural: it describes what a
table IS — dense, sustained, uniform — against what body text is. The accepted
contents fix works the same way, reasoning about neighbouring lines. That
shape is the approved one.

Prior art worth considering, though the design is yours: `find_body_start`
already locates a structural boundary at the FRONT of a document, and
`CONTENTS_MIN_ENTRIES` already encodes a density idea. A symmetric notion of
where the operative body ENDS may be the honest answer, or the density rule
may be, or both. Argue your choice.

## Deliverables
- The diff, exactly as applied.
- The rule in prose, and why it is structural rather than a special case.
- A test fixture carrying REAL examples of BOTH what must be dropped AND what
  must be kept — including all three Bank of WA rows and both must-survive
  headings above. Source every string from a real document and name it.
- The before/after numbers: duplicate pinpoint groups, total provisions, and
  how many rows you removed by class.
- An explicit regression check: does anything genuine get dropped? Your own
  caveat says this is where the risk lives. Check it; do not assume zero.

## Constraints — these bind you
- **Do not re-ingest, purge, TRUNCATE, seed or write to the corpus.** The
  owner runs rebuilds; he will rebuild once more when your fix lands. You may
  READ the corpus freely.
- **Do not delete data.** Anything that deletes escalates to me, then him.
- **Do not restore, replay or read-and-rewrite anything in `backups/`.**
  Those are the owner's.
- Do not touch retrieval scoring, thresholds, `calibration/` or `bench/` —
  WORKER-2's ground, currently held.
- **WORKER-4 has concurrent edits in `src/michael/ingest.py`** (the ingestion
  file-log fix, already reported and under my review). You are both in that
  file. Do not revert or restructure its logging changes; work around them,
  and tell me immediately if you cannot.
- MICHAEL.md is read-only to you. Propose wording to me instead.
- `uv run pytest` and `uv run mypy .` green BEFORE you report done. Note:
  there are 7 PRE-EXISTING mypy errors in the untracked `backups/compare.py`,
  which is the owner's own script and outside everyone's scope — do not fix
  it, do not count it against yourself, and do not touch `backups/`.
- No commit without my approval. No push — the owner's alone.
- Leave the repo root clean. Work in a temp directory. Never print a secret.

## Observable acceptance
Every number above, real, with the query or script shown. If you cannot get
duplicate pinpoint groups well below 618 without dropping genuine law, STOP
and report that tension rather than trading traceability for a number. Report
what you ran and what you saw. Never claim a result you did not verify.
