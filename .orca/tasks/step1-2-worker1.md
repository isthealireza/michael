STEPS 1 AND 2 — fix the trailing-number predicate, then quantify the damage.
You are WORKER-1. Two deliverables, in order. No database write in either.

Read .orca/WORKER.md and .orca/WORKER-1.md, and MICHAEL.md.

## What you found, and what the owner found on top of it

Your Phase A report was accepted. Your 26WD finding was correct and I
reproduced it independently. The owner then reproduced it too and established
that it is NOT one section — it is a CLASS. Any section heading that ends in a
cited Act year is silently dropped, in every document, not only the Privacy
Act.

Confirmed by the owner against the real function in the project venv:

Currently True — WRONGLY DROPPED, must become False:
  1. "26WD Exception-notification under the My Health Records Act 2012"
     (also test the em-dash form: "26WD Exception—notification under the
      My Health Records Act 2012")
  2. "80P Disclosure under the Freedom of Information Act 1982"
  3. "7B Acts and practices of organisations 1988"

Currently False — CORRECTLY KEPT, must stay False:
  4. 26WC   ("26WC Deemed holding of information")
  5. 26WL   ("26WL Entity must notify eligible data breach")
  6. 6A
  7. 26WK
  8. 13G
  (For 6A, 26WK and 13G, source the exact heading text from the corpus
   sources and put the real strings in the fixture. Do not invent them.)

Currently True and CORRECT — must stay True:
  9. "15A Meaning of casual employee 68"   (a genuine contents entry)

## STEP 1 — fix the predicate

Target: `_is_contents_entry` in src/michael/ingest.py, and whatever else in
the splitter the fix properly belongs in.

The current rule is "the heading ends in a bare digit run, so it is a page
number". That is wrong because a cited statute title also ends in a number.

Requirements, all binding:

- **Do not special-case 26WD.** A named exception is not a fix.
- **Do not fix it with a single heuristic.** Specifically, do not simply add
  "a 4-digit number in 1900-2100 is a year, so ignore it" and call it done.
  That is one more brittle guess of the same kind as the bug.
- **It must be a rule about what a trailing number MEANS** — what makes a
  number a page reference as against part of the heading's own text. A
  contents entry's page number is metadata appended to a complete title; a
  cited year is a grammatical part of the title itself. Design the rule around
  that distinction. You own this ground; the design is yours. Use document
  context if that is what makes the rule sound, rather than looking at the
  string alone.
- **A test fixture carries every one of the nine headings above**, each with
  its expected verdict, plus any further real examples you find in the corpus
  sources while doing step 2. The fixture is the deliverable that proves the
  rule, so it holds real strings taken from real documents, not invented ones.
- Say plainly in your report what rule you implemented and why it is not a
  heuristic about years.

Note: changing the splitter changes the corpus, which voids the calibrated
RETRIEVAL_MIN_SCORE. That is expected and already planned for. **Do not
touch the threshold, calibration/ or bench/** — that is WORKER-2's ground and
a later stage.

## STEP 2 — quantify the damage. Read-only.

The existing 8,756-provision corpus was built with the broken predicate, so
it is already missing every section whose heading ends in a cited year. Nobody
knows how many.

With the fixed predicate, measure across the existing corpus SOURCES how many
headings would now be KEPT that are currently DROPPED.

Report:
- the total count of newly-kept headings;
- which documents are affected, and how many each loses today;
- the headings themselves, or a representative sample if the list is long;
- whether any currently-kept heading would now be dropped (a regression in
  the other direction) — check this explicitly, do not assume zero.

Constraints: **no database write, no ingest, no embedding call, no re-ingest.**
This is measurement only. Work against the source documents.

This number decides whether the existing 204 documents must be re-ingested.
**That decision is the owner's alone.** Do not re-ingest, do not recommend
acting without him, and do not touch the existing corpus rows. Deleting or
rebuilding corpus data needs his approval and you escalate to me for it.

## Constraints — these bind you
- No git push. No local commit without my approval.
- MICHAEL.md is read-only to you. Propose wording to me instead.
- Do not touch retrieval scoring, thresholds, calibration/ or bench/.
- Do not use the Railway MCP tools; their view of this account is a decoy.
  You need no Railway access at all here.
- Endnote/amendment-table junk provisions: OUT OF SCOPE. The owner agreed it
  becomes its own task. Do not fix it here even though you will see it again.
- Secrets stay in .env. Never print a key.

## Observable acceptance
1. The diff, exactly as applied.
2. The rule stated in prose, and why it is not a single heuristic.
3. The test fixture with all nine headings and their verdicts, and the test
   run showing each passes — paste the real output.
4. `uv run pytest` green — paste it. 149 tests currently pass and stay passing.
5. `uv run mypy .` clean — paste it.
6. The step 2 count, the affected documents, and the regression check.

Then STOP and report. Do not start Phase B; I gate it on accepting this.
Report what you ran and what you saw. Never claim a result you did not verify.
