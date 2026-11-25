"""A plain MCP server for the integration test: each tool echoes the AgentEnv-Role header its call arrived with."""
import json

from mcp.server.fastmcp import Context, FastMCP

mcp = FastMCP("fake", host="0.0.0.0", port=18765, stateless_http=True)


def role(ctx: Context) -> str:
    return ctx.request_context.request.headers.get("agentenv-role", "none")


@mcp.tool()
def add_note(title: str, body: str, tags: list[str] | None = None, ctx: Context = None) -> str:
    """Add a note."""
    return json.dumps({"added": title, "body": body, "tags": tags or [], "role": role(ctx)})


@mcp.tool()
def list_notes(limit: int = 10, ctx: Context = None) -> str:
    """List notes."""
    return json.dumps({"notes": [{"title": f"n{i}"} for i in range(limit)], "role": role(ctx)})


@mcp.tool()
def fail_always(reason: str) -> str:
    """Always fails."""
    raise ValueError(f"refused: {reason}")


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
