"""Score verify_draft against calibration/verify_labelled.json.

Run with a judge configured (``VERIFY_JUDGE_MODEL``, ``OPENROUTER_API_KEY``)::

    uv run python calibration/score_verify.py [--limit N] [--out transcript.jsonl]

Two measurements, because the system has two things worth measuring.

*Detection*: a claim is detected when the verifier flags it - UNSUPPORTED or
PARTIAL - because both are marked inline and both reach the practitioner under
OPEN ITEMS. That is what the draft actually does with a verdict.

*Strict UNSUPPORTED*: precision and recall counting only UNSUPPORTED as a
positive. Reported separately because PARTIAL is the honest answer to a claim
whose substance is right and whose number is wrong, and a measurement that
counts that as a miss would push the judge towards over-flagging.

The failure that matters is neither of those rates. It is a claim carrying an
injected error that comes back SUPPORTED, because that is the one outcome that
tells a practitioner a wrong thing is right. Every one is printed in full.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from michael.cli import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from michael.config import settings  # noqa: E402
from michael.retrieve import RetrievedProvision  # noqa: E402
from michael.verify import Verification, verify_draft  # noqa: E402

LABELLED = ROOT / "calibration" / "verify_labelled.json"


def provision_of(row: dict[str, Any]) -> RetrievedProvision:
    """Rebuild a retrieved provision from the labelled set.

    The scores are placeholders: verification never reads them, and inventing
    a plausible fused score here would suggest this set had been through
    retrieval when it has not.
    """
    text = str(row["text"])
    return RetrievedProvision(
        provision_id=int(row["provision_id"]),
        document_id=0,
        jurisdiction=str(row["jurisdiction"]),
        title=str(row["citation"]),
        citation=str(row["citation"]),
        source_url=str(row["source_url"]),
        snapshot_date=date(2026, 7, 7),
        sha256="0" * 64,
        doc_type="act",
        section_number=str(row["section_number"]),
        heading=str(row["heading"]),
        text=text,
        char_start=0,
        char_end=len(text),
        lexical_score=0.0,
        vector_score=0.0,
        score=0.0,
    )


@dataclass
class Tally:
    true_positive: int = 0
    false_positive: int = 0
    false_negative: int = 0

    @property
    def precision(self) -> float:
        hit = self.true_positive + self.false_positive
        return self.true_positive / hit if hit else 0.0

    @property
    def recall(self) -> float:
        hit = self.true_positive + self.false_negative
        return self.true_positive / hit if hit else 0.0

    def line(self, name: str) -> str:
        return (
            f"{name}: precision {self.precision:.3f} "
            f"({self.true_positive}/{self.true_positive + self.false_positive}), "
            f"recall {self.recall:.3f} "
            f"({self.true_positive}/{self.true_positive + self.false_negative})"
        )


def run_case(case: dict[str, Any]) -> tuple[Verification, float]:
    provisions = [provision_of(p) for p in case["provisions"]]
    started = time.monotonic()
    result = verify_draft(str(case["draft"]), provisions=provisions)
    return result, time.monotonic() - started


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="score only the first N cases")
    parser.add_argument("--out", type=Path, default=ROOT / "calibration" / "verify_scored.jsonl")
    args = parser.parse_args()

    payload = json.loads(LABELLED.read_text(encoding="utf-8"))
    cases = payload["cases"][: args.limit] if args.limit else payload["cases"]

    detection = Tally()
    strict = Tally()
    false_supported: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    seconds = 0.0
    prompt_tokens = completion_tokens = 0
    cost = 0.0
    cost_known = True
    judge_model = settings().verify_judge_model

    for case in cases:
        result, elapsed = run_case(case)
        seconds += elapsed
        if result.call:
            judge_model = result.call.model or judge_model
            prompt_tokens += result.call.prompt_tokens
            completion_tokens += result.call.completion_tokens
            if result.call.cost_usd is None:
                cost_known = False
            else:
                cost += result.call.cost_usd
            if result.call.error:
                print(f"  ! {case['case_id']}: {result.call.error}", file=sys.stderr)

        by_id = {r.claim_id: r for r in result.rulings}
        for labelled in case["claims"]:
            ruling = by_id.get(labelled["claim_id"])
            verdict = ruling.verdict if ruling else "UNSUPPORTED"
            reason = ruling.reason if ruling else "(no ruling)"
            ids = list(ruling.provision_ids) if ruling else []
            injected = bool(labelled["injected"])

            flagged = verdict in ("UNSUPPORTED", "PARTIAL")
            if injected and flagged:
                detection.true_positive += 1
            elif injected:
                detection.false_negative += 1
            elif flagged:
                detection.false_positive += 1

            if injected and verdict == "UNSUPPORTED":
                strict.true_positive += 1
            elif injected:
                strict.false_negative += 1
            elif verdict == "UNSUPPORTED":
                strict.false_positive += 1

            row = {
                "case_id": case["case_id"],
                "claim_id": labelled["claim_id"],
                "label": labelled["label"],
                "injected": injected,
                "verdict": verdict,
                "provision_ids": ids,
                "reason": reason,
                "claim": labelled["text"],
            }
            rows.append(row)
            if injected and verdict == "SUPPORTED":
                false_supported.append(
                    {
                        **row,
                        "provisions": [
                            {
                                "provision_id": p["provision_id"],
                                "citation": p["citation"],
                                "section_number": p["section_number"],
                                "text": p["text"],
                            }
                            for p in case["provisions"]
                        ],
                    }
                )
        print(
            f"{case['case_id']:<28} {result.counts} in {elapsed:5.1f}s",
            flush=True,
        )

    with args.out.open("w", encoding="utf-8", newline="") as handle:
        handle.writelines(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)

    n = len(cases)
    claims = len(rows)
    print()
    print(f"judge model:        {judge_model}")
    print(f"cases:              {n}")
    print(f"claims:             {claims}")
    print(f"injected errors:    {sum(1 for r in rows if r['injected'])}")
    print()
    print(detection.line("flagged (UNSUPPORTED or PARTIAL)"))
    print(strict.line("strict UNSUPPORTED             "))
    print()
    print(f"FALSE SUPPORTED on an injected error: {len(false_supported)}")
    for row in false_supported:
        print("-" * 72)
        print(f"  case:     {row['case_id']} / {row['claim_id']} ({row['label']})")
        print(f"  claim:    {row['claim']}")
        print(f"  verdict:  SUPPORTED, citing {row['provision_ids']}")
        print(f"  reason:   {row['reason']}")
        for provision in row["provisions"]:
            print(
                f"  provision {provision['provision_id']} "
                f"({provision['citation']} s {provision['section_number']})"
            )
    print("-" * 72)
    print()
    print(f"latency:  {seconds / n:6.2f} s per draft ({seconds:.1f}s total)")
    print(f"tokens:   {prompt_tokens / n:6.0f} prompt + {completion_tokens / n:4.0f} completion")
    if cost_known:
        print(f"cost:     ${cost / n:.6f} per draft (${cost:.4f} total)")
    else:
        print("cost:     not reported by the provider for every call")
    print(f"transcript written to {args.out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
