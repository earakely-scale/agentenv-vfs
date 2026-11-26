"""Tool locks hold through the shell: python drive_locks.py VFS_URL LOCKED_URL LOCKED_MCP_URL (the locked env as this
machine reaches it, and its MCP endpoint as the agentvfs server reaches it). A locked tool is no command in the role's
workspace; a role locked after its workspace opened is refused by the env; an unlocked role's call goes through."""
import asyncio
import json
import sys
from pathlib import Path

import httpx
from agentenv_protocol import DataPart, client
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

VFS, LOCKED, LOCKED_MCP = sys.argv[1:4]


async def shell(role, command):
    async with streamablehttp_client(f"{VFS}/mcp", headers={"AgentEnv-Role": role}) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            res = await s.call_tool("shell", {"command": command})
    text = "".join(x.text for x in res.content if x.type == "text").strip()
    print(f"[{role}] $ {command}\n   isError={res.isError} {text[:200]}")
    return res.isError, text


def lock(role, tools):
    reply = httpx.post(f"{LOCKED}/tools/disable", json={"role": role, "tools": tools}, timeout=30)
    reply.raise_for_status()
    print(f"locked {tools} for {role}: {reply.json()}")


async def main():
    lock("reader", ["delete_note"])
    await client.add_data(VFS, [DataPart(data={"envs": [{"name": "locked", "mcp_url": LOCKED_MCP}], "role": "reader"})],
                          timeout=300)
    # Hidden: the reader's workspace has no delete_note command.
    refused, _ = await shell("reader", "delete_note --note_id 1")
    assert refused
    refused, text = await shell("reader", "list_notes | jq -s length")
    assert not refused and text == "3"
    # Refused by the env: this role's workspace opened (with delete_note) before its lock.
    refused, _ = await shell("late", "list_notes | jq -s length")
    assert not refused
    lock("late", ["delete_note"])
    refused, text = await shell("late", "delete_note --note_id 1")
    assert refused and "disabled for role 'late'" in text, text
    # Allowed: an unlocked role.
    refused, _ = await shell("writer", "delete_note --note_id 2")
    assert not refused
    notes = (await client.get_data(LOCKED, timeout=30)).model_dump()["parts"][0]["data"]["notes"]
    assert [n["id"] for n in notes] == [1, 3], notes
    # The call log records what each role was shown, and the one refused call.
    sys.path.insert(0, str(Path(__file__).parents[2] / "src" / "agentenv_vfs" / "calls"))
    import calls
    log = await asyncio.to_thread(calls.run, {"env": {"url": VFS, "headers": {}}, "args": {}, "results": {}})
    print("shown:", json.dumps(log["shown"]))
    tools = {r["role"]: r["tools"] for r in log["log"] if r["kind"] == "shown"}
    assert "delete_note" not in tools["reader"] and "delete_note" in tools["late"] and "delete_note" in tools["writer"]
    assert log["shown"]["reader"]["description_chars"] and log["shown"]["late"]["description_chars"] is None
    failed = [(r["role"], r["tool"]) for r in log["log"] if r["kind"] == "call" and not r["ok"]]
    assert failed == [("late", "delete_note")], failed
    print("locks hold through the shell")


asyncio.run(main())
