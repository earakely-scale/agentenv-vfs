"""Tools as the shell commands that carry them, one usage line each (`<tool> --flag <type> [--opt <type>]`: what it
does), in the format of agentvfs/vfs-bench's tool listings."""

HEADER = ("Each tool is a shell command, `<tool> [--flag value ...]`, whose output is the tool's reply; a list or an "
          "object is one JSON value (`--ids '[\"a\",\"b\"]'`), and `<tool> --help` says more.")


def _kind(schema: dict) -> str:
    kind = schema.get("type")
    if kind is None and ("anyOf" in schema or "oneOf" in schema):
        kinds = [s.get("type") for s in schema.get("anyOf") or schema.get("oneOf") if s.get("type") != "null"]
        kind = kinds[0] if len(kinds) == 1 else "object"
    if isinstance(kind, list):
        real = [k for k in kind if k != "null"]
        kind = real[0] if len(real) == 1 else "object"
    return "JSON" if kind in ("array", "object", None) else kind


def usage(tool: dict) -> str:
    params = (tool.get("parameters") or {}).get("properties") or {}
    required = set((tool.get("parameters") or {}).get("required") or [])
    flags = [f"--{p} <{_kind(s)}>" if p in required else f"[--{p} <{_kind(s)}>]" for p, s in params.items()]
    return " ".join([tool["name"], *flags])


def listing(tools: list[dict]) -> str:
    lines = [HEADER, ""]
    for t in tools:
        description = " ".join((t.get("description") or "").split())
        lines.append(f"- `{usage(t)}`: {description}".rstrip(": "))
    return "\n".join(lines) + "\n"
