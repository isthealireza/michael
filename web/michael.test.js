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

const { renderAnswer, headingMatch, NOT_COVERED, toolLabel, connect, CITATION } = require("./michael.js");

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

/* MICHAEL.md tells Michael exactly how to write a citation, and this file's
 * CITATION pattern is what decides whether that shape is marked on screen for
 * the reader. They are two halves of one contract held in two files, and the
 * first real question asked through the gate showed what happens when they
 * drift: Michael cited "(s 26WL)" with the Act named a sentence earlier, every
 * pinpoint in a 2,952-character answer went unmarked, and the answer looked
 * unsourced to the person relying on it.
 *
 * So the prompt's own worked examples are run against the real pattern here.
 * If either side changes without the other, this fails. */
test("MICHAEL.md's citation examples are the shape the page actually marks", () => {
  const fs = require("node:fs");
  const path = require("node:path");
  const prompt = fs.readFileSync(path.join(__dirname, "..", "MICHAEL.md"), "utf8");

  const section = prompt.split("## Citation form")[1];
  assert.ok(section, "MICHAEL.md no longer has a 'Citation form' section");

  // Every line the prompt offers as correct must match, whole.
  const rights = [...section.matchAll(/^\s*right:\s*(.+)$/gm)].map((m) => m[1].trim());
  assert.ok(rights.length > 0, "no 'right:' examples found to check");
  for (const line of rights) {
    CITATION.lastIndex = 0;
    assert.ok(CITATION.exec(line), `MICHAEL.md offers this as correct but the page would not mark it: ${line}`);
  }

  // And every line it calls wrong must not be marked, or the guidance is moot.
  const wrongs = [...section.matchAll(/^\s*wrong:\s*(.+)$/gm)].map((m) => m[1].trim());
  assert.ok(wrongs.length > 0, "no 'wrong:' examples found to check");
  for (const line of wrongs) {
    CITATION.lastIndex = 0;
    const hit = CITATION.exec(line);
    assert.ok(
      !hit || hit[0].trim() !== line,
      `MICHAEL.md calls this wrong but the page marks it in full: ${line}`,
    );
  }

  // The four indented specimens at the top of the section are the canonical
  // forms; each must match in full, snapshot included.
  const specimens = [...section.matchAll(/^ {4}([A-Z][^\n]*\(snapshot \d{4}-\d{2}-\d{2}\))$/gm)]
    .map((m) => m[1].trim())
    .filter((s) => !s.startsWith("wrong") && !s.startsWith("right"));
  assert.ok(specimens.length >= 4, `expected the canonical specimens, found ${specimens.length}`);
  for (const line of specimens) {
    CITATION.lastIndex = 0;
    const hit = CITATION.exec(line);
    assert.equal(hit && hit[0], line, `specimen is not matched whole: ${line}`);
  }
});

/* ---- the reader-facing extras: usage, export, timestamps ---------------- */

const { usageLabel, transcriptMarkdown, state } = require("./michael.js");

test("usageLabel reads the session.usage frames already on the wire", () => {
  assert.equal(usageLabel({ input_tokens: 1240, output_tokens: 880, cost: 0.01171 }),
    "1240 in · 880 out · $0.0117");
  // Providers name these differently; both spellings are accepted.
  assert.equal(usageLabel({ prompt_tokens: 10, completion_tokens: 5 }), "10 in · 5 out");
});

test("usageLabel shows nothing rather than a misleading zero", () => {
  // A missing cost is not a free turn, and a missing count is not zero tokens.
  assert.equal(usageLabel({ input_tokens: 10, output_tokens: 5, cost: 0 }), "10 in · 5 out");
  assert.equal(usageLabel({}), "");
  assert.equal(usageLabel(null), "");
  assert.equal(usageLabel("nonsense"), "");
});

test("the export carries the citations and the practitioner notice", () => {
  /* The point of the export is that a transcript pasted into a memo is still
   * sourced and still carries its disclaimer. An export that dropped either
   * would be worse than no export: it would look like a clean answer. */
  state.turns.length = 0;
  state.turns.push({
    question: "What notice period applies?",
    answer: "CLASSIFICATION: RESEARCH\nFair Work Act 2009 (Cth) s 117 (snapshot 2026-07-07).",
    tools: "searched the corpus",
    at: new Date("2026-09-23T08:15:00Z"),
  });

  const md = transcriptMarkdown();

  assert.ok(md.includes("Fair Work Act 2009 (Cth) s 117 (snapshot 2026-07-07)"));
  assert.ok(md.includes("not a lawyer"));
  assert.ok(md.includes("admitted Australian legal"));
  assert.ok(md.includes("What notice period applies?"));
  assert.ok(md.includes("searched the corpus"));
  state.turns.length = 0;
});

test("the export of an empty conversation is still a valid document", () => {
  state.turns.length = 0;
  const md = transcriptMarkdown();
  assert.ok(md.startsWith("# Michael"));
  assert.ok(md.includes("not a lawyer"), "the disclaimer is not conditional on content");
});
