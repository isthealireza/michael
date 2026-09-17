/* Michael's presentable surface.
 *
 * A CLIENT of the existing answering path, never a second one. It calls
 * prompt.submit on /api/ws - the same server-side choke point every dashboard
 * submit passes through - so Hermes runs the model with MICHAEL.md, calls the
 * three MCP tools, and produces the three closing blocks exactly as it always
 * has. This file only renders what comes back. It generates no legal content,
 * so it cannot weaken a guarantee: if Michael emits no citation, none is shown.
 */
"use strict";

const $ = (id) => document.getElementById(id);
const GATEWAY_PROTOCOL = "hermes-gateway-v1";

/* Only message.delta is the answer. reasoning.delta and thinking.delta are the
 * model's private working and must never reach the page - concatenating all
 * three renders the reasoning trace as if it were the advice. */
const ANSWER_FRAME = "message.delta";

async function api(path, body) {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify(body || {}),
  });
  const text = await res.text();
  let parsed = null;
  try { parsed = text ? JSON.parse(text) : null; } catch { /* non-json */ }
  if (!res.ok) throw new Error((parsed && parsed.detail) || `HTTP ${res.status}`);
  return parsed;
}

$("loginBtn").onclick = async () => {
  const btn = $("loginBtn");
  btn.disabled = true;
  $("loginStatus").textContent = "signing in…";
  try {
    await api("/auth/password-login", {
      provider: "basic", username: "michael", password: $("pw").value,
    });
    $("loginCard").classList.add("hidden");
    $("askCard").classList.remove("hidden");
    $("q").focus();
  } catch (err) {
    $("loginStatus").textContent = String(err.message || err);
    btn.disabled = false;
  }
};

$("pw").addEventListener("keydown", (e) => { if (e.key === "Enter") $("loginBtn").click(); });

$("askBtn").onclick = async () => {
  const question = $("q").value.trim();
  if (!question) return;
  $("askBtn").disabled = true;
  $("out").innerHTML = "";
  $("tools").classList.add("hidden");
  $("tools").textContent = "";
  $("status").textContent = "connecting…";
  try {
    const answer = await ask(question);
    render(answer);
    $("status").textContent = "";
  } catch (err) {
    $("status").textContent = "failed: " + String(err.message || err);
  } finally {
    $("askBtn").disabled = false;
  }
};

async function ask(question) {
  const { ticket } = await api("/api/auth/ws-ticket", {});
  const scheme = location.protocol === "https:" ? "wss:" : "ws:";
  const ws = new WebSocket(
    `${scheme}//${location.host}/api/ws?ticket=${encodeURIComponent(ticket)}`,
    [GATEWAY_PROTOCOL],
  );

  const pending = new Map();
  let answer = "";
  let settle, fail;
  const done = new Promise((res, rej) => { settle = res; fail = rej; });

  const call = (method, params, id) =>
    new Promise((res) => {
      pending.set(id, res);
      ws.send(JSON.stringify({ id, method, params }));
    });

  ws.onerror = () => fail(new Error("connection failed"));
  ws.onclose = () => { if (!answer) fail(new Error("connection closed before an answer")); };

  ws.onmessage = (event) => {
    let frame;
    try { frame = JSON.parse(event.data); } catch { return; }

    if (frame.id && pending.has(frame.id)) {
      const res = pending.get(frame.id);
      pending.delete(frame.id);
      res(frame);
      return;
    }

    const params = frame.params || {};
    const kind = params.type || frame.method;
    const payload = params.payload && typeof params.payload === "object" ? params.payload : params;

    if (kind === ANSWER_FRAME) {
      const piece = payload.delta ?? payload.text ?? payload.content ?? "";
      if (typeof piece === "string") answer += piece;
      $("status").textContent = `receiving… ${answer.length} characters`;
    } else if (kind === "tool.start") {
      const name = payload.name || payload.tool || "tool";
      $("tools").classList.remove("hidden");
      $("tools").textContent += (($("tools").textContent && " · ") || "") + name;
    } else if (kind === "message.complete" || kind === "turn.end") {
      settle();
    }
  };

  await new Promise((res, rej) => {
    ws.onopen = res;
    setTimeout(() => rej(new Error("timed out opening the connection")), 20000);
  });

  $("status").textContent = "opening a session…";
  const listed = await call("session.list", {}, "list");
  const sessions = ((listed.result || {}).sessions) || [];
  if (!sessions.length) throw new Error("no session available to resume");

  const resumed = await call("session.resume", { session_id: sessions[0].id }, "resume");
  const live = (resumed.result || {}).session_id;
  if (!live) throw new Error("could not open a live session");

  $("status").textContent = "Michael is working…";
  ws.send(JSON.stringify({
    id: "submit", method: "prompt.submit",
    params: { session_id: live, text: question },
  }));

  await done;
  ws.close();
  return answer;
}

/* ---------------- rendering Michael's output as what it is ---------------- */

const esc = (s) => s.replace(/[&<>"]/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

/* A pinpoint as Michael emits it: "Privacy Act 1988 (Cth) s 26WL (snapshot
 * 2026-06-04)". Also matches "Sch 1 cl 11" for a Schedule clause, which is a
 * different provision from a section of the same number. */
const CITATION = /\b([A-Z][A-Za-z'’\- ]+?(?:Act|Regulations|Code|Rules)\s+\d{4}(?:\s*\((?:Cth|WA|NSW|Vic|Qld|SA|Tas|NT|ACT|Imp)\))?)\s+(s\s+[\w.]+|Sch\s+\w+\s+cl\s+[\w.]+)(\s*\(snapshot\s+[\d-]+\))?/g;

const MISSING = /\[MISSING:\s*([^\]]+)\]/g;

/* The three closing blocks, and the NOT COVERED line. Michael is required to
 * end every output with all three - an answer, a refusal and a NOT COVERED
 * reply alike - so their absence is itself worth showing rather than hiding. */
const BLOCKS = [
  { key: "OPEN ITEMS", cls: "" },
  { key: "VERIFY BEFORE USE", cls: "verify" },
];
const NOT_COVERED = /^.*NOT COVERED\b.*$/mi;
const NOTICE = /Internal research only\.[\s\S]*?practitioner\./i;

function inline(text) {
  let html = esc(text);
  html = html.replace(MISSING, (_m, item) =>
    `<span class="missing">MISSING: ${esc(item.trim())}</span>`);
  html = html.replace(CITATION, (_m, act, pin, snap) =>
    `<span class="cite">${esc(act)} ${esc(pin)}${snap ? esc(snap) : ""}</span>`);
  return html;
}

function render(raw) {
  const out = $("out");
  const text = (raw || "").trim();
  if (!text) {
    out.innerHTML = `<div class="card"><h2>No answer</h2>
      <div class="answer">Michael returned nothing. That is a failed run, not an
      empty answer — try again rather than treating this as a result.</div></div>`;
    return;
  }

  let html = "";

  /* NOT COVERED first, and unmissable. Michael returning "not covered" is a
   * real answer - the corpus does not cover the question - and a reader must
   * never mistake it for a partial one. */
  const nc = text.match(NOT_COVERED);
  if (nc) {
    html += `<div class="banner"><div class="h">NOT COVERED</div>
      <div class="b">${inline(nc[0].trim())}</div></div>`;
  }

  /* Split the body from the closing blocks. */
  let body = text;
  const found = [];
  for (const b of BLOCKS) {
    const at = body.indexOf(b.key);
    if (at !== -1) found.push({ ...b, at });
  }
  found.sort((x, y) => x.at - y.at);

  let tail = "";
  if (found.length) {
    tail = body.slice(found[0].at);
    body = body.slice(0, found[0].at);
  }

  const notice = tail.match(NOTICE) || body.match(NOTICE);
  if (notice) tail = tail.replace(NOTICE, "");

  html += `<div class="card"><h2>Answer</h2>
    <div class="answer">${inline(body.trim())}</div>`;

  for (let i = 0; i < found.length; i++) {
    const start = found[i].at - found[0].at + found[i].key.length;
    const end = i + 1 < found.length ? found[i + 1].at - found[0].at : tail.length;
    const content = tail.slice(start, end).replace(/^[\s—:-]+/, "").trim();
    html += `<div class="block ${found[i].cls}"><div class="h">${found[i].key}</div>
      <div class="answer">${inline(content)}</div></div>`;
  }

  /* The closing notice is mandatory. If Michael did not emit it, say so -
   * silently supplying it here would fake a guarantee this page does not make. */
  html += notice
    ? `<div class="notice">${esc(notice[0].trim())}</div>`
    : `<div class="notice" style="color:#7f1d1d">The closing notice was not
       present in this output. That is a rule violation worth reporting.</div>`;

  out.innerHTML = html + `</div>`;
}
