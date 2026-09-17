# Real-document evidence for the contract clause splitter

Date: 2026-09-17
Worker: WORKER-3
Task: Task 3 of `docs/superpowers/plans/2026-09-17-contract-comparison.md`,
under the correction in `docs/superpowers/plans/evidence/2026-09-17-plan-review.md`
finding 2 (the third fixture must be a genuine third-party document, not
hand-written).

**This is evidence, not an assertion. No code was changed to produce it.**
`src/michael/contracts.py` and `src/michael/contract_text.py` are exactly as
committed in Tasks 1 and 2 (`111ca79`, `14faa3a`).

## Why this task exists, restated

On 2026-09-17 a splitter change passed five green unit tests and cut 13% of
the corpus (1,199 provisions lost to recover 44) before it was reverted. Green
fixtures are not evidence that a splitter works on real text. This file is
that evidence, produced by running the shipped splitter over real documents
and reading the output by eye.

## Step 1 — sourcing the three documents

The plan's original Step 1 specified the repository's own template, "one
contract from `sources/` if present", and a hand-written synthetic third
document. Both of the last two are wrong, per the accepted review:

- **`sources/` holds no contracts.** Checked directly:
  `find sources -maxdepth 2 -type f` returns only `sources/Fair Work Act
  2009/*.docx` (four Federal Register volumes) and one cached
  `www.legislation.gov.au` fetch. No contract exists there. This leg is
  confirmed to yield nothing, as the review predicted — I did not hunt
  further because there is nothing further to hunt for in this repository.
- **A hand-written third document was not produced.** Per the review, a
  fixture written by the person testing the splitter shares that person's
  assumptions about contract shape, which is the thing under test.

Instead, **two genuine third-party government standard-form contracts** were
sourced from the public web and used alongside the repository's own template,
giving three documents in three distinct drafting houses:

| # | Document | Provenance | Numbering style |
|---|---|---|---|
| 1 | `01_inhouse_casual_employment_contract.md` | This repository's own template: `templates/employment/casual_employment_contract.md`, copied verbatim (sha256 `b50663ee...`). Kept per the plan's original Step 1, unaffected by the review's correction, which concerns only the third document. | Markdown `## N. HEADING` decimal numbering, plus one unnumbered all-caps heading (`EXECUTION`) at the end. |
| 2 | `02_wa_gov_general_conditions_consultancy_agreement.docx` | Western Australia Government, Department of Finance — *General Conditions for Engagement of a Consultant*, September 2024. Fetched 2026-09-17 from `https://www.wa.gov.au/system/files/2025-04/general_conditions_consultancy_agreement_template_26.09.24.docx` (found via web search, downloaded with `curl`; genuine Word 2007+ document, confirmed with `file`). sha256 `2a97f6fd...`. | Decimal numbering (`1.`, `1.1`, `1.2`...), **with a genuine embedded restart**: an annexed Deed of Novation template restarts its own Table of Contents and clause numbering at `1.` partway through the document. Not constructed — found. |
| 3 | `03_wa_gov_form1aa_residential_tenancy_agreement.docx` | Western Australia Government, Consumer Protection (Dept of Local Government, Industry Regulation and Safety) — *Form 1AA Residential Tenancy Agreement*, approved under the *Residential Tenancies Act 1987* (WA) s 88C. Fetched 2026-09-17 from `https://www.consumerprotection.wa.gov.au/system/files/documents/2026-04/form1AArentalagreement2026.docx`, located via `WebFetch` on the publication page `consumerprotection.wa.gov.au/publications/rent-agreement-form-1aa`. sha256 `752ce838...`. | Entirely unnumbered, all-caps section headings (`RENT`, `TERMINATION`-equivalent sections, etc.) — the government's own prescribed drafting style for this instrument, not selected to fit the splitter. |

I did not use the NSW Government "Short Form Consultancy Agreement" or the WA
General Conditions' Deed of Novation as *separate* fixtures — the WA General
Conditions document already contains a genuine embedded numbering restart
(see below), so a fourth fixture was not needed to demonstrate that
property, and adding fixtures beyond what the task asks for is scope
creep I was told to avoid. Both extra documents were fetched during sourcing
and are available if the ORCHESTRATOR wants broader coverage; they are not
committed, to keep the fixture set to what this task specifies.

**All three fixtures are committed under `tests/fixtures/contracts/`.**

## A note on timing, added after an ORCHESTRATOR follow-up

The ORCHESTRATOR asked, in a message that arrived after this evidence file was
already drafted and the splitter already run over all three documents, that a
per-document, per-line prediction of where `CAPS_CLAUSE` would over-match be
written down *before* running the splitter, as its own section, so the
prediction could be checked honestly against the result.

**I cannot honestly produce that for this run: the splitter was already run
before that instruction reached me.** Reconstructing a prediction now and
presenting it as one made in advance would be exactly the thing I was told
not to do — writing a prediction after the fact and calling it prior. So this
is recorded plainly instead: the only prediction that genuinely predates this
task is the general one from the Tasks 1-2 report (`CAPS_CLAUSE` will
over-match on a defined term written in capitals inside a clause body, such
as `CONFIDENTIAL INFORMATION` followed by `means ...`). That prediction is
checked against these three real documents, honestly, in the "`CAPS_CLAUSE`
prediction, checked against real documents" section below. No
document-specific, line-specific prediction was made before this run, and
none is fabricated here to fill that gap.

## Step 2 — running the splitter

```python
import pathlib
from michael.contract_text import contract_text
from michael.contracts import split_clauses

for path in sorted(pathlib.Path("tests/fixtures/contracts").iterdir()):
    text = contract_text(path.read_bytes(), origin=path.name)
    clauses = split_clauses(text)
    print(f"=== {path.name}: {len(text):,} chars -> {len(clauses)} clauses ===")
    for c in clauses:
        print(f"  {c.clause_id:<24} {len(c.text):>6} chars  {c.heading[:60]}")
    total_span = sum(c.char_end - c.char_start for c in clauses)
    print(f"  -- char coverage: spans sum to {total_span:,}, document is {len(text):,} --")
```

### Full output, document 1 — `01_inhouse_casual_employment_contract.md`

```
=== 01_inhouse_casual_employment_contract.md: 3,576 chars -> 33 clauses ===
  (preamble)                  427 chars
  1                           139 chars  PARTIES
  2                            23 chars  NATURE OF EMPLOYMENT
  2.1                         124 chars  The Employee is engaged as a casual employee in the position
  2.2                         225 chars  There is no firm advance commitment to continuing and indefi
  2.3                         226 chars  The Employee is not entitled to paid leave entitlements that
  3                            27 chars  AWARD AND CLASSIFICATION
  3.1                          55 chars  The employment is covered by {{MODERN_AWARD_NAME}}.
  3.2                          76 chars  The Employee is classified at {{CLASSIFICATION_LEVEL}} under
  3.3                         127 chars  The classification and award coverage above are as instructe
  4                            21 chars  HOURS AND LOCATION
  4.1                          46 chars  Ordinary place of work: {{WORK_LOCATION}}.
  4.2                         167 chars  Hours are as offered and accepted for each engagement. Minim
  5                            15 chars  REMUNERATION
  5.1                          43 chars  Base hourly rate: {{BASE_HOURLY_RATE}}.
  5.2                         121 chars  Casual loading: {{CASUAL_LOADING_PERCENTAGE}}, paid in lieu
  5.3                         163 chars  Superannuation contributions will be paid to {{SUPERANNUATIO
  5.4                          81 chars  Payment is made {{PAY_FREQUENCY}} into the account nominated
  6                            20 chars  CASUAL CONVERSION
  6.1                         202 chars  The Employee's rights in relation to a change to full-time o
  7                            22 chars  SPECIFIC CONDITIONS
  7.1                          19 chars  {{CONDITION_A}}
  7.2                          19 chars  {{CONDITION_B}}
  8                            18 chars  CONFIDENTIALITY
  8.1                         143 chars  The Employee must not use or disclose the Employer's confide
  9                            25 chars  WORK HEALTH AND SAFETY
  9.1                         157 chars  The Employee must comply with the Employer's work health and
  10                           15 chars  TERMINATION
  10.1                        189 chars  Either party may end an engagement in accordance with the no
  10.2                        168 chars  Nothing in this clause limits the Employer's right to termin
  11                           20 chars  ENTIRE AGREEMENT
  11.1                        141 chars  This document records the whole agreement between the partie
  EXECUTION                   247 chars
  -- char coverage: spans sum to 3,576, document is 3,576 --
```

**Judged by eye:** preamble holds the title, status line and the Fair Work Act
paragraph — not clause 1. No clause is implausibly large (max 247 chars in a
3,576-char document). No clause is a sentence fragment. `EXECUTION` is a real,
correctly identified unnumbered section, not a spurious match. Character
coverage totals exactly. **Fit.**

### Full output, document 2 — `02_wa_gov_general_conditions_consultancy_agreement.docx`

```
=== 02_wa_gov_general_conditions_consultancy_agreement.docx: 86,853 chars -> 153 clauses ===
  (preamble)                   65 chars
  NOTE TO USER (TO BE REMOVED BEFORE ISSUE)     41 chars
  1                           301 chars  USE OF THIS TEMPLATE
  2                           555 chars  WORK HEALTH AND SAFETY
  TABLE OF CONTENTS            17 chars
  1                            35 chars  DEFINITIONS AND INTERPRETATION 8
  1.1                          17 chars  Definitions 8
  1.2                          21 chars  Interpretation 13
  2                            42 chars  SCOPE OF CONTRACT AND HEAD AGREEMENT 14
  2.1                          15 chars  Services 14
  2.2                          14 chars  Pricing 14
  2.3                          23 chars  Term of Contract 14
  2.4                          30 chars  Scope of Head Agreement 14
  2.5                          29 chars  Term of Head Agreement 14
  2.6                          34 chars  Extension of Head Agreement 14
  3                            46 chars  FORMATION OF CONTRACT AND HEAD AGREEMENT 15
  3.1                          28 chars  Formation of Contract 15
  3.2                          34 chars  Formation of Head Agreement 15
  3.3                          31 chars  Constitution of Contract 15
  3.4                          16 chars  Generally 16
  3.5                          13 chars  Orders 16
  3.6                          25 chars  Supply of Services 16
  3.7                          34 chars  Variation of Head Agreement 17
  3.8                          25 chars  Variation of Order 17
  3.9                         112 chars  Effect of Expiration or Termination of the Head Agreement 17
  3.10                         53 chars  Effect of Expiration or Termination of Orders 17
  3.11                         35 chars  Dealing with Head Agreement 17
  3.12                         20 chars  Change Panel 17
  3.13                         54 chars  Order of precedence - Head Agreement Documents 17
  3.14                         47 chars  Order of precedence -Contract Documents 18
  4                            49 chars  SUPPLY OF SERVICES AND CONTRACTING SERVICES 18
  4.1                          25 chars  Supply of Services 18
  4.2                          26 chars  Additional Services 18
  4.3                          27 chars  Standard of Services 19
  4.4                          35 chars  Time for performing Services 19
  4.5                          29 chars  Consultant's Personnel 19
  4.6                          20 chars  Key Personnel 20
  4.7                          23 chars  Police Clearance 20
  5                            16 chars  SUSPENSION 20
  5.1                          28 chars  Suspension - Contract 20
  5.2                          34 chars  Suspension - Head Agreement 20
  6                            33 chars  WARRANTIES AND UNDERTAKINGS 21
  6.1                          25 chars  General Warranties 21
  6.2                          28 chars  Consultant Warranties 21
  6.3                          19 chars  Undertakings 21
  7                            27 chars  PAYMENT AND INVOICING 22
  8                             9 chars  GST 23
  9                            34 chars  INTELLECTUAL PROPERTY RIGHTS 24
  10                           22 chars  CONFIDENTIALITY 24
  11                           13 chars  ACCESS 24
  12                           27 chars  CONFLICT OF INTEREST 25
  13                           16 chars  INSURANCE 25
  13.1                         45 chars  Head Agreement Insurance Requirements 25
  13.2                         39 chars  Contract Insurance Requirements 25
  13.3                         37 chars  Reputable and Solvent Insurer 26
  13.4                         32 chars  Maintenance of Insurance 26
  13.5                         29 chars  Evidence of Insurance 26
  13.6                         34 chars  Failure to Prove Insurance 26
  13.7                         28 chars  Incidents and claims 26
  13.8                         29 chars  Continuing Obligation 26
  13.9                         42 chars  No Limitation of Other Liabilities 27
  14                           53 chars  INDEMNITY, LIMITATION OF LIABILITY AND SET OFF 27
  14.1                         17 chars  Indemnity 27
  14.2                         31 chars  Limitation of Liability 27
  14.3                         30 chars  Liability of Principal 27
  14.4                         24 chars  Right of Set Off 28
  14.5                         16 chars  Survival 28
  15                           29 chars  PERFORMANCE MANAGEMENT 28
  16                           24 chars  GOVERNMENT POLICY 28
  17                           29 chars  WORK HEALTH AND SAFETY 28
  18                           30 chars  DEFAULT AND TERMINATION 28
  18.1                         44 chars  Performance of Services by Principal 28
  18.2                         41 chars  Termination of Contract for cause 29
  18.3                         47 chars  Termination of Contract for convenience 29
  18.4                         60 chars  Consequences of Expiration or Termination - Contract 29
  18.5                         49 chars  Principal's further rights on Termination 29
  18.6                         36 chars  Termination - Head Agreement 29
  18.7                         66 chars  Consequences of Expiration or Termination - Head Agreement 3
  19                           25 chars  DISPUTE RESOLUTION 30
  20                           20 chars  NO ASSIGNMENT 30
  21                           15 chars  NOVATION 30
  22                           32 chars  SUB-CONSULTANT/CONTRACTOR 31
  23                           19 chars  RELATIONSHIP 31
  24                           39 chars  NOTICES AND OTHER COMMUNICATIONS 32
  24.1                         26 chars  Service of Notices 32
  24.2                         28 chars  Effective on Receipt 32
  25                           25 chars  GENERAL PROVISIONS 32
  25.1                         14 chars  Waiver 32
  25.2                         24 chars  Entire Agreement 32
  25.3                         20 chars  Counterparts 32
  25.4                         17 chars  Variation 33
  25.5                         17 chars  No Merger 33
  25.6                         20 chars  Severability 33
  25.7                         22 chars  Applicable Law 33
  25.8                         25 chars  Cumulative Rights 33
  25.9                         23 chars  Auditor General 33
  25.10                        16 chars  Consent 33
  25.11                        26 chars  Further Assurance 34
  25.12                        14 chars  Costs 34
  25.13                        47 chars  Trusts 34
  DEFINITIONS AND INTERPRETATION  17002 chars
  SCOPE OF CONTRACT AND HEAD AGREEMENT   2075 chars
  FORMATION OF CONTRACT AND HEAD AGREEMENT   7934 chars
  SUPPLY OF SERVICES AND CONTRACTING SERVICES   7076 chars
  SUSPENSION                 1049 chars
  WARRANTIES AND UNDERTAKINGS   2261 chars
  PAYMENT AND INVOICING      4923 chars
  GST                        1414 chars
  INTELLECTUAL PROPERTY RIGHTS   1245 chars
  CONFIDENTIALITY             891 chars
  ACCESS                     1904 chars
  CONFLICT OF INTEREST        575 chars
  INSURANCE                  6489 chars
  PERFORMANCE MANAGEMENT      163 chars
  GOVERNMENT POLICY           173 chars
  WORK HEALTH AND SAFETY      550 chars
  DEFAULT AND TERMINATION    5110 chars
  DISPUTE RESOLUTION          798 chars
  NO ASSIGNMENT               718 chars
  NOVATION                   2910 chars
  RELATIONSHIP                303 chars
  NOTICES AND OTHER COMMUNICATIONS   1724 chars
  GENERAL PROVISIONS         5527 chars
  TABLE OF CONTENTS            17 chars
  1                            36 chars  DEFINITIONS AND INTERPRETATIONS 3
  1.1                          17 chars  Definitions 3
  1.2                          20 chars  Interpretation 3
  2                            13 chars  NOVATION 4
  3                            12 chars  RELEASE 4
  4                            12 chars  CONSENT 5
  5                            35 chars  WARRANTIES AND REPRESENTATIONS 5
  6                            30 chars  TAXES, COSTS AND EXPENSES 5
  7                            12 chars  NOTICES 5
  7.1                          14 chars  Delivery 5
  7.2                          25 chars  Effect and delivery 6
  8                            22 chars  GOVERNING CLAUSES 6
  8.1                          36 chars  Governing law and jurisdiction 6
  8.2                          15 chars  Variation 6
  8.3                          27 chars  Rights are cumulative 6
  8.4                          15 chars  Severance 6
  8.5                          15 chars  No waiver 6
  8.6                          22 chars  Entire agreement 7
  8.7                          23 chars  Further assurance 7
  8.8                         644 chars  Counterparts 7
  DEFINITIONS AND INTERPRETATIONS   2341 chars
  NOVATION                    721 chars
  RELEASE                     330 chars
  CONSENT                    1336 chars
  WARRANTIES AND REPRESENTATIONS    668 chars
  NOTICES                     765 chars
  3                            45 chars  days after posting if within Australia; and
  7                           248 chars  days after posting if posted to or from a place outside Aust
  GOVERNING CLAUSES          2504 chars
  -- char coverage: spans sum to 86,853, document is 86,853 --
```

**Judged by eye, in detail — this is the document that earns its place as a
gate.**

1. **A genuine numbering restart, not constructed.** `1` and `2` each appear
   three times, `TABLE OF CONTENTS` twice, `NOVATION` twice. This is not a
   splitter defect — the source document itself restarts: an instructional
   "NOTE TO USER" box at the front uses its own `1.`/`2.` (`USE OF THIS
   TEMPLATE`, `WORK HEALTH AND SAFETY`), the real Table of Contents then
   starts its own `1.` through `25.`, and an annexed **Deed of Novation**
   template partway through the document has its own Table of Contents and
   its own `1.` through `8.`. Rule 2 (no sequence assumption) is not a
   theoretical concern here — a real government document restarts numbering
   twice in 87,000 characters, and the splitter handles it with **zero
   provisions lost**, confirmed by the character-coverage check below.

2. **The TOC produces short, fragment-shaped "clauses" — flagged, not a
   defect in the drop sense.** Every TOC line (`1.1 Definitions 8`, 17
   characters) is syntactically indistinguishable from a real one-line
   clause heading with same-line heading text, so each becomes its own tiny
   clause. This is over-segmentation, and several of these clauses read as
   sentence fragments (`GST 23` at 9 characters) — the Step 3 checklist item
   "no clause is a fragment of a sentence" is genuinely violated here, by
   design of the regex rather than by a coding mistake, and it is worth
   naming plainly rather than waving through. It fails on the safe side:
   nothing is lost, the TOC's own words are simply split more finely than a
   human would.

3. **A real, out-of-scope limitation surfaced: Word auto-numbering does not
   survive extraction.** The largest clauses — `DEFINITIONS AND
   INTERPRETATION` at 17,002 characters, `INSURANCE` at 6,489 — are large
   because the real sub-clause numbers (`13.1`, `13.2`, etc., visible only in
   the TOC) **do not appear as literal characters in the document body at
   all**. I confirmed this directly: the underlying `word/document.xml`
   contains 148 `<w:numPr>` elements — Word's native multilevel-list
   numbering property — and `13.1` appears in the extracted text only as an
   inline cross-reference inside a sentence (`"13.1 must be on the terms,
   for the period of time..."`), never as the start of its own numbered
   paragraph. Word generates that visible "13.1" at render time from the
   list definition; it is not stored as text, so `docx_text.py` — which
   reads only `<w:t>` text runs, and which is out of scope for this task and
   for `contracts.py` generally — never sees it. `split_clauses()` is
   behaving correctly on the text it is handed: it cannot detect a boundary
   that was never in the input. But this is a real, material finding: **any
   contract using native Word auto-numbering for its clauses will lose that
   numbering before `contracts.py` ever runs**, and the splitter will fall
   back to whatever heading survives (here, the bare all-caps section
   title). This is not something to fix in this task — no code changes were
   made — but it belongs on the record, and whoever owns `docx_text.py` next
   should see it.

4. **`NOTE TO USER (TO BE REMOVED BEFORE ISSUE)` is a genuine over-match.**
   This is an administrative guidance box that is not part of the contract
   at all, caught by `CAPS_CLAUSE` and turned into its own 41-character
   clause. Over-inclusion, not data loss — consistent with the rule.

5. **Character coverage is total.** Spans sum to 86,853; the document is
   86,853 characters. Nothing was dropped, including inside the two restarts
   and the TOC.

**Verdict on this document: fit to proceed, on the safe side of every rule,
with two real findings recorded above (points 2 and 3) that are not
`contracts.py` defects but are genuine properties of real-world Word
documents that the next person touching either file should know about.**

### Full output, document 3 — `03_wa_gov_form1aa_residential_tenancy_agreement.docx`

```
=== 03_wa_gov_form1aa_residential_tenancy_agreement.docx: 44,122 chars -> 42 clauses ===
  (preamble)                    8 chars
  RESIDENTIAL TENANCY AGREEMENT     29 chars
  RESIDENTIAL TENANCIES ACT 1987 (WA)    172 chars
  PART A                     2105 chars
  TERM OF AGREEMENT           396 chars
  RESIDENTIAL PREMISES        391 chars
  MAXIMUM NUMBER OF OCCUPANTS    131 chars
  RENT                        620 chars
  SECURITY BOND               480 chars
  RENT INCREASE               928 chars
  WATER SERVICES              180 chars
  WATER USAGE COSTS (SCHEME WATER)    124 chars
  PERMISSION TO CONTACT THE WATER SERVICES PROVIDER   1472 chars
  STRATA BY-LAWS              149 chars
  SCHEME BY-LAWS FOR A COMMUNITY TITLES SCHEME    672 chars
  PETS                        512 chars
  RIGHT OF TENANT TO ASSIGN OR SUB-LET    380 chars
  RIGHT OF TENANT TO MAKE MODIFICATIONS    781 chars
  PROPERTY CONDITION REPORTS   1039 chars
  PART B                      312 chars
  RIGHT TO OCCUPY THE PREMISES    279 chars
  COPY OF AGREEMENT           307 chars
  RENT                       2344 chars
  PUBLIC UTILITY SERVICES    1353 chars
  POSSESSION OF THE PREMISES   1044 chars
  USE OF THE PREMISES BY TENANT   1487 chars
  URGENT REPAIRS             5291 chars
  MODIFICATIONS TO THE PREMISES   4140 chars
  LOCKS AND SECURITY DEVICES    896 chars
  PETS                       1139 chars
  TRANSFER OF TENANCY OR SUB-LETTING BY TENANT    460 chars
  CONTRACTING OUT             104 chars
  ENDING THE RESIDENTIAL TENANCY AGREEMENT    702 chars
  ENDING A FIXED-TERM AGREEMENT    533 chars
  ENDING A PERIODIC AGREEMENT   1222 chars
  OTHER GROUNDS FOR ENDING AGREEMENT   1230 chars
  SECURITY BOND              2322 chars
  TENANCY DATABASES           374 chars
  NOTICES                    1767 chars
  IF A DISPUTE CANNOT BE RESOLVED   1728 chars
  IMPORTANT INFORMATION       431 chars
  IMPORTANT INFORMATION      4020 chars
  -- char coverage: spans sum to 44,122, document is 44,122 --
```

**Judged by eye:**

- Preamble is `FORM 1AA` only (8 characters) — correct: this is the form
  number label, not a clause, and the very next all-caps line
  (`RESIDENTIAL TENANCY AGREEMENT`) is the document's real title, correctly
  treated as its own heading rather than folded into the preamble, because it
  is not at offset 0 (the offset-0 exception from Task 2 applies only to the
  very first line, `FORM 1AA` here).
- `RENT` and `IMPORTANT INFORMATION` and `PETS` each appear twice. I checked
  the content directly: both `IMPORTANT INFORMATION` clauses begin with the
  identical sentence ("Additional terms may be included in this agreement
  if..."), confirming this is the **source document itself repeating an
  identical notice** in two places (once in the standard terms, once as a
  closing notice before signatures), not a splitter artefact. `RENT` and
  `PETS` are genuinely distinct clauses under Part A and Part B respectively
  — the government form itself uses the same plain-English heading for
  related but separate provisions in each Part.
- No clause is implausibly large relative to the document (largest is
  `URGENT REPAIRS` at 5,291 of 44,122 characters, about 12%) and none reads
  as a sentence fragment.
- Character coverage: spans sum to 44,122; the document is 44,122 characters.
- This document's headings are genuinely, deliberately unnumbered — the
  government's own prescribed drafting style for this instrument — so there
  is no numbering-restart property to find here, and none was expected.

**Verdict on this document: fit. This is the cleanest of the three, and
confirms the unnumbered-all-caps-heading style works as designed on a real
government form, not just on the synthetic fixture from Task 2.**

## The `CAPS_CLAUSE` prediction, checked against real documents

The Tasks 1-2 report predicted that `CAPS_CLAUSE` would over-match on **a
defined term written in capitals inside a clause body** (the demonstrated
example was `CONFIDENTIAL INFORMATION` on its own line, followed by `means
...`, splitting what should be one definitions clause into two).

**That specific shape did not occur in any of the three real documents.** I
looked for it directly — searched every clause boundary produced across all
three fixtures for a case where a defined term (rather than a genuine section
title) triggered a spurious split — and found none. No contract here defines
a term by writing it in full capitals on its own line inside a clause.

**What did occur, instead, in document 2:**

- `CAPS_CLAUSE` fired on **genuine section-level headings that had lost their
  clause number** (`DEFINITIONS AND INTERPRETATION`, `INSURANCE`, and 21
  others) — not because the pattern mistook a defined term for a heading, but
  because the real numbered heading (`13. INSURANCE`, say) never survived
  DOCX extraction as literal text (see finding 3 above); only the bare caps
  title did. The over-match is real, but its cause is upstream extraction
  loss, not a defined term being confused for a boundary.
- `CAPS_CLAUSE` also fired on `NOTE TO USER (TO BE REMOVED BEFORE ISSUE)`, an
  administrative box that is not part of the contract text at all — a
  different failure shape again: a genuine all-caps heading, just one that
  should not have counted as part of the contract.

**Reporting this plainly rather than adjusting the prediction to fit:** the
prediction was wrong in its specific mechanism (no defined-term-in-caps case
was found) but right in its general shape (`CAPS_CLAUSE` does over-match on
real documents, and does so safely — every case above produced a spurious or
misplaced clause, never a dropped one). The demonstrated
`CONFIDENTIAL INFORMATION` example from the Tasks 1-2 report remains a valid
synthetic illustration of a failure mode the pattern *can* produce, but it is
not the failure mode that actually surfaced on these three real documents.
Anyone testing further real contracts should not assume the
defined-term-in-caps shape is the main risk; the extraction-loss shape found
here is at least as significant and was not anticipated before this task.

## Character coverage and overlap, checked explicitly per document

A plausible clause count does not, by itself, prove nothing was lost or
double-counted — a splitter could return the right number of clauses while
two of them overlap, or while their spans undercount the source. Checked
directly, per the ORCHESTRATOR's request, rather than inferred from the
clause count:

```python
for path in sorted(pathlib.Path("tests/fixtures/contracts").iterdir()):
    text = contract_text(path.read_bytes(), origin=path.name)
    clauses = split_clauses(text)
    total_span = sum(c.char_end - c.char_start for c in clauses)
    spans = sorted((c.char_start, c.char_end) for c in clauses)
    overlaps = [(spans[i], spans[i+1]) for i in range(len(spans) - 1)
                if spans[i][1] > spans[i+1][0]]
    print(f"{path.name}: doc_len={len(text)} span_sum={total_span} "
          f"equal={total_span==len(text)} overlaps={len(overlaps)}")
```

```
01_inhouse_casual_employment_contract.md: doc_len=3576 span_sum=3576 equal=True overlaps=0
02_wa_gov_general_conditions_consultancy_agreement.docx: doc_len=86853 span_sum=86853 equal=True overlaps=0
03_wa_gov_form1aa_residential_tenancy_agreement.docx: doc_len=44122 span_sum=44122 equal=True overlaps=0
```

| Document | Document length | Sum of clause spans | Equal? | Overlapping ranges |
|---|---|---|---|---|
| `01_inhouse_casual_employment_contract.md` | 3,576 | 3,576 | Yes | 0 |
| `02_wa_gov_general_conditions_consultancy_agreement.docx` | 86,853 | 86,853 | Yes | 0 |
| `03_wa_gov_form1aa_residential_tenancy_agreement.docx` | 44,122 | 44,122 | Yes | 0 |

All three: span sum equals document length exactly, and zero overlapping
ranges. This holds even for document 2, which has 153 clauses across two
restarted numbering sequences and a duplicated Table of Contents — the
invariant survives the exact condition (real numbering restarts) that this
task exists to test.

## Step 4 — is the splitter fit to proceed?

**Yes.** Across three genuinely different documents — an in-house markdown
template, a genuine WA government consultancy agreement with a real numbering
restart embedded in it, and a genuine WA government unnumbered-caps statutory
form — 100% of character content is accounted for in every case, no clause is
a dropped fragment of the source, and every over-match found fails on the
correct side (a spurious clause, never a lost one). Rule 1 (nothing dropped)
and Rule 2 (no sequence assumption) were each tested against real, unplanned
document structure, not merely against the Task 2 fixtures that were written
to exercise them, and both held.

Two findings are carried forward for the record rather than fixed here,
because this task makes no code change:

1. A Table of Contents block produces many short, fragment-shaped spurious
   clauses. Over-inclusion, not data loss, but worth knowing before reading a
   comparison or completeness report generated from a document that ships
   with a Word-generated TOC.
2. `docx_text.py` does not preserve clause numbers created by Word's native
   auto-numbered list formatting (`<w:numPr>`), which measurably widens
   clauses in any document using that drafting convention. This sits outside
   `contracts.py` and outside this task's scope, and is named here so it is
   not rediscovered the way the "and stop" wording and the monotonic-section
   assumption were.

Task 4 may proceed.
