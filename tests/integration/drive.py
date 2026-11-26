"""Front the fake env with agentenv-vfs and run shell lines as two roles: python drive.py VFS_URL FAKE_MCP_URL."""
import asyncio
import json
import sys
from pathlib import Path

from agentenv_protocol import DataPart, client
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

VFS, FAKE = sys.argv[1], sys.argv[2]


async def lines(role, cmds):
    async with streamablehttp_client(f"{VFS}/mcp", headers={"AgentEnv-Role": role}) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = (await s.list_tools()).tools
            print(f"[{role}] shell description: {len(tools[0].description)} chars; first lines:")
            print("   " + "\n   ".join(tools[0].description.splitlines()[:5]))
            for c in cmds:
                res = await s.call_tool("shell", {"command": c})
                text = "".join(x.text for x in res.content if x.type == "text").strip().replace("\n", " | ")
                print(f"[{role}] $ {c}\n   isError={res.isError} {text[:300]}")


async def main():
    await client.add_data(VFS, [DataPart(data={"mcp_url": FAKE, "role": "alice"})], timeout=300)
    await lines("alice", ["add_note --title t1 --body hello --tags a --tags b", "list_notes --limit 2 | jq -c .",
                          "fail_always --reason no; echo exit=$?", "add_note --help | head -6", "curl -s http://example.com",
                          "date", "ls /", "list_notes --limit 3 | python3 -c \"import json,sys; print(len(json.load(sys.stdin)['notes']))\"",
                          "python3 -c 'import urllib.request; urllib.request.urlopen(\"http://example.com\", timeout=5)'"])
    await lines("bob", ["list_notes --limit 1"])
    print((await client.get_data(VFS, timeout=30)).model_dump()["parts"][0]["data"])
    # The run-record step's script, against this server: every line and call, in order, by role.
    sys.path.insert(0, str(Path(__file__).parents[2] / "src" / "agentenv_vfs" / "calls"))
    import calls
    log = await asyncio.to_thread(calls.run, {"env": {"url": VFS, "headers": {}}, "args": {}, "results": {}})
    print("call log:", {k: v for k, v in log.items() if k != "log"})
    for row in log["log"][:4]:
        print("  ", json.dumps(row)[:200])
    first = [r for r in log["log"] if r["kind"] == "call"][0]
    assert first["role"] == "alice" and first["tool"] == "add_note" and first["line"] == 1 and first["ok"]
    assert log["failed_calls"] >= 1 and log["by_tool"]["list_notes"] >= 2
    # What each role was shown: alice's commands in the shell description (her role named at data/add), bob's not.
    print("shown:", json.dumps(log["shown"]))
    assert log["shown"]["alice"]["commands"] == 3 and log["shown"]["alice"]["description_chars"] > 0
    assert log["shown"]["alice"]["help_lines"] == 1 and log["shown"]["bob"]["description_chars"] is None


asyncio.run(main())
