# Michael

An internal legal research and drafting assistant for a single user. Not
published, not multi-tenant. Optimised for correctness and traceability, not
for scale.

**Michael is not a lawyer and does not give legal advice.** Every output ends
with OPEN ITEMS, VERIFY BEFORE USE, and a notice that the work requires review
by an admitted Australian legal practitioner.

## Architecture

Hermes is the brain and the only orchestrator. It receives the scenario, plans,
calls tools, and writes the answer. Everything in `src/michael/` is a tool
Hermes calls. There is no second agent framework here.

```
Hermes ──▶ classify_request ──▶ search_provisions ──▶ draft_document
             (domains.yaml)      (BM25 + vector,       (templates/,
                                  read-only)            [MISSING] rule)
```

Ingestion is a separate tool group with its own database role. The answering
path connects as `michael_ro`, which holds SELECT only and is created with
`default_transaction_read_only = on`, so it cannot write even by mistake.

## Setup

```bash
cp .env.example .env    # then fill it in; .env is git-ignored
docker compose up -d
uv sync --all-groups
uv run michael schema
```

Seed the corpus (needs the `corpus` extra):

```bash
uv sync --extra corpus
uv run michael seed --limit 500
```

Fill a gap from an allowlisted host:

```bash
uv run michael ingest https://www.legislation.gov.au/... \
  --jurisdiction commonwealth --doc-type act \
  --title "Fair Work Act 2009" --citation "Fair Work Act 2009 (Cth)"
```

Only `legislation.wa.gov.au`, `legislation.gov.au`, `fairwork.gov.au` and
`austlii.edu.au` (and their subdomains) are fetchable. Every other host is
refused and logged. There is no configuration key that widens this.

## Using it

```bash
uv run michael classify "I need a casual employment contract for a new hire"
uv run michael search "casual employee entitlements"
uv run michael draft "casual employment contract" --fact EMPLOYER_NAME="Palm Vision Pty Ltd"
```

To attach it to an agent runtime over MCP:

```bash
uv sync --extra mcp
uv run python -m michael.mcp_server
```

## Retrieval

Hybrid: BM25 over the Postgres `tsvector` (one tokeniser and one stemmer end to
end) fused with pgvector cosine similarity, each squashed to `[0, 1)` and
weighted 0.5/0.5. The threshold is therefore absolute and meaningful.

If the best fused score is below `RETRIEVAL_MIN_SCORE` the result is **empty**.
The nearest guess is never returned. An empty result travels as a value —
`covered: false` plus a `NOT COVERED — run ingestion for <topic>` line — so it
is reported, not silently dropped.

### Calibrating the threshold

`RETRIEVAL_MIN_SCORE = 0.60`, calibrated — not guessed.

The labelled set is `calibration/labelled_queries.json`: 15 known-good queries
paired with the provision each should return, and 10 known-absent queries that
must return nothing. Queries are paraphrases rather than verbatim provision
text, so the test is not trivially lexical. Reproduce with:

```bash
uv run python calibration/calibrate.py
```

Scoring runs **unfiltered**, with no domain filter. A jurisdiction filter would
reject most known-absent queries before scoring and flatter the result;
unfiltered, the threshold alone has to do the work.

| threshold | TP | FN | FP | TN | precision | recall |
|---|---|---|---|---|---|---|
| 0.30 | 15 | 0 | 10 | 0 | 0.600 | 1.000 |
| 0.35 | 15 | 0 | 9 | 1 | 0.625 | 1.000 |
| 0.50 | 15 | 0 | 6 | 4 | 0.714 | 1.000 |
| 0.55 | 15 | 0 | 2 | 8 | 0.882 | 1.000 |
| **0.60** | **15** | **0** | **0** | **10** | **1.000** | **1.000** |
| 0.65 | 14 | 1 | 0 | 10 | 1.000 | 0.933 |
| 0.70 | 10 | 5 | 0 | 10 | 1.000 | 0.667 |
| 0.80 | 1 | 14 | 0 | 10 | 1.000 | 0.067 |

A false positive is the failure that matters — answering a question the corpus
cannot answer is the "nearest guess" the design forbids — so the chosen value is
the **lowest threshold with zero false positives**. At 0.60 that costs nothing:
recall is also 1.000.

Highest known-absent score is 0.574; lowest retained known-good score is 0.653
(*Fair Work Act 2009 (Cth)* s 15A). The **margin is 0.079** — against 0.034
before the contents-entry fix, which removed the short table-of-provisions rows
that were both inflating absent-query scores and outranking real sections.

Re-derive after any change to the corpus, the embedding model, the splitter or
the fusion weights; none of those preserve this scale. The known-absent set also
has to be re-checked when the corpus grows: two Fair Work queries were retired
from it once the Act was ingested, because they stopped being absent.

## Drafting

Templates are markdown in `templates/` with `{{PLACEHOLDER}}` markers. The
[MISSING] rule is enforced in code, not left to the model: `fill()` substitutes
only values that were passed in, and everything else becomes
`[MISSING: <item>]`. No code path produces a party name, ABN, address, date,
pay rate, award name, classification level or superannuation fund from
anything other than the caller's facts.

If no template matches, Michael does not refuse. It produces a clause-level
outline grounded in the retrieved provisions, labels it `DRAFT - NO TEMPLATE`,
and writes the outline to `templates/drafts/` for review.

## Known limitations

**Case law is chunked and cited as if it were legislation.** `split_sections()`
matches numbered judgment paragraphs and stores them in `section_number`, with
the paragraph's first line in `heading`. `pinpoint()` then renders them as
`Muir v Open Brethren [1956] HCA 14 s 2` — citing a judgment paragraph as a
*section*, which is wrong. Observed on a real seed:

```
section_number | heading
2              | (1842) 5 Beav., at p. 303 [49 E.R., at p. 594].
6              | The Tang respondents are:
```

Until this is fixed, seed legislation only:

```bash
uv run michael seed --limit 200 --doc-type act --doc-type regulation
```

The fix needs a decision on whether cases are chunked by paragraph and cited as
`[2]`, or handled by a separate splitter. The section splitter is correct for
legislation, which is what the acceptance test exercises.

**The calibrated threshold is stale.** `RETRIEVAL_MIN_SCORE = 0.65` was derived
against a WA-only corpus. The Fair Work Act 2009 (Cth) has since been ingested,
and at 0.65 the most apposite provisions for a casual-employment draft fall just
below the line:

```
KEPT   0.6551  s 359C  [operative   93w]  Misrepresentation to engage as casual employee
cut    0.6338  s 47A   [operative  198w]  Casual employees of small business employers
cut    0.6243  s 125B  [operative  200w]  Giving employees the Casual Employment Info Statement
cut    0.6194  s 15A   [operative  924w]  Meaning of casual employee
```

Re-run `calibration/calibrate.py` with Commonwealth cases added to
`calibration/labelled_queries.json` before relying on the current value. A
threshold is only valid for the corpus it was measured against.

The same listing shows the contents-entry problem biting: the 12-word contents
line for s 125B scores 0.6298, *above* the 200-word operative section at 0.6243.

**Tables of provisions are ingested as provisions.** Each Act repeats its
section numbers and headings in a contents table at the front, and
`split_sections()` cannot tell those lines from the real sections. On the
current seed, 11,366 provisions cover only 6,387 distinct (document, section)
pairs — roughly 44% are contents entries. They are short and carry no operative
text:

```
section | char_start | tokens | text
8       |        380 |     13 | 8. Duty to minimise risk from dangerous goods 1
8       |      10952 |    113 | 8. Duty to minimise risk from dangerous goods (1) A person who i...
```

The effect is duplicate pinpoints in results and a diluted index. Raising
`MIN_PROVISION_CHARS`, or detecting the contents table and skipping it, would
fix it — but changing the splitter changes the corpus, so
`calibration/calibrate.py` must be re-run afterwards and the threshold
re-derived.

**The Federal Register's authorised text cannot be fetched programmatically.**
`/latest/text` is a client-rendered page whose HTML carries only the table of
provisions; every download path returns the same SPA shell; the OData API
exposes metadata but not file content; and AustLII returns 403 to non-browser
clients. Download the Word volumes by hand and ingest them with provenance
intact:

```bash
uv run michael ingest-file "Fair Work Act 2009 Vol 1.docx"   --source-url https://www.legislation.gov.au/C2009A00028/latest/downloads   --jurisdiction commonwealth --doc-type act   --title "Fair Work Act 2009" --citation "Fair Work Act 2009 (Cth)"
```

`ingest-file` still checks `--source-url` against the host allowlist, so a local
file cannot be used to launder an off-allowlist source. DOCX and HTML are
converted to text by `docx_text.py` and `html_text.py`; the sha256 is taken over
the original bytes either way, so deriving text never weakens provenance.

**Purging documents wipes the database audit log.** `ingestion_log` carries a
foreign key to `documents`, so `TRUNCATE documents CASCADE` truncates it too.
The durable record is the append-only file at `sources/ingestion.log.jsonl`,
which is never truncated.

**Very long sections are embedded from their opening only.** Embedding
providers cap input (8192 tokens for `text-embedding-3-small`), so
`EMBEDDING_MAX_CHARS` (default 24,000) caps what is *sent*. The provision
stored in the database keeps its full verbatim text — that is what gets quoted
and cited — and the BM25 arm indexes every word of it. Only the vector for an
oversized section is computed from its opening portion.

**Seeding is one transaction.** `seed_from_corpus` commits once at the end, so a
failure part-way rolls back cleanly and leaves nothing half-written, but a large
`--limit` means a long-running transaction and no partial progress. Seed in
batches if that matters.

**The schema pins the embedding dimension at creation.** `apply_schema()` reads
`EMBEDDING_DIM` when it first creates `provisions`, and `CREATE TABLE IF NOT
EXISTS` will not correct a later mismatch. Changing the embedding model to one
with a different dimension needs a migration, which does not exist yet.

## Hermes

Hermes Agent v0.21.0 drives Michael as its orchestrator. Michael's stdio MCP
server is installed *into* the Hermes image: Hermes runs stdio servers as
subprocesses inside its own container, so a host path would not resolve, and a
Windows `.venv` holds binaries a Linux container cannot execute.

```bash
uv run python hermes/render_config.py      # fills secrets from .env
docker compose -f hermes/docker-compose.yml up -d --build
docker exec michael-hermes hermes -z "your question"
```

**Model choice is benchmarked, not assumed.** `deepseek/deepseek-v4-pro`, chosen
over `claude-opus-5` by 42 runs (7 models × 2 prompts × 3 runs) over the
acceptance prompt and a deliberately uncovered question. Harness in `bench/`,
raw results in `bench/results/`. Disqualification was any single rule breach.

| model | mean $/run | verdict |
|---|---|---|
| anthropic/claude-opus-5 | 0.23645 | disqualified — no closing notice (1/6) |
| anthropic/claude-sonnet-5 | 0.03949 | **pass** |
| **deepseek/deepseek-v4-pro** | **0.01720** | **pass — selected** |
| anthropic/claude-haiku-4.5 | 0.01432 | disqualified — zero MCP calls, no notice |
| deepseek/deepseek-v4-flash | 0.00466 | disqualified — no closing notice (1/6) |
| openai/gpt-oss-120b | 0.00217 | disqualified — answered with zero MCP calls |
| qwen/qwen3.7-flash | 0.00078 | disqualified — 6/6 breaches |

Cost is actual OpenRouter spend, from diffing the key's cumulative usage around
each run; the `--usage-file` figure is self-declared "estimated".

**No model fabricated a citation.** Every citation in all 42 runs resolved to a
provision in the corpus. The failures were all about the *output contract*, not
invented law.

**Four of seven models dropped the closing notice on the uncovered path**, and
all four otherwise refused correctly. That points at MICHAEL.md rather than the
models: "If retrieval is empty, reply NOT COVERED ... **and stop**" reads as
licence to skip the "Every output ends with" blocks. Worth resolving in the
prompt — it is currently the single most common failure mode, and it disqualified
the incumbent.

**Pin the provider when overriding the model.** `hermes -m anthropic/claude-sonnet-5`
routes to provider `gmi` and fails with no credentials; `--provider openrouter`
is required, and is also what keeps spend on the measured key.

**One container, not two.** The dashboard's chat talks to the gateway over
localhost, so they must share a network namespace. Upstream's Linux compose
splits them and relies on `network_mode: host`, which Docker Desktop for Windows
does not support; on a bridge network the dashboard reports "Gateway Status:
Stopped" and never opens a websocket, because its localhost is not the
gateway's. `HERMES_DASHBOARD=1` has s6 supervise both in one container.

**The dashboard requires auth.** A non-loopback bind is refused outright without
an auth provider, and `--insecure` has been a no-op since the June 2026
hardening — the process exits 0 and `restart: unless-stopped` loops it, which
presents to the browser as ERR_EMPTY_RESPONSE. Inside a container the bind must
be `0.0.0.0` for Docker's port proxy to reach it, so credentials are mandatory:
`HERMES_DASHBOARD_BASIC_AUTH_USERNAME` / `_PASSWORD` in `hermes.env`. The
published port is `127.0.0.1` only.

**`agent.disabled_toolsets` lives in `config.yaml`,** the same file
`render_config.py` writes — so it is declared in the template. It is not
per-platform: omitting it once restored the full toolset to the web chat while
the CLI stayed restricted.

The image tag is `nousresearch/hermes-agent:v2026.8.31` — there is no `v0.21.0`
tag; the registry uses date tags, and `hermes --version` in that image reports
`v0.21.0 (2026.8.31)`.

Its own container (`michael-hermes`), volume (`michael_hermes_data`) and port
(`127.0.0.1:9120`). It joins the `michael_default` network and reaches Postgres
as `michael-postgres:5432`, so the database is never exposed to the host for it.

**Persona.** `/opt/data/SOUL.md` is a byte-identical copy of `MICHAEL.md`,
verified by sha256 at deploy time. `MICHAEL.md` is the source of truth and is
never edited.

**Environment.** Hermes filters the environment of stdio MCP subprocesses to a
small allowlist, so Michael's settings are declared in the `env:` block of the
server entry in `config.yaml`. Inheriting them from the container does not work
— the first deployment failed exactly this way.

**Web search is a locator, not a source.** The `web` toolset stays enabled so
the agent can find a document worth ingesting, but MICHAEL.md forbids citing a
web page, search result, snippet or summary: only a provision retrieved from the
corpus may be cited, and a web hit never converts NOT COVERED into covered.

That rule is a prompt-level control, and prompt-level controls are not
guarantees. The structural guarantee sits underneath it: the `BASED ON`
citations in a draft are generated by `draft.citations_of()` from the provisions
`retrieve.search()` returned, so a web page cannot become a citation there
whatever the model writes in prose. Anything outside that block is the model's
own text and is checked by reading it.

**Tool posture.** Everything that can write a file or execute code is disabled:

```bash
hermes tools disable terminal file code_execution computer_use                      skills cronjob delegation browser image_gen tts
```

`skills`, `cronjob` and `delegation` are included because each is an indirect
route back to execution — installing a package, scheduling a job, or spawning a
subagent with its own tools. `--safe-mode` is **not** the mechanism for this: it
is a troubleshooting flag that disables all customisation *including MCP
servers*, which would switch Michael off.

## Layout

```
docker-compose.yml       pgvector/pg16, loopback-bound, own volume
.env.example             every key; .env is never committed
MICHAEL.md               the system prompt, loaded on every request
domains.yaml             domain routing: filters and template dirs only
db/init/                 creates the read-only role on first start
src/michael/
  config.py  db.py  schema.py  sources.py  embeddings.py
  bm25.py  ingest.py  retrieve.py  domains.py  draft.py
  tools.py  cli.py  mcp_server.py
templates/               markdown templates with explicit placeholders
sources/                 downloaded originals, content-addressed, immutable
tests/
```

## Checks

```bash
uv run ruff check . && uv run ruff format --check .
uv run mypy . && uv run pytest
```

`pytest -m integration` additionally needs the container running; those tests
are excluded from the default run.
