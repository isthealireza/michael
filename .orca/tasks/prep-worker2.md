PREPARE THE RECALIBRATION — but DO NOT RUN IT. You are WORKER-2, Retrieval &
Calibration Engineer. Orca restarted; your terminal is fresh and you hold no
memory of earlier work.

Read first, in full:
  D:\Projects\michael\.orca\WORKER.md      (rules binding every worker)
  D:\Projects\michael\.orca\WORKER-2.md    (your role, your files, your traps)
  D:\Projects\michael\MICHAEL.md           (the safety rules)
  D:\Projects\michael\.orca\RAILWAY.md     (the operator path - new)
  README.md, the "Retrieval" and "Known limitations" sections

## Where the project is, so you do not act on a stale picture

`RETRIEVAL_MIN_SCORE = 0.60`, calibrated twice, precision and recall 1.000
both times. It has NOT been re-derived since the corpus changed.

Two things changed the corpus:
1. The Privacy Act 1988 (Cth) was ingested. Both environments went to 205
   documents / 9111 provisions.
2. **A full re-ingest of all 204 legacy documents is RUNNING RIGHT NOW**,
   driven by the owner as an operator action because it deletes data. A
   parser bug (`_is_contents_entry` dropped every heading ending in a cited
   Act's year) meant the old corpus was silently missing provisions; the
   rebuild recovers them. A further fix for endnote/amendment-table junk
   provisions may land and trigger ANOTHER rebuild.

So **the corpus is in flight and its final shape is not known yet.** That is
exactly why this task is preparation only.

## DO NOT RUN THE CALIBRATION

Do not execute `calibration/calibrate.py`. Do not embed anything. Do not spend
money. Do not change `RETRIEVAL_MIN_SCORE` anywhere. The run happens later,
ONCE, against the final rebuilt corpus, on the owner's approval of your spend
estimate. Running it now would measure a corpus that no longer exists — which
is the exact waste this sequencing exists to avoid.

## Deliverables — preparation, analysis and an estimate

1. **Add Privacy Act queries to `calibration/labelled_queries.json`.**
   Real paraphrase-style queries in the style of the existing known-good
   entries — NOT verbatim provision text, so the test is not trivially
   lexical — each paired with the provision it should return. Cover Part IIIC
   (notifiable data breaches) among them, including s 26WD, which is the
   section the parser bug silently dropped.

2. **Re-check EVERY known-absent entry.** The rebuild recovers provisions, so
   a query that was genuinely absent may now be covered. There is precedent:
   two Fair Work queries had to be retired from the absent set once that Act
   was ingested, because they stopped being absent. Go through all 10. For
   each, say whether it is still absent, and retire with a reason any that is
   not. A known-absent set that is quietly wrong flatters the threshold.
   You may READ the corpus to check coverage. You may not write to it.

3. **Report the spend estimate to me** — the embedding cost of one full
   calibration run over the labelled set, with the arithmetic shown. That is
   the gate; the owner approves spend, not you and not me.

4. **State what would void the result.** Which changes to corpus, embedding
   model, splitter or fusion weights force a re-derivation, and confirm
   whether the endnote fix (if it lands) means the calibration must wait for
   a second rebuild. Say plainly what the run must not be started before.

## Traps on your ground — from your own brief, so you do not rediscover them
- One tokeniser and one stemmer end to end; the config that built the
  `tsvector` must run the query. A mismatch degrades silently.
- Both scores squash to [0,1) and fuse 0.5/0.5 — that is what makes the
  threshold an absolute number. Change the fusion and the threshold is void.
- Score UNFILTERED when the run happens. A jurisdiction filter would reject
  most known-absent queries before scoring and flatter the result.
- Pick the LOWEST threshold with zero false positives. A false positive —
  answering a question the corpus cannot answer — is the failure that matters.

## Constraints — these bind you
- **No calibration run, no embedding call, no spend.** Preparation only.
- **Read-only against the corpus.** No writes, no ingest, no re-ingest, no
  schema change. The owner's rebuild is running; stay out of its way.
- Do not change `RETRIEVAL_MIN_SCORE` in `.env.example`, in
  `hermes/config.template.yaml`, or anywhere else. Propagating the number is
  WORKER-4's ground and a separate task after the run.
- Do not touch ingestion or the splitter — WORKER-1's ground.
- MICHAEL.md is read-only to you. Propose wording to me instead.
- Do not touch Railway config. Never open the public Postgres proxy.
- `uv run pytest` and `uv run mypy .` green before you report done.
- No commit without my approval. No push — the owner's alone.
- Leave the repo root clean. Never print a key.

## Observable acceptance
- The diff to `calibration/labelled_queries.json`, with each new query and the
  provision it targets.
- A per-entry verdict on all 10 known-absent queries, with reasons.
- The spend estimate with arithmetic.
- The list of things that would void the result.
- pytest and mypy output pasted.
Then STOP. Do not run the calibration.
