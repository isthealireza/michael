/* MICHAEL — the surface a non-technical reader is given.
 *
 * A CLIENT of the existing answering path, never a second one. It calls
 * prompt.submit on /api/ws, which by its own comment is the single
 * server-side choke point every dashboard submit passes through. Hermes runs
 * the model with MICHAEL.md, calls the three MCP tools, and emits the three
 * closing blocks exactly as before. This file renders; it never answers, so it
 * cannot weaken a guarantee: if Michael emits no citation, none is shown.
 *
 * Every conversation gets its OWN titled session, so it is findable in the
 * Hermes dashboard's session list. Reusing sessions[0] - the first version of
 * this page - mixed a reader's questions into whatever session happened to be
 * most recent, which during testing was the scenario tester's.
 */
"use strict";

const $ = (id) => document.getElementById(id);
const GATEWAY_PROTOCOL = "hermes-gateway-v1";

/* Only message.delta is the answer. reasoning.delta and thinking.delta are the
 * model's private working; rendering them shows the reasoning trace as though
 * it were the advice. */
const ANSWER_FRAME = "message.delta";
const TERMINAL_FRAMES = new Set(["message.complete", "turn.end", "turn.complete"]);

const state = {
  live: null,
  stored: null,
  busy: false,
  /* The socket of the turn in flight, so Stop can close it. One socket per
   * turn (see connect), so there is never more than one to hold. */
  socket: null,
  /* Question/answer pairs, kept for the Markdown export. The gate stores who
   * owns which session, never what was asked in it, so this is the only place
   * the transcript exists — and it lives for the life of the page only. */
  turns: [],
};

/* Preferences that are the reader's, not the system's. localStorage is
 * per-browser and can throw outright in a private window, so every access is
 * guarded and the page works the same when it fails. */
const PREFS = { sendKey: "enter" };

function loadPrefs() {
  try {
    const raw = localStorage.getItem("michael.prefs");
    if (raw) Object.assign(PREFS, JSON.parse(raw));
  } catch { /* unavailable or unparseable: the defaults above stand */ }
}

function savePrefs() {
  try {
    localStorage.setItem("michael.prefs", JSON.stringify(PREFS));
  } catch { /* nothing to do; the preference simply will not persist */ }
}

/* ------------------------------- transport ------------------------------- */

function sessionTitle() {
  const when = new Date().toLocaleString("en-AU",
    { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
  return `MICHAEL web — ${when}`;
}

/* One socket per turn. Michael's turns are long but discrete, and a socket
 * held open across idle minutes is a reconnect problem for no benefit. */
async function connect() {
  /* No ws-ticket call: michael-gate authenticates this socket from the session
   * cookie and obtains the upstream ticket itself, so a client never handles a
   * credential-backed handle on the gateway. */
  const scheme = location.protocol === "https:" ? "wss:" : "ws:";
  const ws = new WebSocket(`${scheme}//${location.host}/api/ws`, [GATEWAY_PROTOCOL]);
  const pending = new Map();
  ws.addEventListener("message", (event) => {
    let frame;
    try { frame = JSON.parse(event.data); } catch { return; }
    if (frame.id && pending.has(frame.id)) {
      const settle = pending.get(frame.id);
      pending.delete(frame.id);
      settle(frame);
    }
  });
  ws.call = (method, params, id) =>
    new Promise((resolve, reject) => {
      pending.set(id, resolve);
      setTimeout(() => {
        if (pending.delete(id)) reject(new Error(`${method} timed out`));
      }, 90000);
      ws.send(JSON.stringify({ id, method, params }));
    });

  await new Promise((resolve, reject) => {
    ws.addEventListener("open", resolve, { once: true });
    ws.addEventListener("error", () => reject(new Error("could not connect")), { once: true });
    setTimeout(() => reject(new Error("connection timed out")), 20000);
  });
  return ws;
}

function unwrap(frame, what) {
  if (frame.error) throw new Error(`${what}: ${frame.error.message || "failed"}`);
  return frame.result || {};
}

/* --------------------------------- asking -------------------------------- */

async function ask(question, onDelta, onTool, onPhase, onUsage) {
  const ws = await connect();
  state.socket = ws;
  try {
    if (!state.live) {
      onPhase("opening a session");
      const created = unwrap(
        await ws.call("session.create", { title: sessionTitle() }, "create"),
        "session.create",
      );
      state.live = created.session_id;
      state.stored = created.stored_session_id || null;
      showSession();
    } else {
      /* A live session lives in the dashboard process, not the browser. If it
       * was reclaimed between turns, resume the stored record rather than
       * silently starting a new conversation the reader cannot see. */
      const status = await ws.call("session.status", { session_id: state.live }, "status")
        .catch(() => null);
      const alive = status && !status.error;
      if (!alive && state.stored) {
        onPhase("reopening the session");
        const again = unwrap(
          await ws.call("session.resume", { session_id: state.stored }, "resume"),
          "session.resume",
        );
        state.live = again.session_id;
      }
    }

    let answer = "";
    const finished = new Promise((resolve, reject) => {
      ws.addEventListener("message", (event) => {
        let frame;
        try { frame = JSON.parse(event.data); } catch { return; }
        if (frame.id === "submit" && frame.error) {
          reject(new Error(frame.error.message || "Michael could not answer"));
          return;
        }
        const params = frame.params || {};
        const kind = params.type || frame.method;
        const payload =
          params.payload && typeof params.payload === "object" ? params.payload : params;

        if (kind === ANSWER_FRAME) {
          const piece = payload.delta ?? payload.text ?? payload.content ?? "";
          if (typeof piece === "string" && piece) {
            answer += piece;
            onDelta(answer);
          }
        } else if (kind === "tool.start") {
          onTool(payload.name || payload.tool || "tool");
        } else if (kind === "session.usage") {
          /* Already on the wire — three or four of these arrive per turn.
           * Rendering them costs no new upstream method, and what a turn
           * cost is worth knowing on a tool billed per run. */
          if (onUsage) onUsage(payload);
        } else if (TERMINAL_FRAMES.has(kind)) {
          resolve();
        }
      });
      ws.addEventListener("close", () => {
        if (answer) resolve();
        else reject(new Error("the connection closed before Michael answered"));
      });
      setTimeout(() => reject(new Error("Michael did not finish in time")), 300000);
    });

    onPhase("Michael is working");
    ws.send(JSON.stringify({
      id: "submit", method: "prompt.submit",
      params: { session_id: state.live, text: question },
    }));

    await finished;
    if (!answer.trim()) throw new Error("Michael returned nothing — that is a failed run");
    return answer;
  } finally {
    state.socket = null;
    try { ws.close(); } catch { /* already closed */ }
  }
}

/* Stop. Closing the socket ends the turn from this page's side: `ask` resolves
 * if any answer text had already arrived, and rejects otherwise.
 *
 * Be clear about what this does NOT do. The upstream turn keeps running and
 * keeps costing — cancelling it server-side needs a method the gate's
 * allowlist does not carry, and widening that allowlist is a security
 * decision, not a convenience. So this stops the waiting, not the work. */
function cancelTurn() {
  const ws = state.socket;
  if (!ws) return;
  state.socket = null;
  try { ws.close(); } catch { /* already closed */ }
}

/* ------------------------------- rendering ------------------------------- */

/* Unicode bidi control characters (LRM/RLM, LRE/RLE/PDF, LRO/RLO, LRI/RLI/
 * FSI/PDI) are invisible and carry no HTML markup, so the `[&<>"]` escape
 * below does not touch them: they reach the DOM as ordinary text and the
 * browser still obeys them there, reordering the visible glyphs of
 * whatever text follows. A `U+202E RIGHT-TO-LEFT OVERRIDE` inside a quoted
 * provision or a [MISSING] item can therefore make a citation, an amount or
 * a file-adjacent string read backwards on screen while the underlying text
 * (what gets copied, exported or matched by CITATION/MISSING) is untouched -
 * the classic RLO spoofing trick. Michael's own prose is plain STE English
 * and never needs one, so they are stripped outright rather than escaped. */
const esc = (s) => String(s)
  .replace(/[‎‏‪-‮⁦-⁩]/g, "")
  .replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

/* A pinpoint as Michael emits it: "Privacy Act 1988 (Cth) s 26WL (snapshot
 * 2026-06-04)". "Sch 1 cl 11" is matched too: a Schedule clause is a different
 * provision from a section of the same number, and the corpus contains both. */
/* An Act name is title case, so every word before "Act 2009" is capitalised
 * apart from the connectors a real name carries: Occupational Safety and
 * Health Act 1984, Minimum Conditions of Employment Act 1993.
 *
 * The old pattern allowed any letters and spaces, lazily, so it ran backwards
 * from "Act 2009" across a full stop and highlighted a whole sentence as a
 * citation: "Either party may terminate the agreement with notice required by
 * the Fair Work Act 2009 (Cth)" rendered as one 826px citation chip. Measured
 * over the 84 real outputs in bench/: 332 citations matched, 26 of them with
 * an act name longer than six words. The highlight exists to mark a pinpoint
 * citation, and marking prose as one destroys the signal it carries.
 *
 * A capitalised word opening a sentence looks exactly like the first word of a
 * title, so the leading word is excluded by a stop list. That is a heuristic,
 * not a rule. After it the same 84 outputs yield 332 citations - none lost -
 * over three distinct act names, every one of them correct. */
const CITATION = new RegExp(String.raw`\b((?!(?:The|A|An|In|On|Of|And|To|For|Under|This|That|Its|Their|Whether|When|Where|While|If|Because|Although|See|Note|Both|Each|Every|However|Therefore|Here|There|It|As|At|By|But|So|Such|These|Those|Section|Schedule|Part|Division)\b)[A-Z][A-Za-z'’-]*(?:[ ](?:[A-Z][A-Za-z'’-]*|of|and|the|for|to|in|on)){0,7}[ ](?:Act|Regulations|Code|Rules|Award)\s+\d{4}(?:\s*\((?:Cth|WA|NSW|Vic|Qld|SA|Tas|NT|ACT|Imp)\))?)\s+(ss?\s+[\w.()]+|Sch\s+\w+\s+cl\s+[\w.]+)(\s*\(snapshot\s+[\d-]+\))?`, "g");
const MISSING = /\[MISSING:\s*([^\]]+)\]/g;
/* The closing notice, with any markdown emphasis wrapping it. Michael writes it
 * plain in some outputs and as *…* or **…** in others. Matching only the
 * sentence left the orphaned asterisks behind in the preceding block, so a
 * VERIFY BEFORE USE block ended with a stray "**" on screen. */
const NOTICE = /[*_]{0,2}\s*(Internal research only\.[\s\S]{0,220}?practitioner\.)\s*[*_]{0,2}/i;
/* The classification line. Held to the same standard as a block heading,
 * and for the same reason: "Internal research only" in the closing notice
 * and "I'll draft the contract" in prose each carry a classification word,
 * so a substring test reports a declaration that was never made. Measured
 * on 42 real outputs, a substring test passes 41 and this passes 23.
 * Mirrors src/michael/output_check.py - one definition, two runtimes. */
const CLASSIFICATION = /^[ 	]*#{0,6}[ 	]*\*{0,2}CLASSIFICATION\*{0,2}[ 	]*:.*$/im;

const BLOCK_KEYS = [
  { key: "OPEN ITEMS", cls: "" },
  { key: "VERIFY BEFORE USE", cls: "verify" },
];

/* A block heading is its own line, never a mention inside prose. A substring
 * test cannot tell the two apart: Michael's refusal to drop the closing
 * blocks says, in ordinary prose, "...asked to omit the OPEN ITEMS, VERIFY
 * BEFORE USE, or closing notice" - the old body.indexOf(b.key) matched that
 * mid-sentence occurrence and cut the sentence in half to manufacture a
 * block that was never emitted. Anchoring to line start is what excludes it:
 * "OPEN ITEMS" here is preceded by other words on the same line, not by a
 * newline, so `^` never aligns with it.
 *
 * Optional wrapping is allowed because real output uses more than one style:
 * a bare "OPEN ITEMS" line (most research answers), a markdown "## OPEN
 * ITEMS" heading (drafted output - contract_text.py strips the identical
 * "#{1,6}" marker on the ingestion side, so this is an established
 * convention in this project, not a new one), or "**OPEN ITEMS**". What must
 * follow the key is end of line or a heading separator (—, :, -) - never a
 * continuing word or comma, which is what a sentence looks like instead of a
 * heading. */
function headingMatch(body, key) {
  const escaped = key.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const re = new RegExp(
    String.raw`^[ \t]*#{0,6}[ \t]*\*{0,2}${escaped}\*{0,2}(?=[ \t]*[—:-]|[ \t]*$)`,
    "m",
  );
  return re.exec(body);
}

/* The NOT COVERED declaration is held to the same rule, with one allowance:
 * a short bolded label observed in real output before it - "**Result:** NOT
 * COVERED - run ingestion for ...". Anything longer than a short label
 * before the phrase is prose discussing the rule, not the declaration
 * itself, and must not be promoted to the banner. */
const NOT_COVERED = new RegExp(
  String.raw`^[ \t]*(?:\*{0,2}[A-Za-z][\w '-]{0,24}:\*{0,2}[ \t]*)?\*{0,2}NOT COVERED\b.*$`,
  "m",
);

/* Plain language for the manager, never the internal tool name. A default
 * that renders whatever is unrecognised is how "tool_describe" reached the
 * reader in the first place - so anything not in this map is DROPPED, not
 * shown raw. The "mcp__michael__" prefix is stripped when present, but not
 * assumed: a bare name reaches this unchanged too. */
const TOOL_LABELS = {
  classify_request: "routed the question",
  search_provisions: "searched the corpus",
  draft_document: "drafted from a template",
};

function toolLabel(name) {
  const bare = String(name || "").replace(/^mcp__michael__/, "");
  return TOOL_LABELS[bare] || null;
}

/* Markdown, deliberately without links.
 *
 * `[text](url)` is the one construct here that would turn model output into an
 * attribute — and `javascript:` in an href is the whole XSS problem. Michael
 * has no reason to emit one either: MICHAEL.md forbids citing a web page, so a
 * link in an answer is already a rule violation rather than something to
 * render prettily. Images, raw HTML and reference links are out for the same
 * reason. Everything below emits fixed tags with no attributes drawn from the
 * text, over a string that has ALREADY been escaped by `inline`.
 *
 * Bullets are `-` or `•` only, never `*`, so a bullet can never be confused
 * with the opening of `**bold**`. */
function mdSpans(html) {
  return html
    .replace(/`([^`\n]+)`/g, (_m, code) => `<code>${code}</code>`)
    .replace(/\*\*([^*\n]+)\*\*/g, (_m, strong) => `<strong>${strong}</strong>`);
}

function mdTable(rows) {
  const cells = (line) =>
    line.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
  const head = cells(rows[0]);
  const body = rows.slice(2).map(cells);
  const th = head.map((c) => `<th>${mdSpans(c)}</th>`).join("");
  const tr = body
    .map((r) => `<tr>${r.map((c) => `<td>${mdSpans(c)}</td>`).join("")}</tr>`)
    .join("");
  return `<table><thead><tr>${th}</tr></thead><tbody>${tr}</tbody></table>`;
}

function renderMarkdown(html) {
  const lines = html.split("\n");
  const out = [];
  let i = 0;

  const isTableRow = (s) => /^\s*\|.*\|\s*$/.test(s);
  const isTableRule = (s) => /^\s*\|[\s:|-]+\|\s*$/.test(s);
  const bullet = /^\s*[-•]\s+(.*)$/;
  const numbered = /^\s*\d+[.)]\s+(.*)$/;

  while (i < lines.length) {
    const line = lines[i];

    /* Fenced code: verbatim, and NOT passed through mdSpans. A backtick or an
     * asterisk inside a code block is code, not emphasis. */
    const fence = line.match(/^\s*```(\w*)\s*$/);
    if (fence) {
      const body = [];
      i += 1;
      while (i < lines.length && !/^\s*```\s*$/.test(lines[i])) body.push(lines[i++]);
      i += 1; // the closing fence
      out.push(`<pre><code>${body.join("\n")}</code></pre>`);
      continue;
    }

    if (isTableRow(line) && isTableRule(lines[i + 1] || "")) {
      const rows = [];
      while (i < lines.length && isTableRow(lines[i])) rows.push(lines[i++]);
      out.push(mdTable(rows));
      continue;
    }

    /* ATX headings. Michael sections long answers with ## and ###, and left
     * unhandled the hashes print literally. The card's own heading is an <h2>,
     * so an answer's headings start at <h3> — including a lone "#", which is
     * still subordinate to the card it sits inside. */
    const heading = line.match(/^\s*(#{1,6})[ \t]+(.+?)[ \t]*#*\s*$/);
    if (heading) {
      const level = Math.min(5, heading[1].length <= 2 ? 3 : heading[1].length + 1);
      out.push(`<h${level}>${mdSpans(heading[2])}</h${level}>`);
      i += 1;
      continue;
    }

    if (bullet.test(line) || numbered.test(line)) {
      const ordered = !bullet.test(line);
      const pattern = ordered ? numbered : bullet;
      const items = [];
      while (i < lines.length && pattern.test(lines[i])) {
        items.push(`<li>${mdSpans(lines[i].match(pattern)[1])}</li>`);
        i += 1;
      }
      const tag = ordered ? "ol" : "ul";
      out.push(`<${tag}>${items.join("")}</${tag}>`);
      continue;
    }

    out.push(mdSpans(line));
    i += 1;
  }
  /* The answer is rendered under `white-space: pre-wrap`, so every newline in
   * this markup is a visible line break — and a block element breaks the line
   * by itself. Left alone, each list, table and code block would carry a blank
   * line above and below it that the author never wrote. Blank lines between
   * ordinary paragraphs are the author's and stay. */
  return out.join("\n")
    .replace(/\n+(?=<(?:ul|ol|pre|table|h[3-5])>)/g, "")
    .replace(/(<\/(?:ul|ol|pre|table|h[3-5])>)\n+/g, "$1");
}

function inline(text) {
  /* Escape ONCE, here, then match on the escaped text. The captured groups are
   * already escaped, so the replacements must not escape them again: doing so
   * turned "[MISSING: Smith & Co Pty Ltd]" into a flag reading
   * "Smith &amp; Co Pty Ltd" on screen, and "rate < $30 per hour" into
   * "rate &lt; $30". Both are shapes a real draft produces - a company name and
   * a pay rate - so the corruption would reach a reader.
   *
   * Escaping first is what keeps this safe: every regex below runs over text
   * in which < > & " are already entities, so no markup can be reconstructed
   * out of a capture. */
  let html = esc(text);
  html = html.replace(MISSING, (_m, item) =>
    `<span class="missing">MISSING: ${item.trim()}</span>`);
  html = html.replace(CITATION, (_m, act, pin, snap) =>
    `<span class="cite">${act} ${pin}${snap || ""}</span>`);
  /* Markdown LAST, after the citation and [MISSING] spans exist. The order
   * matters: a citation carries no backtick, asterisk or pipe, so nothing
   * below can match across one and split it, whereas running markdown first
   * could put a <strong> inside a citation and stop CITATION matching it. */
  return renderMarkdown(html);
}

/* Render one answer. `partial` suppresses the structural parsing while text is
 * still streaming: splitting on "OPEN ITEMS" before the block has arrived
 * flickers sections in and out, and a half-parsed answer reads as a broken one. */
function renderAnswer(raw, partial) {
  const text = (raw || "").trim();
  if (partial) return `<div class="answer">${inline(text)}</div>`;

  let html = "";
  const nc = text.match(NOT_COVERED);
  if (nc) {
    html += `<div class="banner"><div class="h">NOT COVERED</div>
      <div class="b">${inline(nc[0].trim())}</div></div>`;
  }

  /* The classification is the first thing Michael must write, so it is the
   * first thing shown - lifted out of the body and labelled. When it is
   * absent the page says so. It is never supplied: writing one here would
   * assert a routing decision the page did not make and cannot check. */
  const cls = text.match(CLASSIFICATION);
  const clsFirst = cls && !text.slice(0, cls.index).trim();
  html += cls
    ? `<div class="classline${clsFirst ? "" : " late"}">${inline(cls[0].trim())}` +
      (clsFirst ? "" : `<span class="flag">not the first line</span>`) + `</div>`
    : `<div class="classline absent">Michael did not state whether this is
       RESEARCH, DRAFT or BOTH. Every output is required to open with that
       line — worth reporting.</div>`;

  let body = cls ? text.replace(CLASSIFICATION, "") : text;
  const found = [];
  for (const b of BLOCK_KEYS) {
    const m = headingMatch(body, b.key);
    if (m) found.push({ ...b, at: m.index, headingLen: m[0].length });
  }
  found.sort((a, b) => a.at - b.at);

  let tail = "";
  if (found.length) {
    tail = body.slice(found[0].at);
    body = body.slice(0, found[0].at);
  }

  const notice = tail.match(NOTICE) || body.match(NOTICE);
  if (notice) { tail = tail.replace(NOTICE, ""); body = body.replace(NOTICE, ""); }

  html += `<div class="answer">${inline(body.trim())}</div>`;

  for (let i = 0; i < found.length; i++) {
    const base = found[0].at;
    const from = found[i].at - base + found[i].headingLen;
    const to = i + 1 < found.length ? found[i + 1].at - base : tail.length;
    const content = tail.slice(from, to).replace(/^[\s—:\-]+/, "").trim();
    html += `<div class="block ${found[i].cls}"><div class="h">${found[i].key}</div>
      <div class="answer">${inline(content)}</div></div>`;
  }

  /* The closing notice is mandatory on every output. If it is absent, say so.
   * Supplying it here would fake a guarantee this page does not make. */
  html += notice
    ? `<div class="notice">${esc((notice[1] || notice[0]).trim())}</div>`
    : `<div class="notice absent">The closing notice was not present in this
       output. Michael is required to end every reply with it — worth reporting.</div>`;
  return html;
}

function showSession() {
  $("sess").textContent = state.stored ? `session ${state.stored}` : "";
}

/* ------------------------- transcript, usage, search ---------------------- */

/* A time, not a date: the thread is one sitting. The full stamp goes on the
 * title attribute, and into the export, where the date matters. */
function clockTime(when) {
  return when.toLocaleTimeString("en-AU", { hour: "2-digit", minute: "2-digit" });
}

/* What a turn cost, from the session.usage frames the socket already carries.
 * Fields vary by provider, so each is read defensively and anything missing is
 * simply not shown rather than rendered as zero. */
function usageLabel(payload) {
  if (!payload || typeof payload !== "object") return "";
  const inTok = payload.input_tokens ?? payload.prompt_tokens;
  const outTok = payload.output_tokens ?? payload.completion_tokens;
  const cost = payload.cost ?? payload.total_cost ?? payload.estimated_cost;
  const parts = [];
  if (Number.isFinite(inTok) && Number.isFinite(outTok)) {
    parts.push(`${inTok} in · ${outTok} out`);
  }
  if (Number.isFinite(cost) && cost > 0) parts.push(`$${Number(cost).toFixed(4)}`);
  return parts.join(" · ");
}

/* The export. Michael's answers already carry their own citations, snapshot
 * dates and closing notice, so a plain-text transcript is a usable record on
 * its own — which is the point: the workflow is getting this into a memo. */
function transcriptMarkdown() {
  const when = new Date();
  const lines = [
    "# Michael — research transcript",
    "",
    `Exported ${when.toLocaleString("en-AU")}`,
    state.stored ? `Session ${state.stored}` : "",
    "",
    "Internal research only. Michael is not a lawyer and does not give legal",
    "advice. Every output requires review by an admitted Australian legal",
    "practitioner before use.",
    "",
    "---",
    "",
  ];
  for (const turn of state.turns) {
    lines.push(`## Question — ${turn.at.toLocaleString("en-AU")}`, "", turn.question, "");
    lines.push("## Michael", "", turn.answer || "(no answer)", "");
    if (turn.tools) lines.push(`_Consulted: ${turn.tools}_`, "");
    lines.push("---", "");
  }
  return lines.filter((line) => line !== null).join("\n");
}

function downloadTranscript() {
  if (!state.turns.length) return;
  const stamp = new Date().toISOString().slice(0, 16).replace(/[:T]/g, "-");
  const blob = new Blob([transcriptMarkdown()], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `michael-${stamp}.md`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

/* Search hides non-matching turns rather than highlighting inside them: the
 * reader is usually hunting one section number across a long answer, and
 * rewriting answer HTML to inject marks would mean re-running the citation
 * and closing-block rendering on already-rendered output. */
function filterThread(query) {
  const needle = query.trim().toLowerCase();
  const turns = [...document.querySelectorAll("#thread .turn")];
  let shown = 0;
  for (const turn of turns) {
    const hit = !needle || turn.textContent.toLowerCase().includes(needle);
    turn.classList.toggle("nomatch", !hit);
    if (hit) shown += 1;
  }
  const note = $("searchCount");
  if (note) {
    note.textContent = !needle
      ? ""
      : `${shown} of ${turns.length} ${turns.length === 1 ? "turn" : "turns"}`;
  }
}

async function copyText(text, button) {
  try {
    await navigator.clipboard.writeText(text);
    const was = button.textContent;
    button.textContent = "Copied";
    setTimeout(() => { button.textContent = was; }, 1400);
  } catch {
    button.textContent = "Press Ctrl+C";
    setTimeout(() => { button.textContent = "Copy"; }, 1800);
  }
}

/* --------------------------------- wiring -------------------------------- */

function scrollDown() {
  const main = document.querySelector("main");
  main.scrollTop = main.scrollHeight;
}

async function submit() {
  const question = $("q").value.trim();
  if (!question || state.busy) return;
  state.busy = true;
  $("askBtn").disabled = true;
  $("q").value = "";
  $("empty")?.remove();

  const at = new Date();
  const record = { question, answer: "", tools: "", at };
  state.turns.push(record);

  const turn = document.createElement("div");
  turn.className = "turn";
  turn.innerHTML =
    `<div class="asked"><div class="who">Question</div>${esc(question)}</div>
     <div class="card" style="margin-top:12px">
       <div class="cardhead"><h2>Michael</h2>
         <time datetime="${at.toISOString()}" title="${esc(at.toLocaleString("en-AU"))}"
           >${esc(clockTime(at))}</time>
         <button type="button" class="copy" hidden>Copy</button></div>
       <div class="body"><span class="working"></span></div>
       <div class="meta hidden"></div>
       <div class="tools hidden"></div></div>`;
  $("thread").appendChild(turn);
  scrollDown();

  const bodyEl = turn.querySelector(".body");
  const toolsEl = turn.querySelector(".tools");
  const metaEl = turn.querySelector(".meta");
  const copyEl = turn.querySelector(".copy");
  const tools = [];

  setComposerBusy(true);

  try {
    const answer = await ask(
      question,
      (sofar) => { bodyEl.innerHTML = renderAnswer(sofar, true); scrollDown(); },
      (name) => {
        const label = toolLabel(name);
        if (!label) return; // unrecognised - dropped, never shown raw
        if (!tools.includes(label)) tools.push(label);
        toolsEl.classList.remove("hidden");
        toolsEl.textContent = "consulted: " + tools.join(" · ");
        record.tools = tools.join(" · ");
      },
      (phase) => { $("status").textContent = phase + "…"; },
      (usage) => {
        const label = usageLabel(usage);
        if (!label) return;
        metaEl.classList.remove("hidden");
        metaEl.textContent = label;
      },
    );
    bodyEl.innerHTML = renderAnswer(answer, false);
    record.answer = answer;
    copyEl.hidden = false;
    copyEl.onclick = () => copyText(answer, copyEl);
    $("status").textContent = "";
  } catch (err) {
    bodyEl.innerHTML =
      `<div class="banner"><div class="h">NO ANSWER</div><div class="b">${
        esc(err.message || err)}</div></div>`;
    record.answer = "";
    $("status").textContent = "";
  } finally {
    state.busy = false;
    setComposerBusy(false);
    scrollDown();
    $("q").focus();
  }
}

/* The send control becomes Stop while a turn is in flight. One control, not
 * two: a disabled Send beside a live Stop is two things to read where the
 * reader only ever has one choice. */
function setComposerBusy(busy) {
  const btn = $("askBtn");
  btn.disabled = false;
  btn.classList.toggle("stop", busy);
  btn.setAttribute("aria-label", busy ? "Stop this answer" : "Send question");
  const dl = $("downloadBtn");
  if (dl) dl.disabled = state.turns.length === 0;
}

$("askBtn").onclick = () => {
  if (state.busy) { cancelTurn(); return; }
  submit();
};

/* Enter-to-send punishes anyone drafting a long factual question, which is
 * the normal case here, so it is a preference rather than a rule. */
$("q").addEventListener("keydown", (e) => {
  if (e.key !== "Enter") return;
  const wantsCtrl = PREFS.sendKey === "ctrl";
  const send = wantsCtrl ? (e.ctrlKey || e.metaKey) : !e.shiftKey;
  if (send) { e.preventDefault(); if (!state.busy) submit(); }
});

loadPrefs();

/* Toolbar. Each control is absent-tolerant so michael.js keeps working against
 * the test harness's stub DOM, which has none of them. */
$("searchBox")?.addEventListener("input", (e) => filterThread(e.target.value));
$("downloadBtn")?.addEventListener("click", downloadTranscript);
if ($("downloadBtn")) $("downloadBtn").disabled = true;

const sendKeyEl = $("sendKey");
if (sendKeyEl) {
  sendKeyEl.value = PREFS.sendKey;
  sendKeyEl.addEventListener("change", (e) => {
    PREFS.sendKey = e.target.value === "ctrl" ? "ctrl" : "enter";
    savePrefs();
    const hint = $("sendHint");
    if (hint) {
      hint.textContent = PREFS.sendKey === "ctrl"
        ? "Ctrl+Enter to send · Enter for a new line"
        : "Enter to send · Shift+Enter for a new line";
    }
  });
}

/* Node-only export, for tests. `module` is undefined in the browser, so this
 * is a no-op there and changes nothing about how the page behaves. Exports
 * the real functions the tests exercise, not a reimplementation of them. */
if (typeof module !== "undefined") {
  module.exports = { renderAnswer, headingMatch, NOT_COVERED, toolLabel, inline, esc,
    connect, CITATION, usageLabel, transcriptMarkdown, clockTime, state, PREFS,
    renderMarkdown };
}
