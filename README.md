# agentenv-vfs

[![tests](https://github.com/earakely-scale/agentenv-vfs/actions/workflows/test.yml/badge.svg)](https://github.com/earakely-scale/agentenv-vfs/actions/workflows/test.yml)

An [agent-env](https://pypi.org/project/agentenv-framework/) plugin that gives an agent any env's tools through
[agentvfs](https://github.com/agentvfs/vfs-bench): a shell in a [Mirage](https://github.com/strukto-ai/mirage)
workspace, where each of the env's MCP tools is a command of the same name, instead of MCP tool calls.

```
$ add_note --title "Budget sync" --body "Q3 budget is flat" --tags finance
{"id": 2, "title": "Budget sync", ...}
$ list_notes | jq -r '.[] | select(.tags | index("finance")) | .title'
Budget sync
```

It works with any env that serves MCP tools, a plain MCP server or a gateway env, and needs no change to the env, its
tasks' other steps or agent-env itself.

## Install

```bash
uv tool install --python 3.12 agentenv-framework --with agentenv-vfs
agent-env vfs --help
```

Or `pip install agentenv-vfs` into an environment of your own, which brings agent-env with it. Pin a release with
`agentenv-vfs==<version>`: `wrap` points bundles at the server image of the same version.

You need Docker, as for any local agent-env run.

## Use it on a bundle

```bash
agent-env vfs wrap my-bundle --out my-bundle-vfs
agent-env run my-bundle-vfs
```

`wrap` copies the bundle and, in each task, puts an `agentvfs` env in front of the agent's envs:

- a `deploy_env` of `agentvfs` names the envs it fronts (`env_params.env`, one id or a list) and the agent's role, and
  waits for those envs, for every tool lock on the role, and for whatever the agent's own deploy waits for;
- the agent's `env_ids` become `["agentvfs"]`, and its built-in tools go off (`builtin_tools: {"disable": "*"}`);
- the agent's instructions gain one paragraph on working through commands (`--no-note` leaves them as they are).

After the steps that send the agent work, a step copies the agentvfs env's log of the agent's shell lines and the tool
calls each made into the run's record, as `script_results.agentvfs_calls` (`--no-call-log` leaves it out). Every other
step (other agents, judges, verifiers) is kept as it is. Keep the agent's built-in tools with `--keep-builtins` (see
[Agents](#agents) for which agents can have them turned off).

### Choosing the agents

The agent to wrap is the one the tasks prompt. Name others with `--agent`, by their `agent_name`, once per agent:

```bash
agent-env vfs wrap my-bundle --out my-bundle-vfs --agent judge --agent judge-b   # only the judges work through agentvfs
```

Any agent a `deploy_agent` deploys can be wrapped, whatever sends it work:

| The agent | Its paragraph goes in | Its log is copied |
|---|---|---|
| prompted by `prompt_agent` steps | their `system_prompt` | after its prompts |
| a judge for verifier steps | their `grading_policy_prompt` (a configured system prompt would replace the agent's own) | after those verifiers |
| sent work some other way, such as by an env's triggers | its `deploy_agent` `system_prompt`, if it has one | at the end of the run |

Each wrapped agent gets its own agentvfs env, so two agents of one role (two judges, say) don't share a workspace.
With one agent wrapped its env is `agentvfs` and its log `agentvfs_calls`; with several, each agent's env is
`agentvfs-<agent>` and its log `agentvfs_calls_<agent>`.

To wire tasks yourself, add the env with `agent-env vfs add-env my-bundle` and deploy it after the env it fronts:

```json
{"id": "vfs", "type": "deploy_env", "env_id": "agentvfs", "env_params": {"env": "notes", "role": "alice"},
 "depends_on": ["env"]},
{"id": "agent", "type": "deploy_agent", "env_ids": ["agentvfs"], "builtin_tools": {"disable": "*"},
 "depends_on": ["vfs"]}
```

[`examples/notes`](examples/notes) is a small bundle to try it on: a notes env, a task, and a check that reads the
notes back. [`examples/notes-and-contacts`](examples/notes-and-contacts) gives one agent two envs, which `wrap` fronts
from one workspace.

## Agents

It works with any agent-env agent: agentvfs is an env, and the agent reaches it over MCP as it reaches any env. Pass
`wrap --for-agent NAME` when you run with `agent-env run --agent NAME`, so the bundle fits that agent:

| Agent | Built-in tools | Checked with |
|---|---|---|
| claude-code (the default), codex, opencode, antigravity (agent-env 0.9.1650+) | turned off: the shell is the agent's only tool | the notes example; claude-code and codex on a gateway env's tasks too |
| gemini, hermes, letta-code, openhands, pi | kept: agent-env can't turn them off | the notes example; gemini on a gateway env's task too |
| pydantic-ai | kept | not yet: its own coding tools include one named `shell`, which agent-env adds MCP tools beside without a prefix |

## How it works

- **One tool.** The agent's only tool is `shell`, a bash line in its role's workspace. `/SKILL.md` lists the commands,
  `<command> --help` shows one, and `/workspace` and `/tmp` are its own. `python3` is CPython in Pyodide, inside the
  workspace: its standard library, the workspace's files and commands, no network. `curl` and `wget` are refused: the
  env is reached through its commands.
- **Commands are the envs' tools.** One workspace serves the tools of every env it fronts, each call going to the env
  that serves its tool; two envs that serve a tool of the same name are refused. Each tool is a command named as the
  tool is, each parameter a flag of the same name
  (`--channel_id`, or `--channel-id`); a list or an object is one JSON value. The `shell` tool's description lists every
  command for the role named at deploy, so the agent sees them up front, as it would see MCP tools.
- **Calls go to the env as the agent.** A command's call goes to the fronted env's MCP endpoint with the agent's
  `AgentEnv-Role`, which the agent sends with every call. So the env sees the calls an MCP agent of that role would
  make: its tool locks, triggers and logs all apply, and each role gets its own workspace.
- **What a run keeps.** The env logs every shell line and every tool call a line makes (role, tool, arguments,
  outcome, timing), in order. `data/get` of `calls.jsonl` returns it, and the step `wrap` adds copies it into the run.
- **Where it runs.** The env is an MCP server env. Its image (Python, Node 24, Mirage's npm packages and Pyodide) is
  published at `ghcr.io/earakely-scale/agentenv-vfs`, tagged with the plugin's version, for linux/amd64 and
  linux/arm64. A wrapped bundle pulls it, and keeps its build context in `envs/agentvfs`, which a deploy builds only
  where it can't pull the image (`--build` always builds it). The env reaches the fronted env at the address
  agent-env gives for its sandbox. Checked locally (Docker) and on Modal (`agent-env run --sandbox modal`), where the
  image is pulled and the fronted envs are reached over their Modal tunnels.

## Not covered yet

- Python packages beyond the standard library (Pyodide fetches them from the network, which the workspace refuses).
- Mounting an env's data as files: reads are commands, as writes are.
- Waiting for the world: a shell-only agent waits with `sleep`.
- Remote sandbox providers other than Modal have not been tried.

## Development

```bash
uv venv && uv pip install -e . 'agentenv-framework>=0.9.1637' pytest
.venv/bin/python -m pytest tests            # unit tests
PATH=.venv/bin:$PATH tests/integration/run.sh  # the server image against a plain MCP server (Docker)
```

## Releasing

Set `version` in `pyproject.toml`, push to `main`, then tag `v<version>` and push the tag. The `image` workflow
publishes `ghcr.io/earakely-scale/agentenv-vfs:<version>`, which `wrap` of that version points bundles at, and the
`publish` workflow publishes the package to PyPI through trusted publishing (no token).

## License

Apache-2.0. Parts are adapted from agentvfs/vfs-bench (MIT); see [NOTICE](NOTICE).
