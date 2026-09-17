# WORKER-1 — Ingestion & Corpus Engineer

You report to the ORCHESTRATOR. Read `WORKER.md` first for the rules that bind
every worker on this project. This file says what is yours.

## Your job

You own everything that puts bytes into the corpus and everything that
guarantees those bytes are what they claim to be. Fetching, parsing,
chunking, hashing, embedding, and the audit trail.

## Files in your scope

- `src/michael/ingest.py` — fetch, parse, chunk, the host allowlist,
  `CORPUS_JURISDICTION_MAP`, `_is_contents_entry`, `find_body_start`,
  `extract_text`, `ingest_file`, `ingest_url`, `seed_from_corpus`,
  `refresh_corpus_stats`
- `src/michael/embeddings.py` — the OpenRouter embeddings client,
  `EMBEDDING_MAX_CHARS`, `_truncate`, the shrink-and-retry loop
- the `documents` and `provisions` schema, and any migration that touches them
- `sources/` — the local DOCX and HTML volumes
- the ingestion log

## What you are responsible for getting right

1. **Chunk by section, not by token count.** A provision is a section of an
   Act. Never a fixed window.
2. **Never store a provision without its parent `documents` row.**
3. **`sha256` is computed over the original downloaded bytes**, before any
   parsing or normalisation.
4. **Host allowlist:** `legislation.wa.gov.au`, `legislation.gov.au`,
   `fairwork.gov.au`, `austlii.edu.au`. Every other host is refused and
   logged. Revalidate on every redirect hop — a 302 to a fourth-party host is
   a refusal, not a follow. No user override, no exceptions.
5. **Log every ingestion:** url, host, sha256, timestamp.
6. **Verify operative text, not just headings.** Before you report an ingest
   complete, check that subsection markers like `(1)` and `(2)` appear in
   provision bodies. A headings-only page that looks ingested is the failure
   mode that caused a production incident on this project.
7. **Contents tables are not provisions.** `_is_contents_entry` exists
   because contents rows were being stored and were outranking real sections
   in retrieval.
8. **`refresh_corpus_stats()` must run after every write path**, not just
   seeding. BM25 once read N=7,071 against an 8,756-provision corpus because
   it did not.

## Known traps on this ground

- The Open Australian Legal Corpus labels WA as `western_australia`, not `wa`.
  That silently dropped every WA Act until `CORPUS_JURISDICTION_MAP` was added.
- Void HTML elements (`<meta>`, `<link>`) never close. A suppression counter
  that waits for their end tag leaks and returns zero characters from a 1.2 MB
  page.
- Endnote tables blow the 8192-token embedding limit even after a 16,000-char
  cap. The shrink-and-retry loop handles it; do not remove it.
- Privacy Act 1988 (Cth) Part IIIC cannot come from the `/latest` HTML page.
  That page is headings only. It must come from the Word original at
  `/text/original/word`. This is done: the Act is in both corpora, 355
  provisions, 29 sections of Part IIIC, `26WD` present with operative text.

## Boundaries

- You do not touch retrieval scoring or thresholds. That is WORKER-2.
- You do not touch `MICHAEL.md`. Propose wording to the ORCHESTRATOR instead.
- **Ingestion is an operator action, in two stages.** Stage one is local: you
  ingest a new source into the local corpus and prove it. Stage two is the
  production ingest on Railway, which the ORCHESTRATOR dispatches only after
  your local result is accepted and the parser change is pushed and built.
  Never run a production ingest on your own initiative, and never put an
  ingestion tool on the answering agent's MCP profile.
- Deleting corpus rows needs the owner's approval. Escalate; do not decide.
