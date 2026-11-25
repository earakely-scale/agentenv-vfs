"""Read the notes env's notes back (data/get) and check the task's note is there, tagged."""
import json
import urllib.request


def run(input):
    env = input["env"]
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "data/get", "params": {}}).encode()
    req = urllib.request.Request(f"{env['url'].rstrip('/')}/agentenv", data=body, method="POST",
                                 headers={"Content-Type": "application/json", **(env.get("headers") or {})})
    with urllib.request.urlopen(req, timeout=60) as r:
        notes = next(p["data"] for p in json.loads(r.read())["result"]["parts"] if p.get("kind") == "data")["notes"]
    found = any("Lee Park" in n["title"] + n["body"] and "sales" in n["tags"] for n in notes)
    return {"notes": len(notes), "score": 1.0 if found else 0.0}
