"""Command line entry point.

Thin: every subcommand is one call into :mod:`michael.tools`. The CLI is for
operating Michael by hand (ingestion, spot checks); Hermes uses the tools
directly or over MCP.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from michael.config import PROJECT_ROOT, ConfigError
from michael.domains import DomainConfigError
from michael.draft import DraftingError
from michael.embeddings import EmbeddingError
from michael.ingest import IngestionError
from michael.sources import SourceFetchFailed, SourceRefused


def load_dotenv(path: Path | None = None) -> None:
    """Read .env into the environment without overriding what is already set.

    Deliberately tiny and dependency-free. Values are taken literally; only
    surrounding quotes are stripped.
    """
    target = path or PROJECT_ROOT / ".env"
    if not target.is_file():
        return
    for line in target.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


#: Failures the design produces deliberately. Anything else is a bug and
#: keeps its traceback.
EXPECTED_FAILURES = (
    ConfigError,
    DomainConfigError,
    DraftingError,
    EmbeddingError,
    IngestionError,
    SourceFetchFailed,
    SourceRefused,
)


def _print(payload: Any) -> None:
    if isinstance(payload, str):
        print(payload)
    else:
        print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="michael", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("schema", help="create the database schema (idempotent)")
    sub.add_parser("domains", help="print the domain routing table")
    sub.add_parser("hosts", help="print the ingestion host allowlist")
    sub.add_parser("prompt", help="print MICHAEL.md")

    check = sub.add_parser("check", help="check one Michael output against the rules in MICHAEL.md")
    check.add_argument("path", nargs="?", help="file holding the output; omit to read stdin")

    classify = sub.add_parser("classify", help="classify and route a request")
    classify.add_argument("request")

    search = sub.add_parser("search", help="retrieve provisions")
    search.add_argument("query")
    search.add_argument("--domain")
    search.add_argument("--top-k", type=int)

    draft = sub.add_parser("draft", help="draft from a template, or outline if none matches")
    draft.add_argument("request")
    draft.add_argument("--domain")
    draft.add_argument(
        "--fact",
        action="append",
        default=[],
        metavar="PLACEHOLDER=VALUE",
        help="a known value. Repeatable. Anything not given is marked [MISSING].",
    )

    seed = sub.add_parser("seed", help="seed from the Open Australian Legal Corpus")
    seed.add_argument("--limit", type=int)
    seed.add_argument(
        "--doc-type",
        action="append",
        default=[],
        choices=["act", "regulation", "award", "case"],
        help="only store these document types. Repeatable. Omit to take all.",
    )

    ingest = sub.add_parser("ingest", help="fetch and ingest one URL from an allowlisted host")
    ingest.add_argument("url")
    ingest.add_argument("--jurisdiction", required=True, choices=["wa", "commonwealth"])
    ingest.add_argument("--title", required=True)
    ingest.add_argument("--citation", required=True)
    ingest.add_argument("--doc-type", required=True, choices=["act", "regulation", "award", "case"])
    ingest.add_argument("--snapshot-date")

    ingest_file = sub.add_parser("ingest-file", help="ingest a DOCX/HTML/text file already on disk")
    ingest_file.add_argument("path")
    ingest_file.add_argument(
        "--source-url", required=True, help="where it came from; checked against the allowlist"
    )
    ingest_file.add_argument("--jurisdiction", required=True, choices=["wa", "commonwealth"])
    ingest_file.add_argument("--title", required=True)
    ingest_file.add_argument("--citation", required=True)
    ingest_file.add_argument(
        "--doc-type", required=True, choices=["act", "regulation", "award", "case"]
    )
    ingest_file.add_argument("--snapshot-date")

    user = sub.add_parser("user", help="manage michael-gate accounts")
    user_sub = user.add_subparsers(dest="user_command", required=True)

    user_add = user_sub.add_parser("add", help="create an account")
    user_add.add_argument("email")
    user_add.add_argument("--name", required=True, help="display name")
    user_add.add_argument("--role", required=True, choices=["admin", "chat"])

    user_sub.add_parser("list", help="list accounts")

    user_disable = user_sub.add_parser("disable", help="disable an account")
    user_disable.add_argument("email")

    return parser


def _facts(pairs: list[str]) -> dict[str, str]:
    facts: dict[str, str] = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep:
            raise SystemExit(f"--fact expects PLACEHOLDER=VALUE, got {pair!r}")
        facts[key.strip().upper()] = value.strip()
    return facts


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = build_parser().parse_args(argv)
    try:
        return _run(args)
    except EXPECTED_FAILURES as exc:
        # These are the failures the design produces on purpose - a refused
        # host, an unreachable embedding provider, a missing setting. They are
        # reported, not raised at the operator as a traceback.
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


def _run(args: argparse.Namespace) -> int:
    # Imported here so that `michael --help` works without a database or a
    # configured environment.
    from michael import tools

    match args.command:
        case "schema":
            _print(tools.apply_schema())
        case "domains":
            _print(tools.list_domains())
        case "hosts":
            _print(tools.allowed_hosts())
        case "prompt":
            _print(tools.load_system_prompt())
        case "check":
            from michael.output_check import check as check_output

            text = Path(args.path).read_text(encoding="utf-8") if args.path else sys.stdin.read()
            findings = check_output(text)
            _print(
                {
                    "clean": not findings,
                    "findings": [{"rule": f.rule, "detail": f.detail} for f in findings],
                }
            )
            # A broken output is a non-zero exit, so a script can gate on it.
            return 1 if findings else 0
        case "classify":
            _print(tools.classify_request(args.request))
        case "search":
            _print(tools.search_provisions(args.query, domain=args.domain, top_k=args.top_k))
        case "draft":
            result = tools.draft_document(args.request, facts=_facts(args.fact), domain=args.domain)
            _print(result["document"])
        case "seed":
            _print(tools.seed_corpus(limit=args.limit, doc_types=args.doc_type or None))
        case "ingest-file":
            _print(
                tools.ingest_local_file(
                    path=args.path,
                    source_url=args.source_url,
                    jurisdiction=args.jurisdiction,
                    title=args.title,
                    citation=args.citation,
                    doc_type=args.doc_type,
                    snapshot_date=args.snapshot_date,
                )
            )
        case "ingest":
            _print(
                tools.ingest_source_url(
                    url=args.url,
                    jurisdiction=args.jurisdiction,
                    title=args.title,
                    citation=args.citation,
                    doc_type=args.doc_type,
                    snapshot_date=args.snapshot_date,
                )
            )
        case "user":
            from getpass import getpass

            from michael.gate import users as gate_users

            match args.user_command:
                case "add":
                    # Never taken as an argv argument: it would land in the
                    # operator's shell history and in the process table.
                    password = getpass("password: ")
                    if password != getpass("repeat: "):
                        print("passwords did not match", file=sys.stderr)
                        return 1
                    # Caught here rather than by adding ValueError to
                    # EXPECTED_FAILURES: that tuple names failures the design
                    # produces on purpose, and a bare ValueError there would
                    # swallow genuine bugs across every other subcommand.
                    try:
                        created = gate_users.create_user(
                            args.email, args.name, password, args.role
                        )
                    except ValueError as exc:
                        print(str(exc), file=sys.stderr)
                        return 1
                    _print({"email": created.email, "role": created.role})
                case "list":
                    _print(
                        [
                            {
                                "email": u.email,
                                "name": u.display_name,
                                "role": u.role,
                                "disabled": u.disabled_at is not None,
                            }
                            for u in gate_users.list_users()
                        ]
                    )
                case "disable":
                    if not gate_users.disable_user(args.email):
                        print(f"no such active account: {args.email}", file=sys.stderr)
                        return 1
                    _print({"disabled": args.email})
        case _:  # pragma: no cover - argparse rejects anything else
            return 2
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
