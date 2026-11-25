"""The agentvfs env: a Mirage shell workspace whose commands are another env's MCP tools.

The agent's one tool is ``shell``. In its workspace each tool of the fronted env is a command of the same name, its
parameters flags of the same names, and a command's call goes to the fronted env's MCP endpoint as the role of the agent
that ran it, so the fronted env sees the same tool calls an MCP agent of that role would make.

``data/add`` (a DataPart) names the fronted envs, ``{"envs": [{"name", "mcp_url"}, ...], "role"?, "headers"?}`` (or one,
``{"mcp_url", ...}``). The workspace serves the tools of all of them, each call going to the env that serves its tool,
and refuses two envs that serve a tool of the same name. With a ``role``, that role's workspace opens now and the
``shell`` tool's description lists its commands; any other role's opens at its first call.

Every shell line, and every tool call a line makes, is kept in order: ``data/get`` of ``calls.jsonl`` returns them.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import socket
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import httpx
from agentenv_protocol import (DATA_OBJECTS_EXTENSION_URI, AgentEnvEnvironment, DataPart, FilePart, add_data,
                               environment_card, get_data, reset_data, tool)
from agentenv_protocol.tool_context import ToolContext
from agentenv_protocol.types import FileWithBytes
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from mcp.types import CallToolResult, TextContent

from listing import listing

HOME = Path(os.environ.get("AGENTENV_VFS_HOME", "/opt/agentenv-vfs"))
DAEMON = HOME / "node_modules/@struktoai/mirage-server/dist/bin/daemon.js"
RUNS = Path(os.environ.get("AGENTENV_VFS_RUNS", "/var/lib/agentenv-vfs"))
NODE = os.environ.get("AGENTENV_VFS_NODE", "node")
ROLE_HEADER = "AgentEnv-Role"
DEFAULT_ROLE = "default"
CALLS = "calls.jsonl"
CLIP = int(os.environ.get("AGENTENV_VFS_LOG_CLIP", "2000"))  # longest string a log entry keeps
SHELL_DOC = ("Run a bash command line in the workspace, from its working directory. Pipes, redirects and Unix commands "
             "work; /SKILL.md lists the commands and /VFS.md the mounts.")


class RoleSession:
    """One role's MCP client session, held open by a task of its own: the client's streams belong to the task that
    opened them, so no request's task may open them."""

    def __init__(self, mcp_url: str, headers: dict[str, str]) -> None:
        self.ready: asyncio.Future[ClientSession] = asyncio.get_running_loop().create_future()
        self.stop = asyncio.Event()
        self.task = asyncio.create_task(self._hold(mcp_url, headers))

    async def _hold(self, mcp_url: str, headers: dict[str, str]) -> None:
        try:
            async with streamablehttp_client(mcp_url, headers=headers) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    self.ready.set_result(session)
                    await self.stop.wait()
        except Exception as e:
            if not self.ready.done():
                self.ready.set_exception(e)

    async def close(self) -> None:
        self.stop.set()
        await asyncio.gather(self.task, return_exceptions=True)


class Fronted:
    """A fronted env's MCP endpoint, one client session per role."""

    def __init__(self, name: str, mcp_url: str, headers: dict[str, str] | None = None) -> None:
        self.name = name
        self.mcp_url = mcp_url
        self.headers = headers or {}
        self.sessions: dict[str, RoleSession] = {}

    async def session(self, role: str) -> ClientSession:
        held = self.sessions.get(role)
        if held is None or held.task.done():  # first use, or the session ended: open one
            held = self.sessions[role] = RoleSession(self.mcp_url, {**self.headers, ROLE_HEADER: role})
        return await held.ready

    async def tools(self, role: str) -> list[dict]:
        session, out, cursor = await self.session(role), [], None
        while True:
            page = await session.list_tools(cursor=cursor) if cursor else await session.list_tools()
            out += [{"name": t.name, "description": t.description or "",
                     "parameters": t.inputSchema or {"type": "object", "properties": {}}} for t in page.tools]
            if not (cursor := page.nextCursor):
                return out

    async def call(self, role: str, name: str, arguments: dict) -> dict:
        try:
            result = await (await self.session(role)).call_tool(name, arguments)
        except Exception as e:  # the endpoint failed the call: the command fails with why
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}
        text = "\n".join(c.text if c.type == "text" else f"[{c.type}]" for c in result.content)
        return {"ok": False, "error": text or "the tool failed"} if result.isError else {"ok": True, "result": text}

    async def close(self) -> None:
        sessions, self.sessions = self.sessions, {}
        await asyncio.gather(*(s.close() for s in sessions.values()))


class Fronts:
    """The fronted envs, and for each role which env serves each of its tools."""

    def __init__(self, envs: list[Fronted]) -> None:
        self.envs = envs
        self.routes: dict[str, dict[str, Fronted]] = {}

    async def tools(self, role: str) -> list[dict]:
        tools, route = [], {}
        for env in self.envs:
            for t in await env.tools(role):
                if t["name"] in route:
                    raise RuntimeError(f"envs '{route[t['name']].name}' and '{env.name}' both serve a tool named "
                                       f"'{t['name']}'; a workspace has one command per name")
                route[t["name"]] = env
                tools.append(t)
        self.routes[role] = route
        return tools

    async def call(self, role: str, name: str, arguments: dict) -> tuple[str | None, dict]:
        env = self.routes.get(role, {}).get(name)
        if env is None:
            return None, {"ok": False, "error": f"no fronted env serves '{name}' for role '{role}'"}
        return env.name, await env.call(role, name, arguments)

    async def close(self) -> None:
        await asyncio.gather(*(env.close() for env in self.envs))


class CallLog:
    """Every shell line and every tool call a line made, in order: what a run keeps of the agent's tool use."""

    def __init__(self) -> None:
        self.rows: list[dict] = []
        self.current: dict[str, int] = {}  # each role's running line

    def line(self, role: str, command: str) -> dict:
        row = {"kind": "line", "line": sum(r["kind"] == "line" for r in self.rows) + 1, "at": _now(), "role": role,
               "command": _clip(command)}
        self.rows.append(row)
        self.current[role] = row["line"]
        return row

    def line_done(self, row: dict, done: dict, ms: float) -> None:
        self.current.pop(row["role"], None)
        row.update(exit_code=done.get("exit_code"), ms=round(ms), stdout_chars=len(done.get("stdout") or ""),
                   stderr_chars=len(done.get("stderr") or ""))

    def call(self, role: str, env: str | None, tool: str, arguments: dict, reply: dict, ms: float) -> None:
        row = {"kind": "call", "line": self.current.get(role), "at": _now(), "role": role, "env": env, "tool": tool,
               "arguments": _clip(arguments), "ok": reply.get("ok"), "ms": round(ms),
               "result_chars": len(reply.get("result") or "")}
        if not reply.get("ok"):
            row["error"] = _clip(reply.get("error"))
        self.rows.append(row)

    def jsonl(self) -> bytes:
        return "".join(json.dumps(r) + "\n" for r in self.rows).encode()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _clip(value):
    """``value`` with every string past ``CLIP`` characters cut, saying how much was cut."""
    if isinstance(value, str):
        return value if len(value) <= CLIP else f"{value[:CLIP]}...[+{len(value) - CLIP} chars]"
    if isinstance(value, dict):
        return {k: _clip(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_clip(v) for v in value]
    return value


class Bridge:
    """A loopback HTTP endpoint the workspace's commands post their calls to: ``POST /call {role, tool, arguments}``."""

    def __init__(self, fronts: Fronts, log: CallLog) -> None:
        self.fronts = fronts
        self.log = log
        self.server: asyncio.base_events.Server | None = None
        self.url = ""

    async def start(self) -> None:
        self.server = await asyncio.start_server(self._serve, "127.0.0.1", 0)
        self.url = f"http://127.0.0.1:{self.server.sockets[0].getsockname()[1]}"

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            head = await reader.readuntil(b"\r\n\r\n")
            length = next((int(line.split(b":", 1)[1]) for line in head.split(b"\r\n")
                           if line.lower().startswith(b"content-length:")), 0)
            body = json.loads(await reader.readexactly(length)) if length else {}
            role, args, started = body.get("role") or DEFAULT_ROLE, body.get("arguments") or {}, time.monotonic()
            env, reply = await self.fronts.call(role, body["tool"], args)
            self.log.call(role, env, body["tool"], args, reply, (time.monotonic() - started) * 1000)
        except Exception as e:
            reply = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        data = json.dumps(reply).encode()
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\n"
                     + f"Content-Length: {len(data)}\r\n\r\n".encode() + data)
        await writer.drain()
        writer.close()

    async def stop(self) -> None:
        if self.server is not None:
            self.server.close()


class Workspace:
    """One role's Mirage daemon and workspace, with one command per tool that role is shown."""

    def __init__(self, role: str, tools: list[dict], bridge_url: str, clock: str | None) -> None:
        self.role, self.tools, self.bridge_url, self.clock = role, tools, bridge_url, clock
        self.id = f"agentvfs-{uuid.uuid4().hex[:8]}"
        self.run = RUNS / self.id
        self.process: asyncio.subprocess.Process | None = None
        self.client: httpx.AsyncClient | None = None
        self.running = asyncio.Lock()  # the daemon runs a session's lines one at a time; so does this

    async def open(self) -> None:
        for d in ("workspace", "tmp", "mirage-home"):
            (self.run / d).mkdir(parents=True)
        (self.run / "tools.json").write_text(json.dumps(self.tools))
        self.client = httpx.AsyncClient(base_url=await self._daemon(), timeout=httpx.Timeout(60, read=None))
        reply = await self.client.post("/v1/workspaces", json={"id": self.id, "config": self._config()})
        if reply.status_code >= 300:
            raise RuntimeError(f"creating the workspace failed ({reply.status_code}): {reply.text}")
        for kind in ("vfs", "skill"):
            (await self.client.put(f"/v1/workspaces/{self.id}/{kind}-md", json={"path": f"/{kind.upper()}.md"})
             ).raise_for_status()
        await self.shell("cd /workspace")

    def _config(self) -> dict:
        disk = lambda d: {"vfs": "disk", "mode": "exec", "read": "fresh", "config": {"root": str(self.run / d)}}
        deny = {"decision": "deny", "reason": "the workspace reaches its env through its commands"}
        config = {
            "mode": "exec",
            "mounts": {"/workspace": disk("workspace"), "/tmp": disk("tmp")},
            # python3: CPython in Pyodide (WebAssembly), inside the workspace: its standard library, no network.
            "machines": {"python": {"provider": "in_process", "runtime": "pyodide",
                                    "max_memory": os.environ.get("AGENTENV_VFS_PYTHON_MEMORY", "1G")}},
            "clis": {t["name"]: {"cli": t["name"], "machine": "in_process",
                                 "config": {"url": self.bridge_url, "role": self.role}} for t in self.tools},
            "profiles": {"default": {"commands": {"curl": deny, "wget": deny}}},
            "command_limits": {"@line": {"timeout_seconds": int(os.environ.get("AGENTENV_VFS_LINE_TIMEOUT_S", "600"))}},
        }
        if self.clock:
            config["system_time"] = self.clock
        return config

    async def _daemon(self) -> str:
        port = _free_port()
        self.process = await asyncio.create_subprocess_exec(
            NODE, "--experimental-transform-types", "--no-warnings", "--import", str(HOME / "preload.ts"), str(DAEMON),
            env={**os.environ, "MIRAGE_HOME": str(self.run / "mirage-home"), "MIRAGE_DAEMON_PORT": str(port),
                 "AGENTENV_VFS_TOOLS": str(self.run / "tools.json"), "AGENTENV_VFS_BRIDGE": self.bridge_url},
            stdin=asyncio.subprocess.PIPE, stdout=open(self.run / "daemon.log", "wb"), stderr=asyncio.subprocess.STDOUT)
        url = f"http://127.0.0.1:{port}"
        async with httpx.AsyncClient() as client:
            for _ in range(900):
                if self.process.returncode is not None:
                    raise RuntimeError(f"the Mirage daemon exited:\n{(self.run / 'daemon.log').read_text()[-4000:]}")
                try:
                    if (await client.get(f"{url}/v1/daemon/ready")).status_code == 200:
                        return url
                except httpx.TransportError:
                    pass
                await asyncio.sleep(0.1)
        raise RuntimeError(f"the Mirage daemon did not answer on {url}")

    async def shell(self, command: str) -> dict:
        reply = await self.client.post(f"/v1/workspaces/{self.id}/shell", json={"command": command})
        if reply.status_code >= 300:
            raise RuntimeError(f"the workspace's shell failed ({reply.status_code}): {reply.text}")
        return reply.json()

    async def close(self) -> None:
        if self.client is not None:
            try:
                history = await self.client.get(f"/v1/workspaces/{self.id}/logs", params={"format": "jsonl"})
                if history.status_code == 200:
                    (self.run / "history.jsonl").write_bytes(history.content)
                await self.client.delete(f"/v1/workspaces/{self.id}")
            except httpx.HTTPError:
                pass
            await self.client.aclose()
        if self.process is not None and self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), 15)
            except TimeoutError:
                self.process.kill()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _clock(mcp_url: str) -> str | None:
    """The fronted env's virtual time, where its server keeps one (an agent-env gateway's ``/clock/state``)."""
    parts = urlsplit(mcp_url)
    base = urlunsplit(parts._replace(path=parts.path.rsplit("/mcp", 1)[0], query="", fragment=""))
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            reply = await client.get(f"{base}/clock/state")
            return reply.json().get("virtual_time") if reply.status_code == 200 else None
    except (httpx.HTTPError, ValueError):
        return None


def _config_of(parts: list) -> dict:
    for part in parts:
        if isinstance(part, DataPart) and "envs" in part.data:
            return part.data
        if isinstance(part, DataPart) and "mcp_url" in part.data:
            return {**part.data, "envs": [{"name": "env", "mcp_url": part.data["mcp_url"]}]}
    raise ValueError('data/add needs a DataPart {"envs": [{"name", "mcp_url"}, ...], "role"?, "headers"?}')


@environment_card(name="agentvfs")
class AgentVfs(AgentEnvEnvironment):
    """Another env's MCP tools as commands in a Mirage shell workspace; the agent's one tool is ``shell``."""

    def __init__(self) -> None:
        self.fronts: Fronts | None = None
        self.bridge: Bridge | None = None
        self.clock: str | None = None
        self.workspaces: dict[str, Workspace] = {}
        self.opening = asyncio.Lock()
        self.log = CallLog()

    async def _workspace(self, role: str) -> Workspace:
        async with self.opening:
            if role not in self.workspaces:
                if self.fronts is None:
                    raise RuntimeError("no env is fronted yet (data/add names one)")
                workspace = Workspace(role, await self.fronts.tools(role), self.bridge.url, self.clock)
                try:
                    await workspace.open()
                except BaseException:
                    await workspace.close()
                    raise
                self.workspaces[role] = workspace
            return self.workspaces[role]

    @reset_data
    async def _reset(self) -> None:
        workspaces, self.workspaces = self.workspaces, {}
        for workspace in workspaces.values():
            await workspace.close()
        for part in (self.bridge, self.fronts):
            if part is not None:
                await part.stop() if isinstance(part, Bridge) else await part.close()
        self.bridge = self.fronts = None

    @add_data
    async def _add(self, parts: list) -> None:
        await self._reset()
        config = _config_of(parts)
        self.fronts = Fronts([Fronted(e["name"], e["mcp_url"], config.get("headers")) for e in config["envs"]])
        self.log = CallLog()
        self.bridge = Bridge(self.fronts, self.log)
        await self.bridge.start()
        self.clock = None
        for e in config["envs"]:  # the first fronted env that keeps a virtual time
            if clock := await _clock(e["mcp_url"]):
                self.clock = clock
                break
        if role := config.get("role"):
            workspace = await self._workspace(role)
            await self.update_tool("shell", description=f"{SHELL_DOC}\n\n{listing(workspace.tools)}")

    @get_data
    async def _state(self, paths: list | None = None) -> list:
        if paths:
            return [FilePart(file=FileWithBytes(bytes=base64.b64encode(self.log.jsonl()).decode()),
                             metadata={DATA_OBJECTS_EXTENSION_URI: {"path": CALLS}}) for p in paths if p == CALLS]
        calls = [r for r in self.log.rows if r["kind"] == "call"]
        return [DataPart(data={"fronted": [{"name": e.name, "mcp_url": e.mcp_url} for e in self.fronts.envs]
                               if self.fronts else [],
                               "roles": {r: [t["name"] for t in w.tools] for r, w in self.workspaces.items()},
                               "lines": len(self.log.rows) - len(calls), "calls": len(calls),
                               "failed_calls": sum(not r["ok"] for r in calls)})]

    @tool()
    async def shell(self, command: str) -> CallToolResult:
        """Run a bash command line in the workspace, from its working directory. Pipes, redirects and Unix commands
        work; /SKILL.md lists the commands and /VFS.md the mounts."""
        role = ToolContext.current().role or DEFAULT_ROLE
        workspace = await self._workspace(role)
        async with workspace.running:
            row, started = self.log.line(role, command), time.monotonic()
            done = await workspace.shell(command)
            self.log.line_done(row, done, (time.monotonic() - started) * 1000)
        text = done["stdout"]
        if done["stderr"]:
            text = f"{text}\n{done['stderr']}" if text else done["stderr"]
        return CallToolResult(content=[TextContent(type="text", text=text)], isError=done["exit_code"] != 0)


if __name__ == "__main__":
    AgentVfs().serve()
