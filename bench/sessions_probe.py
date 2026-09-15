"""Attribute Hermes sessions to (model, prompt) and count Michael MCP calls.

Runs inside the container. The --usage-file did not record a session id, so
sessions are matched by their model and their first user message instead, which
is a stronger join anyway: it cannot drift if runs are retried.
"""

import json
import sqlite3

con = sqlite3.connect("file:/opt/data/state.db?mode=ro", uri=True)

sessions = {}
for sid, model in con.execute("SELECT id, model FROM sessions"):
    sessions[sid] = {"model": model, "prompt": None, "michael_calls": {}, "other_tools": {}}

for sid, role, content, tool_name, tool_calls in con.execute(
    "SELECT session_id, role, content, tool_name, tool_calls FROM messages ORDER BY id"
):
    s = sessions.get(sid)
    if s is None:
        continue
    text = (
        content
        if isinstance(content, str)
        else (content.decode("utf-8", "replace") if content else "")
    )
    if role == "user" and s["prompt"] is None and text.strip():
        s["prompt"] = text.strip()[:90]
    names = []
    if tool_name:
        names.append(tool_name)
    if tool_calls:
        blob = tool_calls if isinstance(tool_calls, str) else tool_calls.decode("utf-8", "replace")
        import re

        names += re.findall(r'"name"\s*:\s*"([\w.-]+)"', blob)
    for n in names:
        bucket = "michael_calls" if "michael__" in n else "other_tools"
        key = n.replace("michael__", "")
        s[bucket][key] = s[bucket].get(key, 0) + 1

out = []
for sid, s in sessions.items():
    label = (
        "contract"
        if "casual employment contract" in (s["prompt"] or "")
        else ("uncovered" if "data breach" in (s["prompt"] or "") else "other")
    )
    out.append(
        {
            "session_id": sid,
            "model": s["model"],
            "kind": label,
            "prompt_head": s["prompt"],
            "michael_calls": s["michael_calls"],
            "michael_total": sum(s["michael_calls"].values()),
            "other_tools": s["other_tools"],
        }
    )
out.sort(key=lambda r: r["session_id"])
print(json.dumps(out, indent=2))
