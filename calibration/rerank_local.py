"""Score cached (query, provision) pairs with a local reranker.

Runs in an environment that has torch and transformers (and ``laya`` for the
Laya model) - deliberately NOT Michael's own, which carries neither. It never
imports michael: it reads the pairs ``separation.py pairs`` wrote and writes
raw scores back for ``separation.py rerank`` to measure.

    <python-with-torch> calibration/rerank_local.py PAIRS OUT --model minilm
    <python-with-torch> calibration/rerank_local.py PAIRS OUT --model bge
    <python-with-torch> calibration/rerank_local.py PAIRS OUT --model laya --laya-dir DIR

Scores are RAW - a cross-encoder's logit, Laya's yes-minus-no logit - so that
calibration (a sigmoid, or Laya's refitted temperature) is applied in one
place, by separation.py, where the labels are.

Latency is measured per query: one forward pass over that query's whole
shortlist, on CPU, after the model is loaded. Load time is reported apart.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import time
from pathlib import Path
from typing import Any

os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

import torch  # noqa: E402

MODELS = {
    "minilm": "cross-encoder/ms-marco-MiniLM-L-6-v2",
    "bge": "BAAI/bge-reranker-base",
}

#: The judgment Laya is asked for each pair: a Noul, Jev-style. It asks whether
#: the provision ANSWERS the question, not whether it is on the same topic -
#: the distinction the fused score fails to draw.
LAYA_QUESTION = {
    "answers": {
        "type": "noul",
        "instructions": (
            "Does the text of `provision` itself state the law that answers `question`? "
            "Yes only if the provision's own words answer it. A provision on a related "
            "topic, or about a different jurisdiction, person or situation, is no."
        ),
    }
}

MAX_TOKENS = 512


def cross_encoder_scorer(name: str) -> Any:
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    tok = AutoTokenizer.from_pretrained(name)
    model = AutoModelForSequenceClassification.from_pretrained(name).eval()

    def score(query: str, passages: list[str]) -> list[float]:
        batch = tok(
            [query] * len(passages),
            passages,
            padding=True,
            truncation="only_second",
            max_length=MAX_TOKENS,
            return_tensors="pt",
        )
        with torch.inference_mode():
            logits = model(**batch).logits
        return logits.view(-1).float().tolist()

    return score


def laya_scorer(model_dir: str) -> Any:
    import laya
    from laya.common import QTYPES, build_sequence, collate_items

    agent = laya.load(model_dir, device="cpu")
    q = agent._to_internal(LAYA_QUESTION["answers"])
    max_len = agent.cfg.get("max_len", 512)
    head_max_len = agent.cfg.get("head_max_len", 192)

    def score(query: str, passages: list[str]) -> list[float]:
        # The same forward pass Agent.system_one runs, batched over the
        # shortlist, returning the raw yes-minus-no logit rather than a
        # probability already divided by the shipped temperature and rounded
        # to four places - rounding would tie every near-certain pair.
        items = []
        for passage in passages:
            state = {"question": query, "provision": passage}
            seq, markers = build_sequence(agent.tok, state, q, max_len, head_max_len)
            items.append({"ids": seq, "markers": markers, "qtype": QTYPES["noul"]})
        b = collate_items([items], agent.tok.pad_token_id)
        with torch.inference_mode():
            logits, _ = agent.model(
                b["input_ids"], b["attention_mask"], b["marker_pos"], b["marker_mask"], b["qtype"]
            )
        logits = logits.float()
        return (logits[:, 1] - logits[:, 0]).tolist()

    return score


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("pairs", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--model", required=True, choices=["minilm", "bge", "laya"])
    ap.add_argument("--laya-dir")
    ap.add_argument("--threads", type=int, default=6)
    args = ap.parse_args()
    torch.set_num_threads(args.threads)

    data = json.loads(args.pairs.read_text(encoding="utf-8"))
    texts: dict[str, str] = data["texts"]

    t0 = time.perf_counter()
    if args.model == "laya":
        if not args.laya_dir:
            ap.error("--laya-dir is required for --model laya")
        scorer = laya_scorer(args.laya_dir)
        model_id = f"laya-english ({args.laya_dir})"
    else:
        scorer = cross_encoder_scorer(MODELS[args.model])
        model_id = MODELS[args.model]
    load_s = time.perf_counter() - t0

    scores: dict[str, dict[str, float]] = {}
    per_query: list[float] = []
    for i, item in enumerate(data["queries"], start=1):
        ids = [str(x) for x in item["ids"]]
        t = time.perf_counter()
        raw = scorer(item["query"], [texts[x] for x in ids])
        per_query.append(time.perf_counter() - t)
        scores[item["query"]] = dict(zip(ids, raw, strict=True))
        if i % 10 == 0:
            print(f"  {args.model}: {i}/{len(data['queries'])}", flush=True)

    per_query.sort()
    out = {
        "model": args.model,
        "model_id": model_id,
        "threads": args.threads,
        "load_seconds": round(load_s, 2),
        "pairs": sum(len(v) for v in scores.values()),
        "latency_per_query_seconds": {
            "median": round(statistics.median(per_query), 4),
            "p95": round(per_query[int(0.95 * (len(per_query) - 1))], 4),
            "max": round(per_query[-1], 4),
        },
        "scores": scores,
    }
    args.out.write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    print(
        f"{args.model}: {out['pairs']} pairs, load {load_s:.1f}s, "
        f"median {out['latency_per_query_seconds']['median']}s/query -> {args.out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
