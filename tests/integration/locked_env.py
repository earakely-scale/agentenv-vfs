"""A notes env that keeps per-role tool locks under agent-env's gateway rules (agentenv_protocol's tool access), for
the lock test: POST /tools/disable {"role", "tools"} hides a tool from a role's tools/list and refuses its calls."""
from agentenv_protocol import AgentEnvEnvironment, DataPart, environment_card, get_data, tool


@environment_card(name="locked")
class Locked(AgentEnvEnvironment):
    """Notes, with tool locks per role."""

    tool_access = True

    def __init__(self) -> None:
        self.notes = [{"id": i, "title": f"n{i}"} for i in (1, 2, 3)]

    @tool()
    async def list_notes(self) -> list[dict]:
        """Every note."""
        return self.notes

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
    Locked().serve()
