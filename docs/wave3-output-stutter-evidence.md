# Wave 3 output-stutter evidence

Date: 2026-09-22

## Result

The three recorded W-4 symptoms are model-output symptoms, not recoverable
client stream replacements. Commit `018be9a` records the pinned Hermes source
inspection: superseded streams are dropped inside `_fire_stream_delta` before
the hook that becomes a websocket frame, and the stream vocabulary carries no
sequence number, index, or replace flag. The same commit identifies the three
observed symptoms as a repeated refusal, a sentence ending mid-word, and tool
plan narration, including the captured fragment: `The initial search didn't
return section 47. Let me try a more`.

## Local non-blocking reproduction

The current client in `web/michael.js:131-140` accepts every `message.delta`
frame and appends `payload.delta` to one accumulator. A read-only Node harness
fed two ordinary deltas containing a repeated refusal produced:

```text
frames: message.delta, message.delta
payload keys: [delta], [delta]
accumulated: I cannot drop the closing blocks. I cannot drop the closing blocks.
```

Fed the recorded unfinished fragment as one ordinary delta, it produced the
same unfinished text unchanged:

```text
accumulated: The initial search didn't return section 47. Let me try a more
```

There is no client-side marker in either shape that could safely replace or
discard a superseded partial. Appending is therefore correct for the protocol
observed; changing the page to deduplicate text would risk deleting legitimate
repeated wording.

## Verification

```text
node --test web/michael.test.js
13 passed, 0 failed

uv run pytest -q tests/test_tools.py tests/test_output_check.py
33 passed
```

Direct production websocket inspection was attempted through
`POST /api/auth/ws-ticket`; the deployed endpoint returned HTTP 401
`{"error":"unauthenticated","detail":"Unauthorized","reason":"no_cookie"}`.
No production mutation or product-code change was made. A coordinator/owner
with an authenticated dashboard session can optionally capture one raw frame
trace, but the existing source-level evidence is sufficient to classify this
as model behavior and retain the prompt-side mitigation already in `018be9a`.
