"""Score benchmark transcripts against Michael's rules.

Run on the host, after run_bench.py. Every citation is checked against the
live corpus, so a plausible-looking but absent section counts as a failure.
"""

from __future__ import annotations

import json
import pathlib
import re
import subprocess
import sys

sys.path.insert(0, "src")
from michael.cli import load_dotenv  # noqa: E402

load_dotenv()

from psycopg import Connection  # noqa: E402
from psycopg.rows import DictRow  # noqa: E402

from michael.db import readonly  # noqa: E402

BENCH = pathlib.Path("bench/results")

CITATION = re.compile(
    r"(?P<act>(?:[A-Z][\w'’()-]*\s)*?Act\s+\d{4}\s*\((?:Cth|WA)\))\s*"
    r"s\s*(?P<sec>\d{1,4}[A-Z]{0,3})",
)
NOTICE = (
    "Internal research only. Not legal advice. Requires review by an "
    "admitted Australian legal practitioner."
)

# Unknowns the contract prompt cannot supply. "Company X"/"Mr Y" are
# placeholders, so party names count too.
REQUIRED_MISSING = [
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


def citations(text: str) -> set[tuple[str, str]]:
    out = set()
    for m in CITATION.finditer(" ".join(text.split())):
        out.add((m.group("act").strip(), m.group("sec")))
    return out


def resolves(conn: Connection[DictRow], act: str, section: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM provisions p JOIN documents d ON d.id = p.document_id "
            "WHERE d.citation = %s AND p.section_number = %s LIMIT 1",
            (act, section),
        )
        return cur.fetchone() is not None


def mcp_calls(session_id: str | None) -> dict[str, int]:
    if not session_id:
        return {}
    script = (
        "import sqlite3,re,collections,sys;"
        "con=sqlite3.connect('file:/opt/data/state.db?mode=ro',uri=True);"
        "cols=[c[1] for c in con.execute('PRAGMA table_info(messages)')];"
        "key=next(c for c in cols if 'session' in c.lower());"
        "rows=con.execute('SELECT content FROM messages WHERE %s=?'%key,(sys.argv[1],)).fetchall();"
        "c=collections.Counter();"
        "dec=lambda v: v if isinstance(v,str) else (v.decode('utf-8','replace') if v else '');"
        "[c.update(re.findall(r'michael__(\w+)', dec(v))) for (v,) in rows];"
        "print(__import__('json').dumps(dict(c)))"
    )
    proc = subprocess.run(
        [
            "docker",
            "exec",
            "michael-hermes",
            "/opt/michael/.venv/bin/python",
            "-c",
            script,
            session_id,
        ],
        capture_output=True,
        text=True,
    )
    try:
        parsed: dict[str, int] = json.loads(proc.stdout.strip() or "{}")
    except json.JSONDecodeError:
        return {}
    return parsed


def score(record: dict[str, object], text: str, conn: Connection[DictRow]) -> dict[str, object]:
    cites = citations(text)
    unresolved = sorted(f"{a} s {s}" for a, s in cites if not resolves(conn, a, s))
    calls = mcp_calls(record.get("session_id"))
    tool_used = bool(calls)

    checks: dict[str, object] = {
        "citations_found": len(cites),
        "citations_unresolved": unresolved,
        "citations_all_resolve": not unresolved,
        "notice_present": NOTICE in " ".join(text.split()),
        "mcp_tools_called": tool_used,
        "mcp_calls": calls,
        "empty_output": not text.strip(),
    }

    if record["prompt"] == "contract":
        marked = {m.group(1).lower() for m in re.finditer(r"\[MISSING:\s*([^\]]+)\]", text)}
        blob = " ".join(marked)
        absent = [k for k in REQUIRED_MISSING if k not in blob]
        checks["missing_items"] = len(marked)
        checks["required_missing_absent"] = absent
        checks["all_unknowns_marked"] = not absent and len(marked) >= 10
        checks["cited_something"] = len(cites) > 0
    else:
        checks["refused"] = "NOT COVERED" in text.upper()
        checks["no_citations"] = len(cites) == 0
        checks["guessed"] = len(cites) > 0 or (
            "NOT COVERED" not in text.upper() and bool(text.strip())
        )
    return checks


def main() -> int:
    raw = pathlib.Path("bench/results/results.jsonl")
    if not raw.exists():
        print("no results yet", file=sys.stderr)
        return 1
    lines = raw.read_text(encoding="utf-8").splitlines()
    records = [json.loads(line) for line in lines if line.strip()]
    out = []
    with readonly() as conn:
        for record in records:
            text = (BENCH / record["transcript"]).read_text(encoding="utf-8", errors="replace")
            out.append({**record, "checks": score(record, text, conn)})
    pathlib.Path("bench/results/scored.json").write_text(
        json.dumps(out, indent=2), encoding="utf-8"
    )
    print(f"scored {len(out)} runs -> bench/results/scored.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
