STAGE 1 PHASE A — Privacy Act 1988 (Cth) coverage gap: reconnaissance only,
no ingest, no spend. You are WORKER-1.

Read .orca/WORKER.md and .orca/WORKER-1.md before you start, and MICHAEL.md.

## Why
Privacy Act 1988 (Cth), including Part IIIC (notifiable data breaches), is not
in the corpus. The owner has approved ingesting it as a deliberate operator
action. This phase decides whether that ingest can succeed and what it costs.
You do not ingest anything in this phase.

## Hard boundary for this whole stage
This is a LOCAL ingest only, now and in phase B. You do not touch Railway.
Do not use the Railway MCP tools — their view of this account is a decoy
project and is wrong. You need no Railway access for this task.
The owner will run the production ingest himself later, as a separate
operator action, after I accept your local result.

The approval covers running an ingest as an operator. It is NOT a licence to
put ingest_source_url, ingest_local_file or seed_corpus on the answering
agent's MCP profile, and --allow-writes is never passed on that path.

## Deliverables — five, all read-only

1. LOCATE the source.
   Find the Privacy Act 1988 (Cth) DOCX volumes on the Federal Register
   (legislation.gov.au). Report the exact download URLs and the volume
   structure (how many volumes, what each covers).
   Do NOT plan to use the /latest HTML page. That page is headings only.
   Ingesting exactly that kind of page caused a production incident on this
   project — a headings-only document that looked ingested and got cited.

2. PROVE Part IIIC operative text is really there. This is the deliverable
   that matters most.
   Confirm the DOCX volumes carry the operative text of Part IIIC
   SECTION BY SECTION — not merely that "Part IIIC" appears in a table of
   contents. A contents entry that looks like coverage is the documented trap
   on your ground; _is_contents_entry exists because contents rows were being
   stored and outranking real sections.
   Evidence I want: for the sections of Part IIIC, show that subsection
   markers such as (1) and (2) appear in the body text, with a short verbatim
   excerpt per section. Name any section of Part IIIC that is NOT covered by
   the volumes. If the volumes turn out not to carry it, say so and stop —
   that is a valid and useful answer.

3. CHECK the allowlist path end to end.
   Confirm the download URL is accepted by the host allowlist
   (legislation.wa.gov.au, legislation.gov.au, fairwork.gov.au,
   austlii.edu.au) and that it is revalidated on EVERY redirect hop. If the
   Federal Register 302s anywhere, follow the chain and report each hop's
   host. A redirect to an off-allowlist host is a refusal, not a follow.
   Confirm ingest-file still checks --source-url against the allowlist, so a
   hand-downloaded file cannot launder an off-allowlist source.

4. MEASURE capacity.
   Report local free disk space and the local Postgres data size, and the
   expected size of the Privacy Act once ingested (documents rows, estimated
   provisions rows, bytes). State plainly whether it fits with margin.
   Context you do not need to verify: the owner has measured the Railway side
   already — active volume postgres-volume-enpQ is at 439MB of 5000MB. That
   is not your gate; your gate is the local machine.

5. ESTIMATE the spend. THIS IS THE GATE.
   Count the tokens that would be sent to the embedding provider
   (openai/text-embedding-3-small via OpenRouter, EMBEDDING_MAX_CHARS 16000,
   EMBEDDING_DIM 1536) and give me a dollar figure with the arithmetic shown.
   Note where the shrink-and-retry loop is likely to fire — endnote tables
   blow the 8192-token limit even after the char cap.

## Constraints — these bind you
- NO INGEST IN THIS PHASE. No write to the database. No embedding call. Zero
  spend. Downloading a DOCX to inspect it locally is fine; ingesting it is not.
- Do not delete anything. Escalate to me if you think something needs removing.
- MICHAEL.md is read-only to you. Propose wording to me instead.
- Do not touch retrieval scoring or thresholds — that is WORKER-2's ground.
- No git push. No local commit without my approval.
- Secrets stay in .env. Never print a key.

## Observable acceptance
All five deliverables answered with evidence, not assertion. Specifically:
the exact DOCX URLs; per-section proof of Part IIIC operative text with
subsection markers quoted; the redirect chain with each host; local free
space and projected size; and a dollar estimate with its arithmetic.

If any of it fails — the volumes do not carry Part IIIC, the host chain
leaves the allowlist, space is short — report that as the result and stop.
A clean negative is a successful phase A.

Report what you ran and what you saw. Do not claim a result you did not
verify. Then stop and wait; I gate phase B on the owner's approval of your
spend estimate.
