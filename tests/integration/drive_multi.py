"""Front two envs with agentenv-vfs, then two that serve a tool of the same name:
python drive_multi.py VFS_URL NOTES_MCP CRM_MCP CLASH_MCP."""
import asyncio
import json
import sys
from pathlib import Path

from agentenv_protocol import DataPart, client
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

VFS, NOTES, CRM, CLASH = sys.argv[1:5]


async def main():
    envs = [{"name": "notes", "mcp_url": NOTES}, {"name": "crm", "mcp_url": CRM}]
    await client.add_data(VFS, [DataPart(data={"envs": envs, "role": "alice"})], timeout=300)
    async with streamablehttp_client(f"{VFS}/mcp", headers={"AgentEnv-Role": "alice"}) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            for c in ["add_note --title t --body b", "add_contact --name Ann"]:
                res = await s.call_tool("shell", {"command": c})
                print(f"$ {c}\n   isError={res.isError} {res.content[0].text.strip()[:160]}")
    sys.path.insert(0, str(Path(__file__).parents[2] / "src" / "agentenv_vfs" / "calls"))
    import calls
    log = await asyncio.to_thread(calls.run, {"env": {"url": VFS, "headers": {}}, "args": {}, "results": {}})
    routed = {r["tool"]: r["env"] for r in log["log"] if r["kind"] == "call"}
    print("routed:", routed)
    assert routed == {"add_note": "notes", "add_contact": "crm"}, routed
    try:
        await client.add_data(VFS, [DataPart(data={"envs": [envs[0], {"name": "clash", "mcp_url": CLASH}],
                                                   "role": "alice"})], timeout=300)
        raise SystemExit("two envs serving list_notes were not refused")
    except RuntimeError as e:
        assert "both serve a tool named 'list_notes'" in str(e), e
        print("clash refused:", str(e)[:160])


asyncio.run(main())
