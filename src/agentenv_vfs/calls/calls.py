"""Copy the agentvfs env's log of the agent's shell lines and tool calls into the run's record: run_code on the
agentvfs env (standard library only; agent-env may run it on the env's host). Returns a summary and the log."""
import base64
import collections
import json
import urllib.request

MAX_ROWS = 20000  # past this the log keeps its first rows, and says so


def run(input):
    env = input["env"]
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "data/get", "params": {"paths": ["calls.jsonl"]}}).encode()
    req = urllib.request.Request(f"{env['url'].rstrip('/')}/agentenv", data=body, method="POST",
                                 headers={"Content-Type": "application/json", **(env.get("headers") or {})})
    with urllib.request.urlopen(req, timeout=120) as r:
        reply = json.loads(r.read().decode())
    if "error" in reply:
        raise RuntimeError(f"data/get failed: {reply['error']}")
    data = b"".join(base64.b64decode(p["file"]["bytes"]) for p in reply["result"]["parts"] if p.get("kind") == "file")
    rows = [json.loads(line) for line in data.decode().splitlines() if line.strip()]
    calls = [r for r in rows if r["kind"] == "call"]
    lines = [r for r in rows if r["kind"] == "line"]
    # What each role was shown, and how often it looked its commands up in the workspace.
    shown = {r["role"]: {"commands": r["commands"], "description_chars": r["description_chars"],
                         "skill_md_lines": sum("/SKILL.md" in x["command"] for x in lines if x["role"] == r["role"]),
                         "help_lines": sum("--help" in x["command"] for x in lines if x["role"] == r["role"])}
             for r in rows if r["kind"] == "shown"}
    return {"lines": len(lines), "calls": len(calls), "failed_calls": sum(not r["ok"] for r in calls),
            "by_tool": dict(collections.Counter(r["tool"] for r in calls).most_common()), "shown": shown,
            "truncated": len(rows) > MAX_ROWS, "log": rows[:MAX_ROWS]}
