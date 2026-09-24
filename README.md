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

`RETRIEVAL_MIN_SCORE = 0.60` is **not currently supported by measurement.** It
was calibrated against a 200-document WA-only corpus with 15 known-good and 10
known-absent queries. Re-measured on 2026-09-24 against the production corpus
(205 documents, 8,915 provisions, including the Privacy Act 1988 (Cth), with
`corpus_stats` freshly refreshed) using an expanded labelled set of 48
known-good and 33 known-absent queries, it admits **11 false positives out of
33** and retrieves only 30 of 48 known-good targets.

The labelled set is `calibration/labelled_queries.json`. Queries are paraphrases
rather than verbatim provision text, so the test is not trivially lexical.
Reproduce with:

```bash
uv run python calibration/calibrate.py
```

Scoring runs **unfiltered**, with no domain filter. A jurisdiction filter would
reject most known-absent queries before scoring and flatter the result;
unfiltered, the threshold alone has to do the work.

| threshold | TP | FN | FP | TN | precision | recall |
|---|---|---|---|---|---|---|
| 0.30 | 38 | 10 | 33 | 0 | 0.535 | 0.792 |
| 0.35 | 38 | 10 | 32 | 1 | 0.543 | 0.792 |
| 0.40 | 38 | 10 | 32 | 1 | 0.543 | 0.792 |
| 0.45 | 37 | 11 | 31 | 2 | 0.544 | 0.771 |
| 0.50 | 37 | 11 | 26 | 7 | 0.587 | 0.771 |
| 0.55 | 33 | 15 | 20 | 13 | 0.623 | 0.688 |
| 0.60 | 30 | 18 | 11 | 22 | 0.732 | 0.625 |
| 0.65 | 23 | 25 | 2 | 31 | 0.920 | 0.479 |
| 0.70 | 13 | 35 | 0 | 33 | 1.000 | 0.271 |
| 0.75 | 9 | 39 | 0 | 33 | 1.000 | 0.188 |
| 0.80 | 1 | 47 | 0 | 33 | 1.000 | 0.021 |

**No threshold separates the two sets.** The lowest threshold with zero false
positives is 0.70, and there recall is 0.271 — 13 of 48 known-good queries
survive. The highest known-absent score is 0.6669 ("what privacy obligations
apply to Queensland government agencies handling personal information", which
returns APP 9). 29 of 48 known-good targets — 60% — score at or below that,
including 10 that never enter the top 10 at all.

Recall of 0.9 is unreachable at **any** threshold, including no threshold: with
the cutoff disabled entirely, only 38 of 48 targets rank in the top 10, a
ceiling of 0.792. That is a retrieval-quality limit, not a threshold choice, so
no value of `RETRIEVAL_MIN_SCORE` fixes it.

Accordingly **no new value has been adopted**, and `.env` is unchanged at 0.60.
Changing the number cannot help: at 0.60 the search answers 11 questions the
corpus cannot answer, and lifting it to 0.70 to stop that discards three
quarters of the questions it can. Both failures are the same defect — the fused
score does not order "covered" above "not covered" on this corpus. See the open
decisions below.

Re-derive after any change to the corpus, the embedding model, the splitter or
the fusion weights; none of those preserve this scale. The known-absent set also
has to be re-checked whenever the corpus grows: two Fair Work queries were
retired from it once that Act was ingested, because they stopped being absent.
On the 2026-09-24 re-check **no query was retired** — all ten pre-existing
known-absent queries remain genuinely uncovered.

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

**The threshold does not separate covered from uncovered questions.** Measured
2026-09-24 against the production corpus with 48 known-good and 33 known-absent
queries: the lowest threshold with zero false positives is 0.70, at which recall
is 0.271. The configured 0.60 admits 11 false positives out of 33. See
"Calibrating the threshold" above for the full table.

This is the retrieval quality limit, not a tuning problem, and it is the
blocking issue for the NOT COVERED guarantee: MICHAEL.md promises that an empty
result means "the corpus cannot answer this", and on the current corpus a
0.60 cutoff breaks that promise on a third of questions built to be uncoverable.
An earlier value of 0.65, derived against a WA-only corpus, is superseded and is
no better — it still admits 2 false positives at recall 0.479.

The leading suspect is **the Privacy Act's internal near-duplication.** Part
IIIA (credit reporting, ss 20A–22F) restates access, correction, quality,
security and notification for credit information, in language close to the
Australian Privacy Principles in Schedule 1. Plain-English APP questions return
the credit-reporting provisions instead: "can an individual demand a copy of
what a company holds about them" returns ss 20T, 21V and 20B, and never APP 12.
Eight of the ten known-good targets that never rank are Privacy Act APP or Part
IIIC provisions.

**Ruled out:** stale corpus statistics. `corpus_stats` had drifted to 8,982
provisions against an actual 8,915 after a note-merge deleted 67 rows without
refreshing, and BM25 reads N and avgdl from that row. It was refreshed on
2026-09-24 and the whole labelled set re-scored. Maximum movement on any fused
score was 0.00025; every headline figure was identical. Worth knowing, because
"the statistics were stale" is the obvious explanation and it is not the answer.

**Tables of provisions were ingested as provisions — fixed, with a residue.**
Each Act repeats its section numbers and headings in a contents table at the
front. `_is_contents_entry()` now separates those rows from operative headings
by looking at the *neighbouring* lines rather than at the trailing number: a
table of provisions paginates every row, so its entries cluster, whereas a
cited Act's year stands alone in a body that is not paginated inline.

The first version of that rule tested the trailing number alone, which made it
drop every heading ending in a cited year — `26WD Exception—notification under
the My Health Records Act 2012`, `80P … Freedom of Information Act 1982`,
`7B … organisations 1988` — in every document, silently.

**The residue:** the corpus predates the fix, so it is still missing every such
heading. The extent has not been measured, and whether to re-ingest the
existing documents is an open decision. A re-ingest changes the corpus and
therefore voids the calibrated threshold.

**The Federal Register's authorised text needs the right URL, not a browser.**
`/latest/text` is a client-rendered page whose HTML carries only the table of
provisions, and the OData API exposes metadata but not file content — so a
naive fetch of an Act returns headings and looks like a successful ingest. That
mistake put a headings-only Privacy Act page into the corpus once.

The dated Word original *is* fetchable, and `michael ingest` handles it end to
end — fetch, sha256 over the original bytes, DOCX to text, section split,
ingestion log:

```bash
uv run michael ingest   "https://www.legislation.gov.au/C2004A03712/2026-06-04/2026-06-04/text/original/word"   --jurisdiction commonwealth --doc-type act --snapshot-date 2026-06-04   --title "Privacy Act 1988" --citation "Privacy Act 1988 (Cth)"
```

That path produced 355 provisions, 29 of them in Part IIIC, with `26WD`
carrying operative subsections rather than a heading alone.

AustLII still returns 403 to non-browser clients. For anything that genuinely
cannot be fetched, download the volumes by hand and ingest them with provenance
intact:

```bash
uv run michael ingest-file "Fair Work Act 2009 Vol 1.docx"   --source-url https://www.legislation.gov.au/C2009A00028/latest/downloads   --jurisdiction commonwealth --doc-type act   --title "Fair Work Act 2009" --citation "Fair Work Act 2009 (Cth)"
```

`ingest-file` still checks `--source-url` against the host allowlist, so a local
file cannot be used to launder an off-allowlist source. DOCX and HTML are
converted to text by `docx_text.py` and `html_text.py`; the sha256 is taken over
the original bytes either way, so deriving text never weakens provenance.

**Purging documents wiped the database audit log, and the file log did not
catch it.** `ingestion_log` carries a foreign key to `documents`, so
`TRUNCATE documents CASCADE` truncates it too. The intended safety net — the
append-only file at `sources/ingestion.log.jsonl` — was written only from the
URL-fetch step inside `ingest_url`; `ingest_file` and `seed_from_corpus`
reached the database row alone. A full-corpus purge exercised exactly that
gap: 205 database rows gone, 3 lines left in the file — the record meant to
survive the purge was the empty one. `_write()` now writes the file-log entry
itself, for every path that inserts a `documents` row, so an ingestion is
durable across a purge regardless of which command produced it. Refusals are
unaffected and are logged the same way they always were, from
`sources.fetch()` and from `ingest_file`'s own host check.

**The corpus cannot rebuild itself from the database alone.** `documents`
stores metadata and the sha256 of the original bytes, not the extracted text;
`provisions.char_start`/`char_end` are offsets into that text, which is kept
nowhere once ingestion finishes — not even the file log records it. A heading
a splitter bug dropped was never stored, so it cannot be recovered by
re-running the splitter over what is in the database. Fixing a splitter bug
means re-fetching the sources and re-ingesting, not re-splitting in place.

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

**Model choice is benchmarked, not assumed.** `deepseek/deepseek-v4-pro`,
chosen by 42 runs (7 models x 2 prompts x 3 runs) over the acceptance prompt and
a deliberately uncovered question. Harness in `bench/`. Disqualification was any
single rule breach. Re-measured against the corrected MICHAEL.md; an earlier run
against the old "and stop" wording is void.

| model | mean $/run | verdict |
|---|---|---|
| anthropic/claude-opus-5 | 0.21727 | pass |
| anthropic/claude-sonnet-5 | 0.06036 | pass |
| **deepseek/deepseek-v4-pro** | **0.01171** | **pass — selected** |
| anthropic/claude-haiku-4.5 | 0.01117 | disqualified — asked a question instead of drafting |
| deepseek/deepseek-v4-flash | 0.00403 | disqualified — drafted with no pinpoint citation (2/3) |
| openai/gpt-oss-120b | 0.00185 | disqualified — bare "can't provide that" refusal |
| qwen/qwen3.7-flash | 0.00115 | disqualified — no notice, no [MISSING], no citation |

Cost is actual OpenRouter spend, from diffing the key's cumulative usage around
each run; the `--usage-file` figure is self-declared "estimated". Total spend for
the 42 runs was $1.85.

The prompt fix worked: **no model dropped the closing notice on the uncovered
path**, against four of seven before. opus-5 and haiku-4.5 requalified on that
rule; haiku still fails elsewhere. The cheapest model, `v4-flash`, is 2.9x
cheaper again but drafted twice with no pinpoint citation, which is the one
failure this project cannot tolerate.

**The scorer produced two false failures before it was trusted.** A loose
act-name pattern absorbed a "BASED ON" heading and a leading "and" into the
citation, which then would not resolve. `bench/score_final.py` now anchors the
act name to Title Case and **self-tests on known citations before scoring**.
Every remaining disqualification was confirmed by reading the transcript.

**Pin the provider when overriding the model.** `hermes -m anthropic/claude-sonnet-5`
routes to provider `gmi` and fails with no credentials; `--provider openrouter`
is required, and is also what keeps spend on the measured key.

**One container, not two.** The dashboard's chat talks to the gateway over
localhost, so they must share a network namespace. Upstream's Linux compose
splits them and relies on `network_mode: host`, which Docker Desktop for Windows
does not support; on a bridge network the dashboard reports "Gateway Status:
Stopped" and never opens a websocket, because its localhost is not the
gateway's. `HERMES_DASHBOARD=1` has s6 supervise both in one container.

**Docker Desktop can crash at startup and look like a config problem.** A hard
kill of Docker Desktop leaves orphaned `AF_UNIX` socket reparse points behind in
`AppData\Local\Docker\run` and `AppData\Local\docker-secrets-engine`; Windows
reports "The file cannot be accessed by the system" for each and refuses to
delete them individually, so the engine crashes on the next launch. The
symptom points at the wrong thing: `com.docker.service` sits `Stopped`,
`docker info` and `docker compose ps` fail to reach the engine pipe, and
nothing listens on `127.0.0.1:5433` — none of it is a compose or configuration
fault. Rotate (rename) both directories and Docker recreates them clean;
starting `com.docker.service` again needs elevation. This recurs after any hard
kill of Docker Desktop.

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

**Michael writes in Simplified Technical English.** MICHAEL.md requires
ASD-STE100 for Michael's own prose: short sentences, one idea each, active
voice, present tense, plain words, no filler. The rule is carved out twice so it
cannot erode the guarantees — it does not touch quoted statutory text, which
stays verbatim including long or passive wording, and it removes nothing from
OPEN ITEMS, VERIFY BEFORE USE or the closing notice.

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

## Operating on Railway

Michael is deployed on Railway and is reachable at
`michael-hermes-production.up.railway.app`. Operations run there rather than
against local Docker.

```
railway ssh --service michael-hermes --environment production <command>
```

That reaches the running container over Railway's private network. It does not
open the public Postgres proxy, and nothing here should: the database is
private by design. Michael's operator CLI lives at
`/opt/michael/.venv/bin/michael` inside the container and carries every
subcommand the local CLI does.

The CLI uses the read/write database URL. That is a different surface from the
agent's MCP profile, which holds `classify_request`, `search_provisions` and
`draft_document` and no write tool. Operating by hand does not return write
access to the agent, and must not.

**What runs where.** Code, the test suite and `mypy` stay local, because tests
touch the schema and production data is not a fixture. Corpus inspection,
schema work and production ingests run on Railway.

**The first ingest of any new source runs locally first.** That gate caught
`_is_contents_entry` dropping every section heading that ended in a cited Act's
year; a production-first ingest would have landed 355 provisions with a section
missing and no symptom beyond an occasional wrong `NOT COVERED`.

**Deploy before ingesting on Railway.** The container runs the code in its
image, not the working tree, so a parser change that has not been pushed and
built means the production ingest runs the old parser.

Two shell traps. `railway ssh` arguments are parsed by the local shell first,
so pass a Python payload base64-encoded rather than as a quoted multi-line
string. And Windows PowerShell 5.1 swallows a native command's stderr, so a
remote failure appears as empty output — run `railway ssh` from bash when a
command fails for no visible reason.

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
