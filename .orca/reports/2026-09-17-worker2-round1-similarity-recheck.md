# Round 1 — re-derive the similarity threshold on corrected ratios

Worker: WORKER-2 (Retrieval & Calibration Engineer)
Date: 2026-09-17

## What triggered this

Task 10's measurement (`0.74`) was taken while `contracts.py`'s
`SequenceMatcher` calls still used the default `autojunk=True`. That default
collapsed one real same-clause pair (266 characters, two numbers changed) from
a true ratio of 0.9925 to 0.1692 — I found this while measuring Task 10,
escalated it rather than editing WORKER-3's file, and the owner fixed it
directly as `760b74b`. This task re-runs the same measurement against the
corrected ratios to check whether `0.74` still holds.

I did not write the fix or the corrected `calibrate_similarity.py` — both were
already on disk, committed as part of `760b74b`, when this task started. I
read them, confirmed they do what the commit message says, and ran them.

## Which failure I optimised for (unchanged from Task 10, in my own words)

Still: **maximise correct pairings, report wrong pairings and missed pairings
as separate columns, and prefer the higher threshold on a tie.** The reason
does not change either — a wrong pairing prints a diff between two clauses
that were never versions of each other, which a reader can mistake for a real
edit; a missed pairing only prints ADDED plus REMOVED, which is more verbose
but never false. I did not revert to "lowest threshold with zero false
positives" and would not: that rule belongs to a failure that is silent
(retrieval answering from a corpus that cannot answer the question), and
neither direction of this threshold is silent.

## Sweep BEFORE the fix (0.74, defective ratios — from Task 10, reproduced for comparison)

```
SAME-CLAUSE (autojunk=True, the bug in effect)
  0.8580  clause 8.1 (Confidentiality)
  0.9871  clause 9.1 (WHS)
  0.9183  clause 10.2 (Termination - misconduct)
  0.9458  clause 11.1 (Entire agreement)
  0.1692  DISPUTE RESOLUTION            <- the pair the bug broke
  0.9961  Termination of Contract
  0.9948  INSURANCE (Continuing Obligation)
  0.9259  CONFIDENTIALITY
  0.9117  CONFLICT OF INTEREST
  0.8687  NO ASSIGNMENT
  0.9349  RELATIONSHIP
  0.8960  GOVERNMENT POLICY
  0.7460  Annexure, Governing law
  0.8211  Annexure, Notices
  0.9852  PETS
  0.9868  SECURITY BOND
  0.9901  RENT
  0.9730  NOTICES

DIFFERENT-CLAUSE (autojunk=True)
  0.5407  governing law            0.0433  notices
  0.4637  entire agreement         0.0339  pets
  0.3977  termination (2nd doc)    0.0276  rent
  0.3295  confidentiality          0.0228  security bond
  0.1162  liability                0.0225  severability
                                   0.0075  termination

SWEEP (key rows)
 thresh   wrong   missed   correct  / total
   0.55       0        1        28       29   <- plateau starts
   0.74       0        1        28       29   <- CHOSEN (highest tied)
   0.75       0        2        27       29

CHOSEN: 0.74 -- 28/29 correct (0 wrong, 1 missed)
highest different-clause ratio: 0.5407
lowest same-clause ratio: 0.1692 (the broken pair; 0.7460 if it is excluded)
```

## Sweep AFTER the fix (corrected ratios, `760b74b`, this task)

```
================================================================================
SAME-CLAUSE PAIRS (should score HIGH -- a missed pairing here is a false ADDED+REMOVED)
================================================================================
  0.8580  01_inhouse_casual_employment_contract.md clause 8.1 (Confidentiality)
  0.9871  01_inhouse_casual_employment_contract.md clause 9.1 (WHS)
  0.9183  01_inhouse_casual_employment_contract.md clause 10.2 (Termination - misconduct)
  0.9458  01_inhouse_casual_employment_contract.md clause 11.1 (Entire agreement)
  0.9925  02_wa_gov_general_conditions_consultancy_agreement.docx, DISPUTE RESOLUTION
  0.9961  02_wa_gov_general_conditions_consultancy_agreement.docx, Termination of Contract
  0.9948  02_wa_gov_general_conditions_consultancy_agreement.docx, INSURANCE (Continuing Obligation)
  0.9259  02_wa_gov_general_conditions_consultancy_agreement.docx, CONFIDENTIALITY
  0.9653  02_wa_gov_general_conditions_consultancy_agreement.docx, CONFLICT OF INTEREST
  0.8897  02_wa_gov_general_conditions_consultancy_agreement.docx, NO ASSIGNMENT
  0.9349  02_wa_gov_general_conditions_consultancy_agreement.docx, RELATIONSHIP
  0.8960  02_wa_gov_general_conditions_consultancy_agreement.docx, GOVERNMENT POLICY
  0.7460  02_wa_gov_general_conditions_consultancy_agreement.docx, Annexure A Deed of Novation, Governing law
  0.8211  02_wa_gov_general_conditions_consultancy_agreement.docx, Annexure A Deed of Novation, Notices
  0.9852  03_wa_gov_form1aa_residential_tenancy_agreement.docx, Part B, PETS
  0.9868  03_wa_gov_form1aa_residential_tenancy_agreement.docx, Part A, SECURITY BOND
  0.9901  03_wa_gov_form1aa_residential_tenancy_agreement.docx, Part B, RENT
  0.9730  03_wa_gov_form1aa_residential_tenancy_agreement.docx, Part B, NOTICES

================================================================================
DIFFERENT-CLAUSE PAIRS (should score LOW -- a wrong pairing here is a false CHANGED)
================================================================================
  0.5407  governing law, two contracts in the same file
  0.4637  entire agreement, two contracts in the same file
  0.3977  termination, different triggers, second contract
  0.3863  notices, two different contracts
  0.3713  severability, two contracts in the same file
  0.3512  termination, different triggers
  0.3295  confidentiality, two different contracts
  0.2942  pets, same contract, Part A summary vs Part B full clause
  0.2938  liability
  0.2893  security bond, same contract, amount clause vs release-procedure clause
  0.1889  rent, same contract, payment-method clause vs obligations clause

================================================================================
SWEEP -- wrong and missed pairings reported SEPARATELY, never a single hiding score
================================================================================
 thresh   wrong   missed   correct  / total
   0.30       7        0        22       29
   0.33       6        0        23       29
   0.36       5        0        24       29
   0.38       4        0        25       29
   0.39       3        0        26       29
   0.40       2        0        27       29
   0.47       1        0        28       29
   0.55       0        0        29       29   <- plateau starts, now CLEAN
   0.60       0        0        29       29
   0.74       0        0        29       29   <- CHOSEN (highest tied)
   0.75       0        1        28       29   <- plateau ends
   0.83       0        2        27       29
   0.86       0        3        26       29
   0.89       0        4        25       29
   0.90       0        5        24       29
   0.92       0        6        23       29
   0.93       0        7        22       29
   0.94       0        8        21       29
   0.95       0        9        20       29

================================================================================
CHOSEN: SIMILARITY_THRESHOLD = 0.74
  29/29 correct (0 wrong pairings, 0 missed pairings)
================================================================================
```

(Every threshold from 0.41 to 0.46 is 2 wrong/0 missed/27 correct, identical
to the 0.40 row; omitted here as redundant with the trend already shown. The
full untruncated 66-row table is reproduced verbatim by
`uv run python calibration/calibrate_similarity.py`.)

## Did any pair score worse after the fix? No.

I checked every same-clause pair before against after: 15 of 18 are
byte-identical (the fix only changes behaviour once `autojunk`'s heuristic
actually triggers), and the three that moved all moved **up** —
0.1692→0.9925, 0.9117→0.9653, 0.8687→0.8897. That matches exactly what the
owner's own pre-check said to expect (one missed pair fixed, two more raised,
none worse), so I did not stop and escalate; there is nothing here that
contradicts the fix's own claim.

## The threshold did not move — 0.74 before, 0.74 after

Both sweeps choose **0.74**. What changed is not the number but what stands
behind it: before the fix, the tied plateau (0.55–0.74) carried one missed
pairing throughout, an artefact of the one broken ratio (0.1692, which the
sweep could never place above any of these thresholds). After the fix, the
same plateau is **fully clean** — 29/29, zero wrong, zero missed, at every
point from 0.55 to 0.74. I have written this explicitly into the constant's
comment in `src/michael/contracts.py` (reproduced below) rather than leaving a
reader to guess whether `0.74` is a coincidence or a re-confirmed number.

## The margin — what happened to the highest different-clause score

This is the check that matters most: a fix that raises same-clause ratios
must not also raise different-clause ratios into collision with them.

**The highest different-clause ratio did not move: 0.5407, both before and
after.** ("governing law, two contracts in the same file" — the Applicable
Law clause in the consultancy agreement's own general provisions against the
Governing Law clause in its embedded Deed of Novation annexure; this pair was
already at 200+ characters and its own character-frequency profile did not
trigger the `autojunk` heuristic either way, so its ratio was never distorted
by the bug.)

Several *other* different-clause ratios did rise after the fix — the bug was
suppressing matches broadly, not only in the one pair that fell below
threshold — but none rose anywhere near the plateau: the highest is still
0.5407, comfortably under both the lowest same-clause ratio (0.7460, the
governing-law-in-the-annexure same-clause pair, unrelated to the different-
clause pair of the identical name — two separate entries in the labelled set)
and the chosen threshold (0.74).

**Margin: 0.7460 − 0.5407 = 0.2053** between the lowest same-clause score and
the highest different-clause score — a real gap, not a coincidence of one
threshold value. `0.74` itself sits 0.1993 above the highest different-clause
score and clear of every same-clause score at or above it.

## The constant and its comment

```python
#: Above this ratio, two clauses with different ids are taken to be the same
#: clause, edited. MEASURED 2026-09-17 against calibration/labelled_clause_pairs.json
#: (18 same-clause, 11 different-clause real pairs from tests/fixtures/contracts/),
#: RE-DERIVED 2026-09-17 after the autojunk fix (760b74b) to the same
#: SequenceMatcher calls this measurement uses: 0.74 is UNCHANGED - the
#: autojunk fix corrected one same-clause ratio from 0.1692 to 0.9925 and
#: raised several others, but the highest different-clause ratio held at
#: 0.5407, so the plateau of thresholds tied for the most correct pairings
#: widened from 0.55-0.74 (28/29, one missed pairing) to a clean 0.55-0.74
#: (29/29, zero wrong, zero missed) without moving its top edge. 0.74 is the
#: highest threshold that maximises correct pairings, tied with every value
#: from 0.55 through 0.74 and resolved toward the higher one, per
#: calibration/calibrate_similarity.py. This is a readability parameter, not
#: a safety parameter: unlike RETRIEVAL_MIN_SCORE, neither a threshold set too
#: low (a wrong pairing, printed as a false CHANGED) nor too high (a missed
#: pairing, printed as true but verbose ADDED+REMOVED) is silent, so the
#: target is maximising correct pairings, not zero false positives.
SIMILARITY_THRESHOLD = 0.74
```

Only this constant and its comment were touched in `src/michael/contracts.py`
(verified by `git diff --stat`: 1 file, +15/-8, all inside this docstring
block). `calibration/calibrate_similarity.py` and
`calibration/labelled_clause_pairs.json` were not modified by me this round —
the sweep script's own `autojunk=False` correction was already committed as
part of `760b74b` before this task started; I read it, confirmed it matches
what the commit claims, and ran it unmodified.

## Checks

```
uv run pytest -q
213 passed, 5 deselected in 8.20s

uv run mypy .
Success: no issues found in 45 source files
```

Repo footprint this round: `src/michael/contracts.py` only (the constant's
comment). No other file touched. Committed locally, not pushed.
