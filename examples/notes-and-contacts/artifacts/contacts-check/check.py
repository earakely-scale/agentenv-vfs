"""Read the contacts env's contacts back (data/get) and check the task's new contact is there."""
import json
import urllib.request


def run(input):
    env = input["env"]
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "data/get", "params": {}}).encode()
    req = urllib.request.Request(f"{env['url'].rstrip('/')}/agentenv", data=body, method="POST",
                                 headers={"Content-Type": "application/json", **(env.get("headers") or {})})
    with urllib.request.urlopen(req, timeout=60) as r:
        contacts = next(p["data"] for p in json.loads(r.read())["result"]["parts"] if p.get("kind") == "data")["contacts"]
    found = any(c["name"] == "Lee Park" and c["email"] == "lee@contoso.com" for c in contacts)
    return {"contacts": len(contacts), "score": 1.0 if found else 0.0}
