"""Score separation experiments: can anything order covered above not-covered?

The fused score does not (README, "Calibrating the threshold"). This measures
two candidate remedies against the SAME labelled set calibrate.py uses:

    probe     retrieve each labelled query once and cache its top-k fused
              candidates, both unfiltered and routed the way production routes
              it (domain from classify_request). Everything below reads this
              cache, so every option is scored on exactly the same shortlist.
    domain    per-domain thresholds, domain from classify_request.
    rerank    a cross-encoder over the cached top-k (see --help on it).

    uv run python calibration/separation.py probe
    uv run python calibration/separation.py domain
    uv run python calibration/separation.py pairs PAIRS.json
    <python-with-torch> calibration/rerank_local.py PAIRS.json scores_bge.json --model bge
    uv run python calibration/separation.py rerank scores_*.json

Nothing here changes RETRIEVAL_MIN_SCORE or any retrieval code path.

A false positive is the failure that matters, as in calibrate.py: an answer to
a question the corpus cannot answer. So each option is summarised by its
lowest zero-false-positive operating point, its recall there, and its margin -
the lowest retained known-good target score minus the highest known-absent
score, as the 2026-09-24 threshold report defines it.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from michael.cli import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from michael.domains import Routing, route  # noqa: E402
from michael.retrieve import search  # noqa: E402

LABELLED = ROOT / "calibration" / "labelled_queries.json"
CACHE = ROOT / "calibration" / "separation_probes.json"
#: Candidates kept per query. A reranker can only promote what retrieval
#: surfaced, so the shortlist is wider than the top 10 that counts as a hit.
SHORTLIST = 20
#: A known-good query is a true positive only if its target ranks in the top
#: 10 at or above the threshold - the same rule calibrate.py applies.
HIT_RANK = 10
PASSAGE_CHARS = 3000
#: Laya's shipped noul temperature (rl_agent_config.json, "noul:2").
LAYA_SHIPPED_NOUL_TEMPERATURE = 1.983399510383606
UNFILTERED = Routing(domain=None, matched_keywords=(), recognised=False)
NO_DOMAIN = "(unrecognised)"


# --- probing -----------------------------------------------------------------


def _candidates(query: str, routing: Routing) -> list[dict[str, Any]]:
    result = search(query, routing=routing, top_k=SHORTLIST, min_score=0.0)
    return [
        {
            "id": p.provision_id,
            "citation": p.citation,
            "section": p.section_number,
            "unit": p.unit_type,
            "doc_type": p.doc_type,
            "score": p.score,
        }
        for p in result.provisions
    ]


def probe() -> None:
    data = json.loads(LABELLED.read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = []
    for kind, cases in (("good", data["known_good"]), ("absent", data["known_absent"])):
        for case in cases:
            routing = route(case["query"])
            row: dict[str, Any] = {
                "label": kind,
                "query": case["query"],
                "domain": routing.name or NO_DOMAIN,
                "unfiltered": _candidates(case["query"], UNFILTERED),
                "routed": _candidates(case["query"], routing),
            }
            if kind == "good":
                row["target"] = [case["citation"], case["section"], case.get("unit", "section")]
            rows.append(row)
            print(f"  {kind:<6} {row['domain']:<20} {case['query'][:60]}")
    CACHE.write_text(json.dumps(rows, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"cached {len(rows)} queries x {SHORTLIST} candidates -> {CACHE.relative_to(ROOT)}")


def load_probes() -> list[dict[str, Any]]:
    if not CACHE.is_file():
        raise SystemExit(f"no probe cache at {CACHE}; run `separation.py probe` first")
    return json.loads(CACHE.read_text(encoding="utf-8"))


def pairs(out: Path) -> None:
    """Write every cached (query, candidate) pair with the text a reranker reads.

    The passage is the citation, the heading and the provision text. The
    citation is included on purpose: it names the jurisdiction ("(Cth)",
    "(WA)"), which is exactly what a question about Queensland or New Zealand
    law needs a reranker to see. Text is capped; every reranker here reads at
    most 512 tokens, so anything past the cap would be truncated regardless.
    """
    from michael.db import readonly

    probes = load_probes()
    ids = sorted({c["id"] for r in probes for m in ("unfiltered", "routed") for c in r[m]})
    texts: dict[str, str] = {}
    with readonly() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT p.id, d.citation, p.section_number, p.heading, p.text
              FROM provisions p JOIN documents d ON d.id = p.document_id
             WHERE p.id = ANY(%s)
            """,
            (ids,),
        )
        for row in cur.fetchall():
            head = f"{row['citation']} {row['section_number']} {row['heading']}".strip()
            texts[str(row["id"])] = f"{head}\n{row['text'][:PASSAGE_CHARS]}"
    queries = [
        {
            "query": r["query"],
            "ids": sorted({c["id"] for m in ("unfiltered", "routed") for c in r[m]}),
        }
        for r in probes
    ]
    out.write_text(json.dumps({"queries": queries, "texts": texts}) + "\n", encoding="utf-8")
    print(f"{sum(len(q['ids']) for q in queries)} pairs, {len(texts)} provisions -> {out}")


# --- scoring -----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Scored:
    """One query reduced to the two numbers every threshold rule needs."""

    label: str
    domain: str
    query: str
    best: float  # top candidate's score - what a known-absent must stay under
    target: float | None  # target's score if it ranks within HIT_RANK, else None


def reduce(row: dict[str, Any], candidates: list[dict[str, Any]], key: str) -> Scored:
    """Order ``candidates`` by ``key`` and read off best and target scores."""
    ranked = sorted(candidates, key=lambda c: -c[key])
    best = ranked[0][key] if ranked else 0.0
    target_score: float | None = None
    if row["label"] == "good":
        want = tuple(row["target"])
        for rank, c in enumerate(ranked, start=1):
            if rank > HIT_RANK:
                break
            if (c["citation"], c["section"], c["unit"]) == want:
                target_score = c[key]
                break
    return Scored(row["label"], row["domain"], row["query"], best, target_score)


def confusion(items: list[Scored], threshold_of: Any) -> tuple[int, int, int, int]:
    tp = fn = fp = tn = 0
    for s in items:
        t = threshold_of(s)
        if s.label == "good":
            if s.target is not None and s.target >= t:
                tp += 1
            else:
                fn += 1
        elif s.best >= t:
            fp += 1
        else:
            tn += 1
    return tp, fn, fp, tn


def row_text(t: float | str, tp: int, fn: int, fp: int, tn: int) -> str:
    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    label = f"{t:.2f}" if isinstance(t, float) else t
    return f"| {label} | {tp} | {fn} | {fp} | {tn} | {precision:.3f} | {recall:.3f} |"


TABLE_HEAD = "| threshold | TP | FN | FP | TN | precision | recall |\n|---|---|---|---|---|---|---|"


def zero_fp_point(items: list[Scored]) -> dict[str, Any]:
    """The lowest single threshold with no false positive, exactly (no grid)."""
    absent = [s.best for s in items if s.label == "absent"]
    worst = max(absent) if absent else 0.0
    threshold = worst + 1e-9
    retained = [
        s.target
        for s in items
        if s.label == "good" and s.target is not None and s.target >= threshold
    ]
    good = sum(1 for s in items if s.label == "good")
    return {
        "threshold": threshold,
        "highest_absent": worst,
        "recall": len(retained) / good if good else 0.0,
        "retained": len(retained),
        "good": good,
        "margin": (min(retained) - worst) if retained else None,
    }


# --- option 2: per-domain thresholds -----------------------------------------


def fit_domain_thresholds(
    items: list[Scored], fallback: float, domains: set[str] | None = None
) -> dict[str, float]:
    """Per domain, the lowest threshold with no false positive in that domain.

    A domain with no known-absent query has nothing to fit a threshold to -
    zero false positives there is free at ANY threshold, including 0. It takes
    ``fallback`` instead, because "no counter-example in the labelled set" is
    not evidence that the domain has no uncovered questions.
    """
    by_domain: dict[str, list[float]] = defaultdict(list)
    domains = domains if domains is not None else {s.domain for s in items}
    for s in items:
        if s.label == "absent":
            by_domain[s.domain].append(s.best)
    return {d: (max(by_domain[d]) + 1e-9 if by_domain[d] else fallback) for d in domains}


def domain_report(mode: str, items: list[Scored]) -> dict[str, Any]:
    global_point = zero_fp_point(items)
    fallback = global_point["threshold"]
    fitted = fit_domain_thresholds(items, fallback)

    print(f"\n### Per-domain thresholds - retrieval {mode}\n")
    print("| domain | good | absent | threshold | from | recall | margin |")
    print("|---|---|---|---|---|---|---|")
    margins = []
    for d in sorted(fitted):
        members = [s for s in items if s.domain == d]
        good = [s for s in members if s.label == "good"]
        absent = [s for s in members if s.label == "absent"]
        t = fitted[d]
        kept = [s.target for s in good if s.target is not None and s.target >= t]
        worst = max((s.best for s in absent), default=None)
        margin = (min(kept) - worst) if (kept and worst is not None) else None
        if margin is not None:
            margins.append(margin)
        source = "fitted" if absent else "fallback (no known-absent)"
        recall = f"{len(kept)}/{len(good)}" if good else "-"
        print(
            f"| {d} | {len(good)} | {len(absent)} | {t:.3f} | {source} | {recall} | "
            f"{'-' if margin is None else f'{margin:.4f}'} |"
        )

    tp, fn, fp, tn = confusion(items, lambda s: fitted[s.domain])
    print(
        f"\nIn-sample (thresholds fitted on the very queries they are scored on):\n\n{TABLE_HEAD}"
    )
    print(row_text("fitted", tp, fn, fp, tn))
    # Sensitivity: the fitted point is exactly at the worst known-absent, so
    # the next unseen question a hair above it is a false positive. Adding a
    # uniform safety offset shows what headroom costs.
    for offset in (0.01, 0.02, 0.05):
        print(
            row_text(
                f"fitted +{offset:.2f}", *confusion(items, lambda s, o=offset: fitted[s.domain] + o)
            )
        )

    # Leave-one-out: re-fit without each query, then score that query. This is
    # the honest number - it asks how the rule does on a question it has not
    # been tuned to, which is the only kind production ever sees.
    loo = [0, 0, 0, 0]
    loo_fp: list[str] = []
    for i, held in enumerate(items):
        rest = items[:i] + items[i + 1 :]
        t = fit_domain_thresholds(
            rest, zero_fp_point(rest)["threshold"], domains={s.domain for s in items}
        )[held.domain]
        tp_, fn_, fp_, tn_ = confusion([held], lambda s, t=t: t)
        loo = [a + b for a, b in zip(loo, (tp_, fn_, fp_, tn_), strict=True)]
        if fp_:
            loo_fp.append(f"{held.domain}: {held.query[:70]} ({held.best:.3f} >= {t:.3f})")
    print(f"\nLeave-one-out:\n\n{TABLE_HEAD}")
    print(row_text("LOO", *loo))
    for line in loo_fp:
        print(f"  LOO false positive - {line}")

    return {
        "mode": mode,
        "fitted": fitted,
        "in_sample": (tp, fn, fp, tn),
        "loo": tuple(loo),
        "loo_false_positives": loo_fp,
        "min_domain_margin": min(margins) if margins else None,
        "global": global_point,
    }


def sweep(items: list[Scored], thresholds: list[float]) -> None:
    print(TABLE_HEAD)
    for t in thresholds:
        print(row_text(t, *confusion(items, lambda s, t=t: t)))


def print_point(name: str, point: dict[str, Any]) -> None:
    margin = "undefined" if point["margin"] is None else f"{point['margin']:.4f}"
    print(
        f"\n{name}: lowest zero-FP threshold {point['threshold']:.4f} "
        f"(highest known-absent {point['highest_absent']:.4f}), "
        f"recall {point['recall']:.3f} ({point['retained']}/{point['good']}), margin {margin}"
    )


def domain_main() -> None:
    probes = load_probes()
    for mode in ("unfiltered", "routed"):
        items = [reduce(r, r[mode], "score") for r in probes]
        print(f"\n## Baseline, fused score, retrieval {mode}\n")
        sweep(items, [0.55, 0.60, 0.65, 0.70, 0.75, 0.80])
        print_point(f"baseline ({mode})", zero_fp_point(items))
        domain_report(mode, items)


def loo_global(items: list[Scored]) -> tuple[int, int, int, int]:
    """Leave-one-out for ONE global zero-FP threshold, for comparison with the
    per-domain rule: refit without each query, score that query."""
    total = [0, 0, 0, 0]
    for i, held in enumerate(items):
        rest = items[:i] + items[i + 1 :]
        t = zero_fp_point(rest)["threshold"]
        cell = confusion([held], lambda s, t=t: t)
        total = [a + b for a, b in zip(total, cell, strict=True)]
    return total[0], total[1], total[2], total[3]


# --- option 1: rerankers -----------------------------------------------------


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x)) if x >= 0 else math.exp(x) / (1.0 + math.exp(x))


def labelled_pairs(
    probes: list[dict[str, Any]], raw: dict[str, dict[str, float]], mode: str
) -> list[tuple[float, int]]:
    """(raw score, label) for every pair whose label is known.

    Positive: a known-good query and its target provision. Negative: any
    provision returned for a known-absent query - none of them can answer it,
    by construction of that set. A known-good query's OTHER candidates are left
    out: some of them may well answer it too, and calling them negatives would
    teach the temperature that relevant text is irrelevant.
    """
    out: list[tuple[float, int]] = []
    for row in probes:
        scores = raw[row["query"]]
        for c in row[mode]:
            key = str(c["id"])
            if row["label"] == "absent":
                out.append((scores[key], 0))
            elif (c["citation"], c["section"], c["unit"]) == tuple(row["target"]):
                out.append((scores[key], 1))
    return out


def fit_temperature(pairs: list[tuple[float, int]]) -> float:
    """The temperature minimising negative log-likelihood of sigmoid(z / T).

    One parameter, fitted by golden-section search on log T. A temperature is
    monotonic: it rescales probabilities and cannot reorder anything, so it
    moves the margin and the numeric threshold, never precision or recall.
    """

    def nll(log_t: float) -> float:
        t = math.exp(log_t)
        total = 0.0
        for z, y in pairs:
            p = min(max(_sigmoid(z / t), 1e-12), 1 - 1e-12)
            total -= math.log(p) if y else math.log(1 - p)
        return total

    lo, hi = math.log(0.05), math.log(50.0)
    g = (math.sqrt(5) - 1) / 2
    a, b = hi - g * (hi - lo), lo + g * (hi - lo)
    for _ in range(80):
        if nll(a) < nll(b):
            hi = b
        else:
            lo = a
        a, b = hi - g * (hi - lo), lo + g * (hi - lo)
    return math.exp((lo + hi) / 2)


RERANK_GRID = [0.05, 0.10, 0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90, 0.95, 0.99]


def rerank_report(
    name: str, scores_file: Path, mode: str, temperature: str, probes: list[dict[str, Any]]
) -> dict[str, Any]:
    doc = json.loads(scores_file.read_text(encoding="utf-8"))
    raw: dict[str, dict[str, float]] = doc["scores"]

    t = 1.0
    t_note = "sigmoid of the raw logit"
    if doc["model"] == "laya":
        pairs_ = labelled_pairs(probes, raw, mode)
        shipped = LAYA_SHIPPED_NOUL_TEMPERATURE
        fitted = fit_temperature(pairs_)
        t = fitted if temperature == "refit" else shipped
        t_note = (
            f"temperature {t:.4f} ({temperature}; shipped {shipped:.4f}, refit {fitted:.4f} "
            f"on {sum(y for _, y in pairs_)} positive / {sum(1 - y for _, y in pairs_)} "
            "negative labelled pairs)"
        )

    items = []
    for row in probes:
        cands = [dict(c, rr=_sigmoid(raw[row["query"]][str(c["id"])] / t)) for c in row[mode]]
        items.append(reduce(row, cands, "rr"))

    print(f"\n## Reranker: {name} ({doc['model_id']}), retrieval {mode}\n")
    print(f"score = {t_note}\n")
    sweep(items, RERANK_GRID)
    point = zero_fp_point(items)
    print_point(name, point)
    # The same point on the raw (logit / T) scale. A sigmoid saturates near 1,
    # so two scores 0.9972 and 0.9990 look adjacent while sitting a whole unit
    # of logit apart; the margin in logits is the one that says how much
    # headroom the threshold actually has.
    logit_items = []
    for row in probes:
        cands = [dict(c, z=raw[row["query"]][str(c["id"])] / t) for c in row[mode]]
        logit_items.append(reduce(row, cands, "z"))
    logit_point = zero_fp_point(logit_items)
    margin = logit_point["margin"]
    print(
        f"  on the logit scale: threshold {logit_point['threshold']:.3f}, "
        f"margin {'undefined' if margin is None else f'{margin:.3f}'} logits"
    )
    ceiling = sum(1 for s in items if s.label == "good" and s.target is not None)
    good = sum(1 for s in items if s.label == "good")
    loo = loo_global(items)
    print(f"top-10 ceiling after reranking: {ceiling}/{good}")
    print(f"\n{TABLE_HEAD}\n{row_text('LOO', *loo)}")
    lat = doc["latency_per_query_seconds"]
    print(
        f"\nlatency: load {doc['load_seconds']}s once; per query ({len(raw)} queries, "
        f"{doc['pairs']} pairs, {doc['threads']} CPU threads) median {lat['median']}s, "
        f"p95 {lat['p95']}s, max {lat['max']}s; cost: local CPU, no API spend"
    )
    worst = sorted((s for s in items if s.label == "absent"), key=lambda s: -s.best)[:3]
    for s in worst:
        print(f"  worst known-absent {s.best:.4f}  {s.query[:80]}")
    return {"point": point, "ceiling": ceiling, "loo": loo}


def rerank_main(files: list[Path], mode: str, temperature: str) -> None:
    # Measure on the queries EVERY scores file covers, and score the baseline
    # on exactly those, so a partial run (the migration queries alone) is
    # compared like for like rather than against the full set.
    scored = [set(json.loads(f.read_text(encoding="utf-8"))["scores"]) for f in files]
    probes = [r for r in load_probes() if all(r["query"] in q for q in scored)]
    base = [reduce(r, r[mode], "score") for r in probes]
    print(f"\n## Baseline, fused score, retrieval {mode} ({len(probes)} queries)\n")
    sweep(base, [0.55, 0.60, 0.65, 0.70, 0.75, 0.80])
    print_point("baseline", zero_fp_point(base))
    print(f"\n{TABLE_HEAD}\n{row_text('LOO', *loo_global(base))}")
    for f in files:
        rerank_report(f.stem.removeprefix("scores_"), f, mode, temperature, probes)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("probe", help="retrieve once and cache the shortlists")
    sub.add_parser("domain", help="per-domain thresholds")
    pairs_cmd = sub.add_parser("pairs", help="write pairs + passage text for rerank_local.py")
    pairs_cmd.add_argument("out", type=Path)
    rr = sub.add_parser("rerank", help="measure reranker scores from rerank_local.py")
    rr.add_argument("scores", type=Path, nargs="+")
    rr.add_argument("--mode", choices=["unfiltered", "routed"], default="unfiltered")
    rr.add_argument("--temperature", choices=["refit", "shipped"], default="refit")
    args = parser.parse_args(argv)
    if args.command == "probe":
        probe()
    elif args.command == "domain":
        domain_main()
    elif args.command == "pairs":
        pairs(args.out)
    elif args.command == "rerank":
        rerank_main(args.scores, args.mode, args.temperature)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
