"""Final scorecard: cost, citations, [MISSING], notice, refusal, MCP usage.

MCP usage comes from the sessions table, joined on model and first user
message, because --usage-file never recorded a session id. For each
(model, prompt) the most recent three sessions are the benchmark's own, so
earlier sessions from previous runs cannot leak in.
"""

from __future__ import annotations

import json
import pathlib
import re
import statistics as st
import sys
from typing import Any

sys.path.insert(0, "src")
from michael.cli import load_dotenv  # noqa: E402

load_dotenv()

from michael.db import readonly  # noqa: E402

RESULTS = pathlib.Path("bench/results")
# The act name is Title Case with lowercase connectors. Requiring a lowercase
# letter after the initial capital stops an ALL-CAPS heading being absorbed:
# a "BASED ON" heading followed by "- Fair Work Act 2009 (Cth) s 15A" flattens
# to one line, where a looser pattern captured the act as "BASED ON Fair Work
# Act 2009 (Cth)" and then failed to resolve it - a false failure.
# The first token of an act name must be Title Case - an initial capital
# followed by a lowercase letter. That excludes an ALL-CAPS heading ("BASED
# ON") and a leading connector ("and"), both of which a looser pattern
# absorbed into the act name and then failed to resolve, scoring a false
# failure against the model. Connectors are allowed inside the name only.
ACT_NAME = (
    r"[A-Z][a-z][\w'’()-]*"
    r"(?:\s(?:[A-Z][a-z][\w'’()-]*|of|and|the|for))*"
    r"\s+Act\s+\d{4}\s*\((?:Cth|WA)\)"
)
CITATION = re.compile(rf"(?P<act>{ACT_NAME})\s*s\s*(?P<sec>\d{{1,4}}[A-Z]{{0,3}})")


def _self_test() -> None:
    """A checker that has produced false failures twice gets tested."""
    cases = [
        ("## BASED ON - Fair Work Act 2009 (Cth) s 15A", "Fair Work Act 2009 (Cth)", "15A"),
        ("under the Fair Work Act 2009 (Cth) s 125B and", "Fair Work Act 2009 (Cth)", "125B"),
        ("Cat Act 2011 (WA) s 18", "Cat Act 2011 (WA)", "18"),
        (
            "Minimum Conditions of Employment Act 1993 (WA) s 8",
            "Minimum Conditions of Employment Act 1993 (WA)",
            "8",
        ),
    ]
    for text, act, sec in cases:
        m = CITATION.search(text)
        assert m, f"no match in {text!r}"
        assert m.group("act") == act, f"{text!r}: act={m.group('act')!r} expected {act!r}"
        assert m.group("sec") == sec, f"{text!r}: sec={m.group('sec')!r}"
    assert not CITATION.search("the Act 2009 says"), "matched a non-citation"


_self_test()

NOTICE = (
    "Internal research only. Not legal advice. Requires review by an "
    "admitted Australian legal practitioner."
)
REQUIRED = [
    "abn",
    "address",
    "position",
    "commencement",
    "award",
    "classification",
    "rate",
    "loading",
    "superannuation",
    "notice",
]


def main() -> int:
    records = [
        json.loads(line)
        for line in (RESULTS / "results.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    sessions = json.loads((RESULTS / "sessions.json").read_text(encoding="utf-8-sig"))

    # The benchmark's own sessions: last three per (model, kind).
    by_pair: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for s in sessions:
        if s["kind"] == "other":
            continue
        by_pair.setdefault((s["model"], s["kind"]), []).append(s)
    mcp_for: dict[tuple[str, str], list[dict[str, Any]]] = {
        k: sorted(v, key=lambda x: str(x["session_id"]))[-3:] for k, v in by_pair.items()
    }

    order: list[str] = []
    for r in records:
        if r["model"] not in order:
            order.append(r["model"])

    with readonly() as conn, conn.cursor() as cur:

        def resolves(act: str, sec: str) -> bool:
            cur.execute(
                "SELECT 1 FROM provisions p JOIN documents d ON d.id=p.document_id "
                "WHERE d.citation=%s AND p.section_number=%s LIMIT 1",
                (act, sec),
            )
            return cur.fetchone() is not None

        rows = []
        for r in records:
            text = (RESULTS / r["transcript"]).read_text(encoding="utf-8", errors="replace")
            flat = " ".join(text.split())
            cites = {(m.group("act").strip(), m.group("sec")) for m in CITATION.finditer(flat)}
            bad = sorted(f"{a} s {s}" for a, s in cites if not resolves(a, s))
            marked = {m.group(1).lower() for m in re.finditer(r"\[MISSING:\s*([^\]]+)\]", text)}
            blob = " ".join(marked)
            fails = []
            if bad:
                fails.append(f"citation not in corpus: {bad[0]}")
            if NOTICE not in flat:
                fails.append("closing notice missing")
            if r["prompt"] == "contract":
                absent = [k for k in REQUIRED if k not in blob]
                if absent or len(marked) < 10:
                    fails.append(f"unknowns not all [MISSING] (absent: {absent[:3]})")
                if not cites:
                    fails.append("no citation at all")
            else:
                if "NOT COVERED" not in flat.upper():
                    fails.append("no NOT COVERED line")
                # A citation here is only a breach if it is invented, which the
                # resolve check above already catches. Naming the provisions
                # retrieval rejected - "the two hits were s 504 ..." - is
                # transparency about a near miss, not an answer drawn from it,
                # and the NOT COVERED line is what says the question is
                # unanswered.
            rows.append({**r, "cites": len(cites), "marked": len(marked), "fails": fails})

    # Zero-MCP runs are a failure of the model, attributed per (model, kind).
    zero: dict[str, list[str]] = {}
    for (model, kind), ss in mcp_for.items():
        for s in ss:
            if s["michael_total"] == 0:
                zero.setdefault(model, []).append(f"{kind} (session {str(s['session_id'])[-6:]})")

    print(f"{'model':<28}{'mean $':>9}{'range':>18}  verdict")
    print("-" * 100)
    summary = []
    for m in order:
        rs = [x for x in rows if x["model"] == m]
        costs = [x["actual_cost_usd"] for x in rs if x["actual_cost_usd"] > 0]
        mean = st.mean(costs) if costs else 0.0
        fails = [f"{x['prompt']} r{x['run']}: {f}" for x in rs for f in x["fails"]]
        if m in zero:
            fails += [f"zero MCP calls: {z}" for z in zero[m]]
        verdict = "PASS" if not fails else "DISQUALIFIED"
        summary.append((m, mean, verdict, fails))
        rng = f"{min(costs):.5f}-{max(costs):.5f}" if costs else "n/a"
        print(f"{m[:27]:<28}{mean:>9.5f}{rng:>18}  {verdict}")
        for f in fails[:4]:
            print(" " * 57 + f"- {f}")
        if len(fails) > 4:
            print(" " * 57 + f"- ... and {len(fails) - 4} more")

    survivors = sorted([(m, c) for m, c, v, _ in summary if v == "PASS"], key=lambda x: x[1])
    print("\nsurvivors, cheapest first:")
    for m, c in survivors:
        print(f"  {m:<30} ${c:.5f}/run")
    total = sum(x["actual_cost_usd"] for x in rows)
    print(f"\ntotal benchmark spend: ${total:.4f}")
    if survivors:
        base = next((c for m, c, _, _ in summary if m == "anthropic/claude-opus-5"), 0.0)
        m, c = survivors[0]
        extra = f" ({base / c:.1f}x cheaper than opus-5)" if c and base else ""
        print(f"recommend: {m} at ${c:.5f}/run{extra}")
    json.dump(rows, (RESULTS / "final_scored.json").open("w", encoding="utf-8"), indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
