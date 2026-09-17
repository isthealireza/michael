"""Score a directory of bench outputs with michael.output_check.

The scorer and the page must not disagree about what a well-formed output
is, so neither owns a definition: both call michael.output_check. An
earlier ad-hoc regex here reported 19 classification misses where the
shared check reports 21, because it also accepted "This is a DRAFT
request" - a declaration MICHAEL.md does not ask for.

    uv run python bench/score_outputs.py bench/results [more dirs...]
"""

from __future__ import annotations

import collections
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from michael.output_check import check  # noqa: E402


def score(directory: pathlib.Path) -> None:
    files = [
        f
        for f in sorted(directory.glob("*__*.txt"))
        if not f.name.endswith(".err.txt")
    ]
    counts: collections.Counter[str] = collections.Counter()
    per_model: dict[str, list[int]] = collections.defaultdict(lambda: [0, 0])
    clean = 0
    for f in files:
        findings = check(f.read_text(encoding="utf-8", errors="replace"))
        model = f.stem.split("__")[0]
        per_model[model][1] += 1
        if findings:
            for finding in findings:
                counts[finding.rule] += 1
        else:
            clean += 1
            per_model[model][0] += 1

    print(f"{directory}: {len(files)} outputs, clean {clean}/{len(files)}")
    for model in sorted(per_model):
        ok, total = per_model[model]
        print(f"    {model:30} {ok}/{total}")
    for rule, n in counts.most_common():
        print(f"    {rule:26} {n}")


if __name__ == "__main__":
    for arg in sys.argv[1:] or ["bench/results"]:
        score(pathlib.Path(arg))
        print()
