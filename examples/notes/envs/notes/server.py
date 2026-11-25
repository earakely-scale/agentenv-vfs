"""A small notes service: an agent-env env with four MCP tools, and its notes on data/get."""
from agentenv_protocol import AgentEnvEnvironment, DataPart, environment_card, get_data, tool


@environment_card(name="notes")
class Notes(AgentEnvEnvironment):
    """Team meeting notes."""

    def __init__(self) -> None:
        self.notes: list[dict] = [
            {"id": 1, "title": "Ops weekly", "body": "Vendor contracts renew in March.", "tags": ["ops"]},
        ]

    @tool()
    async def add_note(self, title: str, body: str, tags: list[str] | None = None) -> dict:
        """Add a note; returns it with its id."""
        note = {"id": max([n["id"] for n in self.notes], default=0) + 1, "title": title, "body": body,
                "tags": tags or []}
        self.notes.append(note)
        return note

    @tool()
    async def list_notes(self, tag: str | None = None) -> list[dict]:
        """List notes, optionally only those with a tag."""
        return [n for n in self.notes if tag is None or tag in n["tags"]]

    @tool()
    async def search_notes(self, query: str) -> list[dict]:
        """Notes whose title or body contains the query (case-insensitive)."""
        q = query.lower()
        return [n for n in self.notes if q in n["title"].lower() or q in n["body"].lower()]

    @tool()
    async def delete_note(self, note_id: int) -> dict:
        """Delete a note by id."""
        before = len(self.notes)
        self.notes = [n for n in self.notes if n["id"] != note_id]
        return {"deleted": before != len(self.notes)}

    @get_data
    async def _state(self, paths: list | None = None) -> list:
        return [DataPart(data={"notes": self.notes})]


if __name__ == "__main__":
    Notes().serve()
