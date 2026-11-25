"""Give a bundle's agent the agentvfs interface: its env's tools as shell commands, in place of MCP tools.

For each task, the agent (the one its prompt_agent steps prompt, or ``agent``) gets an agentvfs env in front of its
one env: a deploy_env of ``agentvfs`` that waits for the env and for every tool lock on the agent's role, then the
agent's ``env_ids`` become that env and its built-in tools go off. After the agent's prompts, a run_code step copies
the env's log of the agent's shell lines and tool calls into the run's record (``script_results.agentvfs_calls``).
Every other step is kept as it is.
"""
from __future__ import annotations

import json
import shutil
from importlib.metadata import version
from importlib.resources import files
from pathlib import Path
from typing import Optional

ENV_NAME = "agentvfs"
CALLS_ARTIFACT = "agentvfs-calls"
IMAGE = "ghcr.io/earakely-scale/agentenv-vfs"  # the published server image, tagged with the plugin's version


def env_toml(build: bool = False) -> str:
    """The agentvfs env's env.toml: the published image of this version, built from the folder only where it can't be
    pulled; with ``build``, always built from the folder."""
    image = ('{ dockerfile = "Dockerfile" }' if build
             else f'{{ ref = "{IMAGE}:{version("agentenv-vfs")}", dockerfile = "Dockerfile" }}')
    return f'type = "{ENV_NAME}"\nenvironment_name = "agentvfs"\nenv_provider_type = "server"\nimage = {image}\n'

SERVER_FILES = ("Dockerfile", ".dockerignore", "package.json", "package-lock.json", "server.py", "listing.py",
                "preload.ts", "cli.ts")
NOTE = ("Your tools are shell commands in the workspace of your `shell` tool: each tool is a command of the same name, "
        "its parameters flags of the same names. Wherever these instructions name a tool, run it as that command. "
        "/SKILL.md lists every command, and `<command> --help` shows one.")


class WrapError(ValueError):
    pass


def add_env(bundle: Path, build: bool = False) -> Path:
    """Write envs/agentvfs (the server's build context and env.toml) into ``bundle`` and require the plugin."""
    env = bundle / "envs" / ENV_NAME
    env.mkdir(parents=True, exist_ok=True)
    source = files("agentenv_vfs") / "server"
    for name in SERVER_FILES:
        (env / name).write_bytes((source / name).read_bytes())
    (env / "env.toml").write_text(env_toml(build))
    calls = bundle / "artifacts" / CALLS_ARTIFACT
    calls.mkdir(parents=True, exist_ok=True)
    for name in ("artifact.toml", "calls.py"):
        (calls / name).write_bytes((files("agentenv_vfs") / "calls" / name).read_bytes())
    toml = bundle / "bundle.toml"
    text = toml.read_text() if toml.exists() else ""
    if "agentenv-vfs" not in text:
        if "requires" in text:
            text = text.replace("requires = [", 'requires = ["agentenv-vfs", ', 1).replace(', ]', ']')
        else:
            text = 'requires = ["agentenv-vfs"]\n' + text
        toml.write_text(text)
    return env


def wrap_steps(steps: list[dict], agent: Optional[str] = None, keep_builtins: bool = False, note: bool = True,
               call_log: bool = True) -> dict:
    """Edit one task's steps in place; return what changed."""
    prompted = {s.get("agent_name") for s in steps if s.get("type") == "prompt_agent"}
    if agent is None:
        if len(prompted) != 1:
            raise WrapError(f"prompt_agent steps prompt {sorted(map(str, prompted))}; name the agent to wrap (--agent)")
        agent = prompted.pop()  # None: the run's default agent, which its steps leave unnamed
    deploys = [s for s in steps if s.get("type") == "deploy_agent" and s.get("agent_name") == agent]
    if len(deploys) != 1:
        raise WrapError(f"{len(deploys)} deploy_agent steps deploy '{agent or 'the default agent'}'")
    deploy = deploys[0]
    env_ids = deploy.get("env_ids") or []
    if len(env_ids) != 1:
        raise WrapError(f"'{agent or 'the default agent'}' has env_ids {env_ids}; agentvfs fronts one env")
    target = env_ids[0]
    if target == ENV_NAME:
        raise WrapError(f"'{agent or 'the default agent'}' already works through agentvfs")
    env_deploys = [s for s in steps if s.get("type") == "deploy_env" and s.get("env_id") == target]
    if len(env_deploys) != 1:
        raise WrapError(f"{len(env_deploys)} deploy_env steps deploy '{target}'")
    role = deploy.get("role")
    # Its tool list is read as the agent's role once the role's tool locks are in.
    locks = [s["id"] for s in steps if s.get("type") == "modify_env_tool_access" and s.get("env_id") == target
             and s.get("role") in (role, "*")]
    front = {"id": f"{deploy['id']}-agentvfs", "type": "deploy_env", "version": None, "fail_task_on_error": True,
             "depends_on": [{"task_step_id": i} for i in [env_deploys[0]["id"], *locks]], "env_id": ENV_NAME,
             "env_version": None, "ttl_seconds": env_deploys[0].get("ttl_seconds") or 10800, "cpu": 1.0,
             "memory_mb": 2048, "metadata": {},
             "env_params": {"env": target, **({"role": role} if role else {})}}
    steps.insert(steps.index(env_deploys[0]) + 1, front)
    deploy["env_ids"] = [ENV_NAME]
    deploy["depends_on"] = [*(deploy.get("depends_on") or []), _dep(deploy, front["id"])]
    if not keep_builtins:
        deploy["builtin_tools"] = {"disable": "*"}
    prompts = [s for s in steps if s.get("type") == "prompt_agent" and s.get("agent_name") == agent]
    noted = 0
    if note:
        for s in prompts:
            s["system_prompt"] = f"{(s.get('system_prompt') or '').rstrip()}\n\n{NOTE}".lstrip()
            noted += 1
    if call_log and prompts:
        steps.append({"id": f"{deploy['id']}-agentvfs-calls", "type": "run_code", "version": None,
                      "fail_task_on_error": False, "depends_on": [_dep(prompts[-1], s["id"]) for s in prompts],
                      "script_artifact_id": CALLS_ARTIFACT, "script_artifact_version": None, "script_file": "calls.py",
                      "entrypoint": "run", "args": {}, "result_id": "agentvfs_calls", "timeout_seconds": 300,
                      "env_id": ENV_NAME, "agent_name": None, "sandbox_name": None})
    steps[:] = _ordered(steps)
    return {"agent": agent, "env": target, "role": role, "waits_for_locks": locks, "prompts_noted": noted}


def _dep_id(dep) -> str | None:
    """A depends_on entry's step id: the entry itself, or its task_step_id."""
    return dep if isinstance(dep, str) else dep.get("task_step_id")


def _dep(step: dict, step_id: str):
    """A depends_on entry for ``step_id`` in the form ``step`` already uses."""
    return step_id if any(isinstance(d, str) for d in step.get("depends_on") or []) else {"task_step_id": step_id}


def _ordered(steps: list[dict]) -> list[dict]:
    """``steps`` with each after the steps it depends on (agent-env refuses a forward reference), else in order."""
    index = {s["id"]: i for i, s in enumerate(steps)}
    deps = {s["id"]: {i for i in map(_dep_id, s.get("depends_on") or []) if i in index} for s in steps}
    done: set[str] = set()
    out: list[dict] = []
    while len(out) < len(steps):
        ready = next((s for s in steps if s["id"] not in done and deps[s["id"]] <= done), None)
        if ready is None:
            raise WrapError("the steps' depends_on form a cycle")
        out.append(ready)
        done.add(ready["id"])
    return out


def wrap_bundle(src: Path, out: Path, agent: Optional[str] = None, keep_builtins: bool = False,
                note: bool = True, call_log: bool = True, build: bool = False) -> list[dict]:
    """Copy ``src`` to ``out`` with every task's agent working through agentvfs."""
    if out.exists():
        raise WrapError(f"{out} exists")
    shutil.copytree(src, out)
    add_env(out, build)
    changes = []
    for path in sorted((out / "tasks").glob("*.json")):
        doc = json.loads(path.read_text())
        steps = doc if isinstance(doc, list) else doc["steps"]
        changes.append({"task": path.name, **wrap_steps(steps, agent, keep_builtins, note, call_log)})
        path.write_text(json.dumps(doc, indent=2) + "\n")
    if not changes:
        raise WrapError(f"{src} has no tasks/*.json")
    return changes
