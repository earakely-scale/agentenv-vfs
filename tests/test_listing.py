import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src" / "agentenv_vfs" / "server"))
from listing import HEADER, listing, usage  # noqa: E402


def test_usage_lines():
    tool = {"name": "send_message", "description": "Send a\n message.", "parameters": {
        "type": "object", "required": ["channel_id", "text"],
        "properties": {"channel_id": {"type": "string"}, "text": {"type": "string"},
                       "thread": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                       "attachments": {"type": "array", "items": {"type": "object"}},
                       "count": {"type": ["integer", "null"]}}}}
    assert usage(tool) == ("send_message --channel_id <string> --text <string> [--thread <string>] "
                           "[--attachments <JSON>] [--count <integer>]")
    text = listing([tool, {"name": "ping"}])
    assert text.startswith(HEADER)
    assert "- `send_message --channel_id <string> --text <string> [--thread <string>] [--attachments <JSON>] " \
           "[--count <integer>]`: Send a message." in text
    assert "- `ping`\n" in text
