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

const state = { live: null, stored: null, busy: false };

/* ------------------------------- transport ------------------------------- */

async function api(path, body) {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify(body || {}),
  });
  const text = await res.text();
  let parsed = null;
  try { parsed = text ? JSON.parse(text) : null; } catch { /* not json */ }
  if (!res.ok) throw new Error((parsed && parsed.detail) || `HTTP ${res.status}`);
  return parsed;
}

function sessionTitle() {
  const when = new Date().toLocaleString("en-AU",
    { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
  return `MICHAEL web — ${when}`;
}

/* One socket per turn. Michael's turns are long but discrete, and a socket
 * held open across idle minutes is a reconnect problem for no benefit. */
async function connect() {
  const { ticket } = await api("/api/auth/ws-ticket", {});
  const scheme = location.protocol === "https:" ? "wss:" : "ws:";
  const ws = new WebSocket(
    `${scheme}//${location.host}/api/ws?ticket=${encodeURIComponent(ticket)}`,
    [GATEWAY_PROTOCOL],
  );
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

async function ask(question, onDelta, onTool, onPhase) {
  const ws = await connect();
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
    try { ws.close(); } catch { /* already closed */ }
  }
}

/* ------------------------------- rendering ------------------------------- */

const esc = (s) => String(s).replace(/[&<>"]/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

/* A pinpoint as Michael emits it: "Privacy Act 1988 (Cth) s 26WL (snapshot
 * 2026-06-04)". "Sch 1 cl 11" is matched too: a Schedule clause is a different
 * provision from a section of the same number, and the corpus contains both. */
const CITATION = /\b([A-Z][A-Za-z'’\-. ]+?(?:Act|Regulations|Code|Rules|Award)\s+\d{4}(?:\s*\((?:Cth|WA|NSW|Vic|Qld|SA|Tas|NT|ACT|Imp)\))?)\s+(ss?\s+[\w.()]+|Sch\s+\w+\s+cl\s+[\w.]+)(\s*\(snapshot\s+[\d-]+\))?/g;
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
  return html;
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

/* --------------------------------- wiring -------------------------------- */

$("loginBtn").onclick = async () => {
  const btn = $("loginBtn");
  btn.disabled = true;
  $("loginStatus").textContent = "signing in…";
  try {
    await api("/auth/password-login",
      { provider: "basic", username: "michael", password: $("pw").value });
    $("loginCard").classList.add("hidden");
    $("thread").classList.remove("hidden");
    $("askArea").classList.remove("hidden");
    $("q").focus();
  } catch (err) {
    $("loginStatus").textContent = String(err.message || err);
    btn.disabled = false;
  }
};
$("pw").addEventListener("keydown", (e) => { if (e.key === "Enter") $("loginBtn").click(); });

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

  const turn = document.createElement("div");
  turn.className = "turn";
  turn.innerHTML =
    `<div class="asked"><div class="who">Question</div>${esc(question)}</div>
     <div class="card" style="margin-top:12px">
       <h2>Michael</h2><div class="body"><span class="working"></span></div>
       <div class="tools hidden"></div></div>`;
  $("thread").appendChild(turn);
  scrollDown();

  const bodyEl = turn.querySelector(".body");
  const toolsEl = turn.querySelector(".tools");
  const tools = [];

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
      },
      (phase) => { $("status").textContent = phase + "…"; },
    );
    bodyEl.innerHTML = renderAnswer(answer, false);
    $("status").textContent = "";
  } catch (err) {
    bodyEl.innerHTML =
      `<div class="banner"><div class="h">NO ANSWER</div><div class="b">${
        esc(err.message || err)}</div></div>`;
    $("status").textContent = "";
  } finally {
    state.busy = false;
    $("askBtn").disabled = false;
    scrollDown();
    $("q").focus();
  }
}

$("askBtn").onclick = submit;
$("q").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); }
});

/* Node-only export, for tests. `module` is undefined in the browser, so this
 * is a no-op there and changes nothing about how the page behaves. Exports
 * the real functions the tests exercise, not a reimplementation of them. */
if (typeof module !== "undefined") {
  module.exports = { renderAnswer, headingMatch, NOT_COVERED, toolLabel, inline, esc };
}
