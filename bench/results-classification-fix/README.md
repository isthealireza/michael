# Bench run: the classification rule given a format and a position

42 runs (7 models x 2 prompts x 3), $2.23, against production at commit
9583461. Compare with `../results/` (the baseline, $1.85).

## What changed between the two runs

`MICHAEL.md` line 3. The classification rule was one subordinate clause -
"and say which" - with no format and no position. It now names a line and
states where it goes, the way the closing blocks always have.

## Result, scored with the same regex over both runs

| model                       | before | after | as first line |
|-----------------------------|--------|-------|---------------|
| openai/gpt-oss-120b         | 0/6    | 6/6   | 6/6           |
| deepseek/deepseek-v4-pro    | 3/6    | 6/6   | 6/6           |
| deepseek/deepseek-v4-flash  | 4/6    | 6/6   | 6/6           |
| anthropic/claude-haiku-4.5  | 2/6    | 5/6   | 4/6           |
| anthropic/claude-opus-5     | 6/6    | 6/6   | 6/6           |
| anthropic/claude-sonnet-5   | 6/6    | 6/6   | 6/6           |
| qwen/qwen3.7-flash          | 2/6    | 2/6   | 2/6           |
| TOTAL                       | 23/42  | 37/42 | 36/42         |

Closing blocks complete: 37/42 -> 40/42, comparing normalised text. Two
apparent opus failures in an earlier count were the closing notice wrapped
across a line; the numbers here normalise whitespace, as the prompt guard
test does.

qwen3.7-flash did not move. Its misses are genuine, not a scoring artefact:
it opens with prose and never declares. It was already a benchmark
disqualification.

A grep for the words RESEARCH, DRAFT or BOTH scores the baseline 41/42,
because "Internal research only" in the closing notice and "I'll draft the
contract" in a sentence both carry one. The measure here is a declaration.

## The uncovered arm proves nothing in either run

Baseline: 19 of 21 uncovered runs replied NOT COVERED. This run: 0 of 21.
That is not a regression. The prompt asked about eligible data breaches
under the Privacy Act 1988 (Cth), which has since been ingested:

    michael search "eligible data breach notification"
    -> covered: true, best_score 0.7332

Answering is correct behaviour for a covered question, so the arm stopped
testing NOT COVERED some time between the two runs, silently, and nothing
failed. `run_bench.py` now refuses to start if the uncovered prompt is
covered, and the prompt is a migration-law question measured at 0.5556.

That guard landed AFTER this run. These 42 outputs were produced with the
stale prompt, so read the uncovered arm here as unmeasured, not as passing.
