# The test-fix-retest loop

A standing cycle between the TESTER and the ORCHESTRATOR. It has no natural
end; the owner stops it.

```
TESTER runs scenarios against production
   -> writes .orca/reports/<date>-tester-<round>.md
   -> every finding gets an ID:  T<round>-<n>
        |
ORCHESTRATOR reads the report, triages every finding
   -> CONFIRMED / NOT A DEFECT / ALREADY KNOWN / OWNER DECISION
   -> writes .orca/reports/<date>-triage-round<n>.md
        |
ORCHESTRATOR plans the fixes and dispatches them to the owning worker
   -> WORKER-1 ingestion, WORKER-2 retrieval, WORKER-3 drafting, WORKER-4 platform
   -> each fix carries a failing test first, and the measurement that proves it
        |
ORCHESTRATOR accepts or rejects each fix, by reading the diff and re-running
   -> writes .orca/reports/<date>-fixes-round<n>.md, closing each T-ID
        |
ORCHESTRATOR sends that report to the TESTER
        |
TESTER writes NEW scenarios aimed at the fixes
   -> regression: does the reported defect stay fixed
   -> adjacent: what did the fix's mechanism newly make possible
   -> round n+1 begins
```

## Rules that keep the loop honest

**The tester never fixes.** Its report is the evidence. A tester that repairs
what it finds destroys the only record of what the deployed system did.

**The orchestrator never marks its own homework.** A fix is accepted by
reading the diff and re-running the measurement, not by a worker's claim.

**Every finding gets an ID and a disposition.** A finding that is not a defect
is closed with the reason, never silently dropped. "NOT A DEFECT" is a valid
outcome and must be argued, because an unargued dismissal is how a real bug
becomes folklore.

**A fix is not done when the test passes.** It is done when the measurement
that exposed the defect is re-run and reported. This project has shipped a
green suite that cut 13% of the corpus.

**New scenarios must attack the fix's mechanism, not repeat the old input.**
Re-running the same question only proves the specific case. The question worth
asking is what the fix newly made possible.

**Owner-only findings stop the loop for that item.** Anything that deletes
data, changes `MICHAEL.md`, pushes, or spends money goes to the owner and
waits. The loop continues around it.

## What each round records

The round number, the deployed version tested - prompt sha256 and corpus
counts, gathered rather than assumed - every finding with its ID and
disposition, every fix with its measurement, and what the next round will
attack.

A round that finds nothing is a valid round. It is recorded as such, with the
scenarios that found nothing listed, so the next round does not repeat them.
