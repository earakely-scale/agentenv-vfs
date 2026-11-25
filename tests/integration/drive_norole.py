"""No role at data/add: the first shell call opens the workspace; later calls must still reach the env."""
import asyncio, sys
from agentenv_protocol import DataPart, client
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
VFS, FAKE = sys.argv[1], sys.argv[2]
async def main():
    await client.add_data(VFS, [DataPart(data={"mcp_url": FAKE})], timeout=300)
    for round in range(2):  # separate MCP sessions, as separate agent turns would be
        async with streamablehttp_client(f"{VFS}/mcp") as (r, w, _):
            async with ClientSession(r, w) as s:
                await s.initialize()
                for c in ["list_notes --limit 1", "add_note --title x --body y", "list_notes --limit 2 | jq -c .role"]:
                    res = await s.call_tool("shell", {"command": c})
                    print(round, c, "->", res.isError, "".join(x.text for x in res.content if x.type == "text").strip()[:120])
asyncio.run(main())
