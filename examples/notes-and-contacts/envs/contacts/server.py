"""A small contacts service: an agent-env env with three MCP tools, and its contacts on data/get."""
from agentenv_protocol import AgentEnvEnvironment, DataPart, environment_card, get_data, tool


@environment_card(name="contacts")
class Contacts(AgentEnvEnvironment):
    """The team's contacts."""

    def __init__(self) -> None:
        self.contacts: list[dict] = [{"id": 1, "name": "Dana Ruiz", "email": "dana@example.com", "company": "Northwind"}]

    @tool()
    async def add_contact(self, name: str, email: str, company: str | None = None) -> dict:
        """Add a contact; returns it with its id."""
        contact = {"id": max([c["id"] for c in self.contacts], default=0) + 1, "name": name, "email": email,
                   "company": company}
        self.contacts.append(contact)
        return contact

    @tool()
    async def find_contacts(self, query: str) -> list[dict]:
        """Contacts whose name, email or company contains the query (case-insensitive)."""
        q = query.lower()
        return [c for c in self.contacts if any(q in (c.get(k) or "").lower() for k in ("name", "email", "company"))]

    @tool()
    async def list_contacts(self) -> list[dict]:
        """Every contact."""
        return self.contacts

    @get_data
    async def _state(self, paths: list | None = None) -> list:
        return [DataPart(data={"contacts": self.contacts})]


if __name__ == "__main__":
    Contacts().serve()
