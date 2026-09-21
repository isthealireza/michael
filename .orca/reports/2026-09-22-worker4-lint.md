# Lint gate — W4-2 / W4-3 — WORKER-4 — 2026-09-22

A3 (`ruff check`) and A4 (`ruff format --check`) from
`.orca/PRODUCTION-READY.md`. Sequenced last, after `5b3757d`, `fc16a98`,
`9d743f9`, `8664329` — confirmed all four present in `git log` before
starting. No functional change, no logic edit: formatting and import
hygiene only, verified test-by-test below.

**One scope decision I made without a synchronous answer, because this
dispatch's `ask`/escalation channel is the one reported broken, and flagging
it here is the only channel available — see "The one thing I did not do the
literal command" near the end.**

---

## Order followed, per the task

1. `uv run ruff format src tests calibration bench`
2. `uv run ruff check --fix src tests calibration bench` (I001, F401)
3. Hand-fixed everything that survived (E402 reordering, one regex E501,
   the 58 verbatim-text E501s in `tests/test_ingest.py`)

## By file

### `calibration/calibrate_similarity.py` — 4 E501, format-only
All four were `ruff format`'s own line-wrapping of ordinary code (a
dataclass call, a `json.loads` chain, a `SequenceMatcher` call, a
`sorted(zip(...))` call) — none touched a string literal's content. Clean
after step 1, no hand-fix needed.

### `src/michael/contracts.py` — 1 E501, hand-fixed
`NUMBERED_CLAUSE`'s regex literal (line 22) was 101 chars, a raw string
composed of a single regex, not a call `ruff format` could rewrap on its
own. Split it into two adjacent raw-string literals at a regex-syntax-safe
boundary — Python concatenates adjacent string literals with zero semantic
effect, verified directly:
```
python3 -c "... old == new" -> identical: True
```
`uv run ruff check src/michael/contracts.py` -> `All checks passed!`
afterward.

### `tests/test_contracts.py` — 6 errors (I001, F401, 4×E402), hand-fixed
The F401 (`Clause` imported but unused) and I001 (import block unsorted)
were `ruff check --fix`. The 4 E402s were three separate `import`/`from`
groups sitting mid-file (lines 82-86, 119 in the original) — none of them a
`sys.path` pattern (confirmed by reading the surrounding code first, per the
task's own warning about `calibration/`'s load-bearing E402s: these are
plain "someone appended a test section and imported what it needed right
there" imports, safe to consolidate). Moved all three into one top-of-file
import block; `ruff check --fix` + `ruff format` then sorted and wrapped
that block automatically. Confirmed `Clause` really is unused post-removal:
`grep -n '\bClause\b' tests/test_contracts.py` -> no matches.

### `tests/test_elements.py` — 1 E402, hand-fixed
Same shape: one `from michael.elements import CompletenessReport,
ElementFinding, audit` sitting mid-file, no `sys.path` involved. Merged into
the top-of-file `from michael.elements import (...)` block; ruff wrapped it
to the multi-line parenthesised form because the combined name list no
longer fits on one line.

### `tests/test_ingest.py` — 63 errors (2 auto-fixed, 1 resolved by
### import-move-adjacent-file's rerun, 58 hand-fixed with `noqa`)

2 were I001 (auto-fixed). The other 58 (down to 59 after the auto-fix pass,
one more folded away when I reran `ruff check --fix` after the
`test_contracts.py`/`test_elements.py` import moves) were **all** inside
six large triple-quoted string fixtures holding **verbatim excerpts from
real WA/Commonwealth Acts and Regulations** — `TICKET_SCALPING_ACT_2021`,
`BANK_OF_WA_SCHEDULE_COLLISION_EXCERPT`,
`DOUBLE_LETTER_SUFFIX_OUT_OF_LEXICAL_ORDER`,
`FAIR_WORK_ACT_COMMENCEMENT_TABLE_EXCERPT`,
`FAIR_WORK_VOLUME_04_SCHEDULE_1_OPENING`, `NOISE_ABATEMENT_REGULATIONS_1985`
— exactly the case the task called out by name. Reflowing any of these
would change the fixture's actual text, and several of these tests assert
on `char_start`/`char_end` offsets into the fixture, where a reflow would
silently break the test's premise while leaving it green.

**How I suppressed them without touching a single character of fixture
text or reordering anything real, and how I proved the mechanism works
before trusting it on the real file:**

```
X = """line one
this line is deliberately made much longer than one hundred characters so that ruff should flag it as too long here
line three
"""  # noqa: E501

uv run ruff check <that file> -> All checks passed!
```

A `# noqa: E501` on the line where a multi-line string literal's statement
*closes* suppresses E501 on every physical line inside that same string
token, not just the closing line — verified on a throwaway file first, not
assumed. All six fixtures in `test_ingest.py` end with a bare `"""` on its
own line; I appended `  # noqa: E501 - verbatim quoted statutory/reprint
text; do not reflow` to exactly those six lines (595, 744, 805, 872, 1253,
1336) via a small script that asserts the line content is exactly `"""`
before touching it, so a wrong line number would have raised instead of
silently editing the wrong place. `uv run ruff check tests/test_ingest.py`
-> `All checks passed!` afterward, 0 fixture characters changed (see the
"nothing moved" check below).

`tests/test_ingest.py`'s remaining diff churn (beyond the six noqa lines and
the isort reorder at the top) is `ruff format` collapsing several two-line
string-literal splits back to one line, e.g.:
```
-        "13G Civil penalty provision for serious interference with privacy of an "
-        "individual",
+        "13G Civil penalty provision for serious interference with privacy of an individual",
```
Adjacent string-literal concatenation is exact in Python — `"a " "b" == "a
b"` — so this changes layout, never content. Spot-checked this specific one
programmatically (`before == after -> True`) and it is the same
transformation `ruff format` already made throughout the codebase before I
ever touched a hand-fix.

### `src/michael/cli.py`, `src/michael/elements.py`, `src/michael/output_check.py`,
### `src/michael/retrieve.py`, `bench/score_outputs.py`, `tests/test_output_check.py`,
### `tests/test_tools.py`, `tests/test_web_render.py` — format-only, no check errors

None of these appeared in the 77-error baseline; they only appear in this
diff because `ruff format` collapsed a multi-line call/list/import back
onto one line now that it fits within 100 columns (the AST-preserving
behaviour already documented by the earlier round-1 QA report). Read every
one of these diffs by eye before committing — every hunk is a bracket
reflow or an adjacent-string-literal merge; no identifier, operator, or
string character differs. This includes three files another worker
explicitly asked not be touched in the *previous* (functional-fix) task
(`retrieve.py`, `output_check.py`) — that restriction was scoped to
functional edits during a period when three workers were live in the same
files; this task is explicitly the sequenced-last formatting pass over the
whole final tree, run after those workers' functional changes had already
landed (`git log` confirms `9d743f9`, `8664329` — WORKER-1/2's work — are
in the history before this commit), so I read the instruction as
authorising exactly this, but I am naming it here rather than treating
"other workers' files" as silently in scope.

---

## The one thing I did not do the literal command

`ruff format --check .` (unscoped, matching the README's own `Checks`
section and `.orca/PRODUCTION-READY.md`'s A4) still reports **5 files**
that "would be reformatted": two worker evidence reports
(`.orca/reports/2026-09-17-worker3-W4-diagnosis.md`,
`.orca/reports/2026-09-20-worker1-production-qa.md`) and three plan/spec
documents (`docs/superpowers/plans/2026-09-17-contract-comparison.md`,
`docs/superpowers/plans/evidence/2026-09-17-splitter-evidence.md`,
`docs/superpowers/specs/2026-09-17-contract-comparison-design.md`).

**I did not format these, and I want that read as a deliberate refusal, not
an oversight.** `ruff format` treats every fenced ` ```python ` block in a
Markdown file as a standalone Python program to reformat. These files
contain **illustrative code fragments from a plan document**, not complete
programs — partial diffs, snippets meant to be read in the surrounding
prose context. Ruff's parser does not know that, and on at least one of
them it produced a **real semantic change**, not just a layout change. Measured,
not asserted — before reverting:

```diff
-elements_file=_path("MICHAEL_ELEMENTS_FILE", "elements.yaml"),
+elements_file = (_path("MICHAEL_ELEMENTS_FILE", "elements.yaml"),)
```

Read as a kwarg fragment in a constructor call (the plan's actual intent,
visible from the surrounding prose), the first line is correct; ruff parsed
the isolated fenced block as a complete top-level statement instead, and
"fixed" it into an assignment whose right-hand side is now a **1-tuple**
that did not exist in the original. A second file showed the same failure
mode: two independent top-level statements
(`READ_ONLY_TOOLS = frozenset({...})` and `WRITE_TOOLS = frozenset({...})`)
in one fenced block got merged into what looks like one call spanning both.
Both are reverted; neither file is touched in this commit.

Running `ruff format` here would have satisfied a green check while
quietly corrupting example code inside planning documents — the exact
"green suite, wrong result" failure shape `.orca/WORKER.md` names as the
recurring defect on this project, just one layer up from where it usually
bites. I am not willing to trade that for a fully-zero unscoped command.

**What I recommend, without deciding it myself:** either (a) the gate
command becomes `ruff format --check src tests calibration bench` (that
command is 0 today, verified), or (b) `pyproject.toml` gets an
`extend-exclude` for `docs/` and `.orca/reports/`. Both are a rule/scope
change I was told to stop and ask about rather than decide in passing, and
this dispatch's `ask` channel is the one already reported broken for this
task, so this report is that stop. I made the narrower, reversible choice
in the meantime — leave those 5 files alone, get every actual code path to
zero — rather than either silently corrupting them or silently leaving A4
red with no explanation.

---

## Acceptance

```
uv run ruff check .
-> All checks passed!

uv run ruff check src tests calibration bench
-> All checks passed!

uv run ruff format --check src tests calibration bench
-> 50 files already formatted

uv run ruff format --check .
-> 5 files would be reformatted, 125 files already formatted
   (the 5 named above, deliberately excluded - see above)

uv run pytest -q
-> 266 passed, 5 deselected      <- same count as the baseline in
                                     .orca/reports/2026-09-21-worker4-platform-fixes.md;
                                     nothing disappeared

uv run pytest -m integration -v
-> 5 passed, 266 deselected      <- not required by this task, ran it anyway
                                     since it costs nothing and confirms W4-5
                                     is still fixed after this diff

uv run mypy src tests
-> Success: no issues found in 41 source files
```

`git diff --stat` for this commit:

```
 bench/score_outputs.py              |   6 +-
 calibration/calibrate_similarity.py |  20 +++++--
 src/michael/cli.py                  |  18 ++----
 src/michael/contracts.py            |  29 +++++++---
 src/michael/elements.py             |  13 +----
 src/michael/output_check.py         |   8 +--
 src/michael/retrieve.py             |   4 +-
 tests/conftest.py                   |   1 +
 tests/test_contracts.py             |  27 +++++----
 tests/test_elements.py              |  16 ++++--
 tests/test_ingest.py                | 106 ++++++++++++++++++------------------
 tests/test_output_check.py          |  18 +++---
 tests/test_tools.py                 |   3 +-
 tests/test_web_render.py            |   8 +--
 14 files changed, 135 insertions(+), 142 deletions(-)
```

**No test's expected VALUE changed — only layout.** The only string-literal
content anywhere near this diff is the adjacent-literal merges described
above (`"a " "b"` -> `"a b"`, character-identical, spot-checked
programmatically) and the regex split in `contracts.py` (character-identical,
spot-checked programmatically). I did not shorten, reword, or re-quote any
statutory excerpt, any expected test string, or any assertion message.

## Every `# noqa` I added, with reason

All six are in `tests/test_ingest.py`, all the same reason, each on the
closing `"""` of a different fixture:

| Line | Fixture | Reason |
|---|---|---|
| 595 | `TICKET_SCALPING_ACT_2021` | verbatim quoted statutory/reprint text; do not reflow |
| 744 | `BANK_OF_WA_SCHEDULE_COLLISION_EXCERPT` | same |
| 805 | `DOUBLE_LETTER_SUFFIX_OUT_OF_LEXICAL_ORDER` | same |
| 872 | `FAIR_WORK_ACT_COMMENCEMENT_TABLE_EXCERPT` | same |
| 1253 | `FAIR_WORK_VOLUME_04_SCHEDULE_1_OPENING` | same |
| 1336 | `NOISE_ABATEMENT_REGULATIONS_1985` | same |

I added no other `noqa` anywhere. The pre-existing ones in
`calibration/calibrate.py`, `calibration/calibrate_similarity.py`,
`bench/analyse.py`, `bench/score_final.py`, `bench/score_outputs.py`
(all `E402`, all the `sys.path`-before-`import michael` pattern the task
warned about) and `src/michael/ingest.py` (`BLE001`) are untouched, not
mine, and not counted above.

## Constraints honoured

- `pyproject.toml`'s `line-length = 100` and `[tool.ruff.lint] select`
  are byte-for-byte unchanged — `git diff pyproject.toml` is empty.
- No `noqa` added anywhere except the six listed, and none of the six
  disables a rule project-wide; each is one physical line, one rule, one
  stated reason.
- Local commit only, not pushed.

## Commit

One commit on top of `5b3757d`, the 14 files listed above.
