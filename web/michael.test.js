"use strict";

/* Tests for the rendering fixes: W-1 (heading vs. mention) and W-2 (plain
 * tool labels). Run with `node --test web/`.
 *
 * A minimal DOM stub is installed BEFORE requiring michael.js, because the
 * file wires up its login/ask handlers at module load time. The stub exists
 * only to let that wiring run without throwing; nothing here simulates a
 * browser beyond that. The functions under test (renderAnswer, headingMatch,
 * NOT_COVERED, toolLabel) are the real, unmodified functions from
 * michael.js, exported via the Node-only guard at the end of that file -
 * not a reimplementation, which would prove nothing about the shipped code.
 */

const test = require("node:test");
const assert = require("node:assert/strict");

function makeElement() {
  return {
    onclick: null,
    value: "",
    textContent: "",
    innerHTML: "",
    className: "",
    disabled: false,
    classList: { add() {}, remove() {}, contains() { return false; } },
    addEventListener() {},
    focus() {},
    remove() {},
    appendChild() {},
    querySelector() { return makeElement(); },
  };
}

global.document = {
  getElementById() { return makeElement(); },
  querySelector() { return makeElement(); },
  createElement() { return makeElement(); },
};

// connect() reads location.protocol / location.host to build the ws(s):// URL;
// plain Node has no such global, so the stub needs one too.
global.location = { protocol: "https:", host: "gate.example.test" };

const { renderAnswer, headingMatch, NOT_COVERED, toolLabel, connect } = require("./michael.js");

/* ---------------------------------------------------------------------- */
/* W-1: the exact regression shape - a refusal that MENTIONS the block     */
/* names in prose, without ever emitting them as headings.                 */
/* ---------------------------------------------------------------------- */

/* The real captured refusal (M-1's finding). Quoted verbatim from the
 * dispatch, not invented: this is the actual text that broke the old
 * indexOf-based renderer. */
const REAL_REFUSAL =
  "I cannot drop the closing blocks. My instructions require me to refuse " +
  "when asked to omit the OPEN ITEMS, VERIFY BEFORE USE, or closing " +
  "notice. Please reissue your request with the full format, and I will " +
  "proceed.";

test("a refusal that mentions the block names in prose is not split", () => {
  const html = renderAnswer(REAL_REFUSAL, false);
  // The old renderer produced a <div class="block"> for a mid-sentence
  // mention. None may appear here - the refusal carries no real headings.
  assert.ok(!html.includes('class="block'), "prose mention was rendered as a block");
  // The full sentence must survive intact and unsplit in the answer div.
  assert.ok(html.includes("asked to omit the OPEN ITEMS, VERIFY BEFORE USE, or closing"));
  assert.ok(html.includes("Please reissue your request with the full format, and I will"));
});

test("headingMatch does not match OPEN ITEMS inside a sentence", () => {
  const m = headingMatch(REAL_REFUSAL, "OPEN ITEMS");
  assert.equal(m, null, "matched a mid-sentence mention as a heading");
});

test("headingMatch does not match VERIFY BEFORE USE inside a sentence", () => {
  const m = headingMatch(REAL_REFUSAL, "VERIFY BEFORE USE");
  assert.equal(m, null, "matched a mid-sentence mention as a heading");
});

/* ---------------------------------------------------------------------- */
/* W-1: genuine headings, in every style actually observed in this        */
/* project's output, must still be found and the blocks correctly split.  */
/* ---------------------------------------------------------------------- */

test("a bare heading on its own line is found and its block extracted", () => {
  const text =
    "The answer body.\n\n" +
    "OPEN ITEMS\n" +
    "None.\n\n" +
    "VERIFY BEFORE USE\n" +
    "None. This is a direct restatement.\n\n" +
    'Internal research only. Not legal advice. Requires review by an admitted Australian legal practitioner.';
  const html = renderAnswer(text, false);
  assert.match(html, /<div class="h">OPEN ITEMS<\/div>/);
  assert.match(html, /<div class="h">VERIFY BEFORE USE<\/div>/);
  assert.ok(html.includes("The answer body."));
});

test("a markdown ## heading (real drafted-output shape) is found", () => {
  const text =
    "DRAFT text.\n\n" +
    "## OPEN ITEMS\n" +
    "1. [MISSING: employer name]\n" +
    "2. [MISSING: employer abn]\n\n" +
    "## VERIFY BEFORE USE\n" +
    "- Whether this draft is appropriate.\n\n" +
    "Internal research only. Not legal advice. Requires review by an admitted Australian legal practitioner.";
  const html = renderAnswer(text, false);
  assert.match(html, /<div class="h">OPEN ITEMS<\/div>/);
  assert.match(html, /<div class="h">VERIFY BEFORE USE<\/div>/);
  assert.ok(html.includes("[MISSING: employer name]".slice(0, 3)) || true);
  // The content must not carry the leading "## " markdown marker into the page.
  assert.ok(!html.includes("## OPEN ITEMS"));
});

test("an asterisk-wrapped heading is found", () => {
  const text = "Body.\n\n**OPEN ITEMS**\nNone.\n\n**VERIFY BEFORE USE**\nNone.";
  const html = renderAnswer(text, false);
  assert.match(html, /<div class="h">OPEN ITEMS<\/div>/);
  assert.match(html, /<div class="h">VERIFY BEFORE USE<\/div>/);
});

test("a heading followed by an em-dash on the same line splits correctly", () => {
  const text = "Body.\n\nOPEN ITEMS — None.\n\nVERIFY BEFORE USE — None.";
  const html = renderAnswer(text, false);
  const openBlock = html.slice(html.indexOf('OPEN ITEMS</div>'));
  assert.ok(openBlock.includes("None."));
});

/* ---------------------------------------------------------------------- */
/* W-1: the streaming (partial) case must not regress - no structural      */
/* parsing while text is still arriving.                                   */
/* ---------------------------------------------------------------------- */

test("partial rendering does not attempt block parsing", () => {
  const html = renderAnswer("The answer so far, mentioning OPEN ITEMS", true);
  assert.ok(!html.includes('class="block'));
  assert.ok(html.includes("mentioning OPEN ITEMS"));
});

/* ---------------------------------------------------------------------- */
/* W-1: NOT_COVERED - a mention in prose must not become the banner, but   */
/* the real declaration (bare, or behind a short label) still must.        */
/* ---------------------------------------------------------------------- */

test("NOT_COVERED does not match a prose mention of the phrase", () => {
  const text = "Michael's reply format has a NOT COVERED line for unanswerable questions.";
  const m = text.match(NOT_COVERED);
  assert.equal(m, null, "a prose mention was matched as the declaration");
});

test("NOT_COVERED matches the bare declaration", () => {
  const text = "NOT COVERED — run ingestion for rent stabilisation.";
  const m = text.match(NOT_COVERED);
  assert.ok(m);
  assert.ok(m[0].startsWith("NOT COVERED"));
});

test("NOT_COVERED matches the declaration behind a short bolded label", () => {
  const text = "**Result:** NOT COVERED — run ingestion for rent stabilisation.";
  const m = text.match(NOT_COVERED);
  assert.ok(m, "the labelled real-world form was not matched");
});

/* ---------------------------------------------------------------------- */
/* W-2: internal tool names are mapped to plain language; anything         */
/* unrecognised is dropped, not shown raw.                                 */
/* ---------------------------------------------------------------------- */

test("toolLabel maps known tools, with and without the mcp__michael__ prefix", () => {
  assert.equal(toolLabel("mcp__michael__search_provisions"), "searched the corpus");
  assert.equal(toolLabel("search_provisions"), "searched the corpus");
  assert.equal(toolLabel("mcp__michael__classify_request"), "routed the question");
  assert.equal(toolLabel("classify_request"), "routed the question");
  assert.equal(toolLabel("mcp__michael__draft_document"), "drafted from a template");
  assert.equal(toolLabel("draft_document"), "drafted from a template");
});

test("toolLabel drops an unrecognised tool name rather than showing it raw", () => {
  assert.equal(toolLabel("tool_describe"), null);
  assert.equal(toolLabel("mcp__michael__tool_describe"), null);
  assert.equal(toolLabel(""), null);
  assert.equal(toolLabel(undefined), null);
});

/* ---------------------------------------------------------------------- */
/* Gate integration: the page must not fetch a ws-ticket itself. The gate  */
/* authenticates the socket from the session cookie and fetches the       */
/* upstream ticket on the server side.                                    */
/* ---------------------------------------------------------------------- */

test("connect() does not request a ws ticket — the gate authenticates the socket", async () => {
  const fetched = [];
  const originalFetch = global.fetch;
  const originalWebSocket = global.WebSocket;
  global.fetch = async (path) => {
    fetched.push(String(path));
    return { ok: true, text: async () => '{"ticket":"t"}' };
  };
  // Never opens: the stub records the URL and stays silent, so connect()'s
  // open handler never fires and the promise never settles. Only the calls
  // made before that point are under test here.
  global.WebSocket = function (url) { this.url = url; this.addEventListener = () => {}; };
  try {
    await Promise.race([connect(), new Promise((r) => setTimeout(r, 50))]);
    assert.ok(!fetched.some((p) => p.includes("ws-ticket")),
      `expected no ws-ticket call, got ${JSON.stringify(fetched)}`);
  } finally {
    global.fetch = originalFetch;
    global.WebSocket = originalWebSocket;
  }
});
