"""Give agents of a bundle the agentvfs interface: their envs' tools as shell commands, in place of MCP tools.

In each task, every agent wrapped (by default the one its prompt_agent steps prompt) gets its own agentvfs env in front
of its envs: a deploy_env that waits for those envs, for every tool lock on the agent's role and for whatever the
agent's own deploy waits for. The agent's ``env_ids`` become that env and, where the agent it runs as can have them
turned off, its built-in tools go off. After the steps that drive the agent (its prompts, or the verifiers it judges
for), or at the end of the run for an agent no step drives, a run_code step copies the env's log of the agent's shell
lines and tool calls into the run's record. Every other step is kept as it is.

With one agent wrapped, its env is ``agentvfs`` and its log ``script_results.agentvfs_calls``; with several, each
agent's env is ``agentvfs-<agent>`` and its log ``script_results.agentvfs_calls_<agent>``.
"""
from __future__ import annotations

import json
import re
import shutil
from importlib.metadata import version
from importlib.resources import files
from pathlib import Path
from typing import Optional, Sequence

ENV_NAME = "agentvfs"
CALLS_ARTIFACT = "agentvfs-calls"
# The agent-env agents whose agent-config takes builtin_tools (antigravity since agentenv-framework 0.9.1650;
# tests/test_wrap.py checks it against the installed framework): deploy_agent refuses builtin_tools for any other.
TAKES_BUILTIN_TOOLS = frozenset({"antigravity", "claude-code", "codex", "opencode"})
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


def add_env(bundle: Path, build: bool = False, name: str = ENV_NAME) -> Path:
    """Write envs/<name> (the server's build context and env.toml) into ``bundle`` and require the plugin."""
    env = bundle / "envs" / name
    env.mkdir(parents=True, exist_ok=True)
    source = files("agentenv_vfs") / "server"
    for f in SERVER_FILES:
        (env / f).write_bytes((source / f).read_bytes())
    (env / "env.toml").write_text(env_toml(build))
    calls = bundle / "artifacts" / CALLS_ARTIFACT
    calls.mkdir(parents=True, exist_ok=True)
    for f in ("artifact.toml", "calls.py"):
        (calls / f).write_bytes((files("agentenv_vfs") / "calls" / f).read_bytes())
    toml = bundle / "bundle.toml"
    text = toml.read_text() if toml.exists() else ""
    if "agentenv-vfs" not in text:
        if "requires" in text:
            text = text.replace("requires = [", 'requires = ["agentenv-vfs", ', 1).replace(', ]', ']')
        else:
            text = 'requires = ["agentenv-vfs"]\n' + text
        toml.write_text(text)
    return env


def runs_as(deploy: dict, for_agent: Optional[str] = None) -> str:
    """The agent a deploy_agent step runs: ``for_agent`` (what `agent-env run --agent` would name), else the step's
    a2a_agent_id, else agent-env's default; by its alias, the last part of its id (claude-code)."""
    agent_id = for_agent or deploy.get("a2a_agent_id")
    if not agent_id:
        from agent_env.config import get_config
        agent_id = get_config().get_default_a2a_agent_id()
    return agent_id.rstrip("/").rsplit("/", 1)[-1]


def wrap_steps(steps: list[dict], agents: Optional[Sequence[Optional[str]]] = None, keep_builtins: bool = False,
               note: bool = True, call_log: bool = True, for_agent: Optional[str] = None) -> list[dict]:
    """Edit one task's steps in place, putting agentvfs in front of each of ``agents`` (by agent_name; default: the
    agent its prompt_agent steps prompt); return what changed, one entry per agent."""
    if not agents:
        prompted = {s.get("agent_name") for s in steps if s.get("type") == "prompt_agent"}
        if len(prompted) != 1:
            raise WrapError(f"prompt_agent steps prompt {sorted(map(str, prompted))}; name the agents to wrap (--agent)")
        agents = [prompted.pop()]  # None: the run's default agent, which its steps leave unnamed
    if len(set(agents)) != len(agents):
        raise WrapError(f"an agent is named twice: {list(agents)}")
    several = len(agents) > 1
    changes = [_wrap_agent(steps, agent, several, keep_builtins, note, call_log, for_agent) for agent in agents]
    steps[:] = _ordered(steps)
    return changes


def wrap_bundle(src: Path, out: Path, agents: Optional[Sequence[str]] = None, keep_builtins: bool = False,
                note: bool = True, call_log: bool = True, build: bool = False,
                for_agent: Optional[str] = None) -> list[dict]:
    """Copy ``src`` to ``out`` with the named agents (default: the one each task prompts) working through agentvfs."""
    if out.exists():
        raise WrapError(f"{out} exists")
    tasks = sorted((src / "tasks").glob("*.json"))
    if not tasks:
        raise WrapError(f"{src} has no tasks/*.json")
    shutil.copytree(src, out)
    changes = []
    for path in sorted((out / "tasks").glob("*.json")):
        doc = json.loads(path.read_text())
        steps = doc if isinstance(doc, list) else doc["steps"]
        changes += [{"task": path.name, **c} for c in wrap_steps(steps, agents, keep_builtins, note, call_log, for_agent)]
        path.write_text(json.dumps(doc, indent=2) + "\n")
    for name in sorted({c["vfs_env"] for c in changes}):
        add_env(out, build, name)
    return changes


def _wrap_agent(steps: list[dict], agent: Optional[str], several: bool, keep_builtins: bool, note: bool,
                call_log: bool, for_agent: Optional[str]) -> dict:
    """Put an agentvfs env in front of one agent's envs; return what changed."""
    who = f"'{agent}'" if agent else "the default agent"
    deploys = [s for s in steps if s.get("type") == "deploy_agent" and s.get("agent_name") == agent]
    if len(deploys) != 1:
        raise WrapError(f"{len(deploys)} deploy_agent steps deploy {who}")
    deploy = deploys[0]
    env_ids = deploy.get("env_ids") or []
    if not env_ids:
        raise WrapError(f"{who} has no env_ids")
    if any(e == ENV_NAME or e.startswith(f"{ENV_NAME}-") for e in env_ids):
        raise WrapError(f"{who} already works through agentvfs")
    if deploy.get("env_step_id"):
        raise WrapError(f"{who} picks its env's deployment by env_step_id, which agentvfs can't front")
    env = f"{ENV_NAME}-{agent or 'default'}" if several else ENV_NAME
    if any(s.get("type") == "deploy_env" and s.get("env_id") == env for s in steps):
        raise WrapError(f"the task already deploys env '{env}'")
    env_deploys = []
    for target in env_ids:
        found = [s for s in steps if s.get("type") == "deploy_env" and s.get("env_id") == target]
        if len(found) != 1:
            raise WrapError(f"{len(found)} deploy_env steps deploy '{target}'")
        env_deploys.append(found[0])
    role = deploy.get("role")
    # Its tool list is read as the agent's role when the agent itself would deploy, once the role's tool locks that come
    # before the agent's work are in. A lock applied later (a colleague frozen after the prompt) the env enforces.
    locks = _locks_before_work(steps, deploy, [s for s in steps if _drives(s, agent)], env_ids, role)
    waits = list(dict.fromkeys([s["id"] for s in env_deploys] + locks + [_dep_id(d) for d in deploy.get("depends_on") or []]))
    front = {"id": f"{deploy['id']}-agentvfs", "type": "deploy_env", "version": None, "fail_task_on_error": True,
             "depends_on": [{"task_step_id": i} for i in waits],
             "env_id": env, "env_version": None,
             "ttl_seconds": max(s.get("ttl_seconds") or 10800 for s in env_deploys), "cpu": 1.0, "memory_mb": 2048,
             "metadata": {},
             "env_params": {"env": env_ids[0] if len(env_ids) == 1 else list(env_ids), **({"role": role} if role else {})}}
    steps.insert(steps.index(env_deploys[-1]) + 1, front)
    deploy["env_ids"] = [env]
    deploy["depends_on"] = [*(deploy.get("depends_on") or []), _dep(deploy, front["id"])]
    harness = runs_as(deploy, for_agent)
    if harness not in TAKES_BUILTIN_TOOLS:
        # deploy_agent refuses builtin_tools for it, the task's own selection too: the agent keeps its own tools.
        deploy.pop("builtin_tools", None)
        builtins = f"kept: {harness} can't have them turned off"
    elif keep_builtins:
        builtins = "kept (--keep-builtins)"
    else:
        deploy["builtin_tools"] = {"disable": "*"}
        builtins = "off"
    drivers = [s for s in steps if _drives(s, agent)]
    noted = _add_note(deploy, drivers) if note else None
    result_id = None
    if call_log:
        result_id = "agentvfs_calls" + (f"_{re.sub(r'[^A-Za-z0-9]', '_', agent or 'default')}" if several else "")
        after = drivers or [s for s in steps if s["id"] in _leaves(steps)]  # no step drives it: at the end of the run
        if not after:
            raise WrapError(f"no step for {who}'s call log to follow")
        steps.append({"id": f"{deploy['id']}-agentvfs-calls", "type": "run_code", "version": None,
                      "fail_task_on_error": False, "depends_on": [_dep(deploy, s["id"]) for s in after],
                      "script_artifact_id": CALLS_ARTIFACT, "script_artifact_version": None, "script_file": "calls.py",
                      "entrypoint": "run", "args": {}, "result_id": result_id, "timeout_seconds": 300,
                      "env_id": env, "agent_name": None, "sandbox_name": None})
    return {"agent": agent, "runs_as": harness, "env": env_ids[0] if len(env_ids) == 1 else env_ids, "vfs_env": env,
            "role": role, "builtin_tools": builtins, "waits_for_locks": locks, "note": noted, "call_log": result_id}


def _drives(step: dict, agent: Optional[str]) -> bool:
    """Whether ``step`` sends ``agent`` work: a prompt to it, or a verifier it judges for."""
    if step.get("type") == "prompt_agent":
        return step.get("agent_name") == agent
    return agent is not None and step.get("agent_name") == agent and "verifier" in str(step.get("type"))


def _locks_before_work(steps: list[dict], deploy: dict, drivers: list[dict], env_ids: list[str],
                       role: Optional[str]) -> list[str]:
    """The tool locks on ``role`` in ``env_ids`` in place when the agent starts work: never one that waits for the
    agent's deploy; one that waits for a prompt only if the agent's deploy or work waits for it."""
    parents = {s["id"]: {_dep_id(d) for d in s.get("depends_on") or []} for s in steps}
    children: dict[str, set] = {}
    for sid, ps in parents.items():
        for p in ps:
            children.setdefault(p, set()).add(sid)
    after_deploy = _reach(children, [deploy["id"]])
    after_prompts = _reach(children, [s["id"] for s in steps if s.get("type") == "prompt_agent"])
    before_work = _reach(parents, [deploy["id"], *(s["id"] for s in drivers)])
    return [s["id"] for s in steps if s.get("type") == "modify_env_tool_access" and s.get("env_id") in env_ids
            and s.get("role") in (role, "*") and s["id"] not in after_deploy
            and (s["id"] in before_work or s["id"] not in after_prompts)]


def _reach(edges: dict[str, set], start: list[str]) -> set[str]:
    """The steps reachable from ``start`` along ``edges``, ``start`` left out."""
    seen: set[str] = set()
    todo = [e for s in start for e in edges.get(s, ())]
    while todo:
        step = todo.pop()
        if step not in seen:
            seen.add(step)
            todo += edges.get(step, ())
    return seen


def _add_note(deploy: dict, drivers: list[dict]) -> Optional[str]:
    """Append NOTE where the agent reads its instructions; where it went, or None if nowhere fits."""
    append = lambda text: f"{(text or '').rstrip()}\n\n{NOTE}".lstrip()
    prompts = [s for s in drivers if s.get("type") == "prompt_agent"]
    policies = [s for s in drivers if s.get("type") != "prompt_agent" and s.get("grading_policy_prompt")]
    if prompts:
        for s in prompts:
            s["system_prompt"] = append(s.get("system_prompt"))
        return f"system_prompt of {len(prompts)} prompt(s)"
    if policies:
        # A judge's grading policy, not its system prompt: a configured system prompt replaces the agent's own.
        for s in policies:
            s["grading_policy_prompt"] = append(s["grading_policy_prompt"])
        return f"grading_policy_prompt of {len(policies)} verifier(s)"
    if deploy.get("system_prompt"):
        deploy["system_prompt"] = append(deploy["system_prompt"])
        return "its deploy_agent system_prompt"
    return None


def _leaves(steps: list[dict]) -> set[str]:
    """The steps no other step depends on, call-log steps (and what they depend on) left out."""
    task = [s for s in steps if not s["id"].endswith("-agentvfs-calls")]
    needed = {_dep_id(d) for s in task for d in s.get("depends_on") or []}
    return {s["id"] for s in task if s["id"] not in needed}


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
