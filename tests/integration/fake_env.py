"""A plain MCP server for the integration test: each tool echoes the AgentEnv-Role header its call arrived with.
FAKE_KIND picks its tools: notes (the default), crm, or clash (crm's, and a list_notes of its own)."""
import json
import os

from mcp.server.fastmcp import Context, FastMCP

mcp = FastMCP("fake", host="0.0.0.0", port=18765, stateless_http=True)


def role(ctx: Context) -> str:
    return ctx.request_context.request.headers.get("agentenv-role", "none")


KIND = os.environ.get("FAKE_KIND", "notes")


def notes_tool(fn):
    return mcp.tool()(fn) if KIND == "notes" else fn


def crm_tool(fn):
    return mcp.tool()(fn) if KIND in ("crm", "clash") else fn


@notes_tool
def add_note(title: str, body: str, tags: list[str] | None = None, ctx: Context = None) -> str:
    """Add a note."""
    return json.dumps({"added": title, "body": body, "tags": tags or [], "role": role(ctx)})


@notes_tool
def list_notes(limit: int = 10, ctx: Context = None) -> str:
    """List notes."""
    return json.dumps({"notes": [{"title": f"n{i}"} for i in range(limit)], "role": role(ctx)})


@notes_tool
def fail_always(reason: str) -> str:
    """Always fails."""
    raise ValueError(f"refused: {reason}")


@crm_tool
def add_contact(name: str, ctx: Context = None) -> str:
    """Add a contact."""
    return json.dumps({"contact": name, "role": role(ctx)})


if KIND == "clash":
    @mcp.tool(name="list_notes")
    def clash_list_notes() -> str:
        """The CRM's own notes."""
        return "[]"


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
