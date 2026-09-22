# BUG-2 — `contract_text()` leaks `DocxError` instead of `ContractTextError`

- **Test ID:** MA-8
- **Severity:** P3 (no live entry point currently calls `contract_text()` for
  untrusted uploads — `tools.py` has no contract-compare/upload command yet — so
  there is no user-facing exposure today; raised as a pre-existing defect for
  whoever wires up the contract-comparison feature next, per the plan at
  `docs/superpowers/plans/2026-09-17-contract-comparison.md`)
- **Confidence:** High (reproduced directly against the shipped code)

## Expected vs actual

`contract_text.contract_text()`'s own docstring says: "Return the contract's
text, **or raise rather than return nothing**." The only exception type it
defines and exports is `ContractTextError`. A caller that follows the module's
contract (`except ContractTextError:`) reasonably expects every failure mode —
including a corrupted/truncated upload — to surface as that type.

**Actual:** a corrupted `.docx` (valid zip magic bytes, invalid/truncated zip
body) raises `michael.docx_text.DocxError` — a different exception class —
which `contract_text()` does not catch or re-wrap. A caller that only catches
`ContractTextError` gets an unhandled exception.

## Minimal reproduction

```python
from michael import contract_text
junk = b"PK\x03\x04" + b"\x00" * 200  # docx-shaped magic, truncated garbage body
contract_text.contract_text(
    junk,
    content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    origin="scratch-truncated.docx",
)
# raises michael.docx_text.DocxError: not a readable zip archive: File is not a zip file
# NOT michael.contract_text.ContractTextError
```

## Affected files

- `src/michael/contract_text.py:41-42` — `text = docx_to_text(body)` call has no
  try/except around it
- `src/michael/docx_text.py:70,73,78` — the three `DocxError` raise sites

## Evidence

- `docs/qa/results_run_78d07dcb1c1b.jsonl`, scenario `MA-8`: `"unhandled DocxError: not a readable zip archive: File is not a zip file"`

## Security / legal impact

Low today (no live caller). Would become a live-availability issue once a
contract-upload/comparison endpoint exists: an untrusted user-supplied file
that is a truncated or corrupted `.docx` would crash that request path with an
uncaught exception rather than a clean, reportable error.

## Likely root cause

`contract_text()` wraps the HTML/plain-text branch in nothing special and
never wraps the docx branch's call to `docx_to_text()` in a try/except that
re-raises as `ContractTextError`.

## Regression risk

None from fixing it (pure exception-type change, no behavioural change on the
success path).

## Proposed fix direction

```python
if looks_like_docx(body, content_type):
    try:
        text = docx_to_text(body)
    except DocxError as exc:
        raise ContractTextError(f"{where}: {exc}") from exc
```

## Proposed regression test

Add to `tests/test_contract_text.py`: feed `contract_text()` docx-magic bytes
with a truncated/invalid body and assert it raises `ContractTextError` (not
`DocxError`).
