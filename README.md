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
uv tool install --python 3.12 'agentenv-framework>=0.9.1637' \
  --with 'agentenv-vfs @ git+https://github.com/earakely-scale/agentenv-vfs'
agent-env vfs --help
```

You need Docker, as for any local agent-env run.

## Use it on a bundle

```bash
agent-env vfs wrap my-bundle --out my-bundle-vfs
agent-env run my-bundle-vfs
```

`wrap` copies the bundle and, in each task, puts an `agentvfs` env in front of the agent's env:

- a `deploy_env` of `agentvfs` names the env it fronts (`env_params.env`) and the agent's role, and waits for that env
  and for every tool lock on the role;
- the agent's `env_ids` become `["agentvfs"]`, and its built-in tools go off (`builtin_tools: {"disable": "*"}`);
- the agent's system prompt gains one paragraph on working through commands (`--no-note` leaves it as it is).

Every other step (other agents, judges, verifiers) is kept as it is. The agent to wrap is the one the tasks prompt;
name another with `--agent`, and keep its built-in tools with `--keep-builtins`.

To wire tasks yourself, add the env with `agent-env vfs add-env my-bundle` and deploy it after the env it fronts:

```json
{"id": "vfs", "type": "deploy_env", "env_id": "agentvfs", "env_params": {"env": "notes", "role": "alice"},
 "depends_on": ["env"]},
{"id": "agent", "type": "deploy_agent", "env_ids": ["agentvfs"], "builtin_tools": {"disable": "*"},
 "depends_on": ["vfs"]}
```

[`examples/notes`](examples/notes) is a small bundle to try it on: a notes env, a task, and a check that reads the
notes back.

## How it works

- **One tool.** The agent's only tool is `shell`, a bash line in its role's workspace. `/SKILL.md` lists the commands,
  `<command> --help` shows one, and `/workspace` and `/tmp` are its own. `curl` and `wget` are refused: the env is
  reached through its commands.
- **Commands are the env's tools.** Each tool is a command named as the tool is, each parameter a flag of the same name
  (`--channel_id`, or `--channel-id`); a list or an object is one JSON value. The `shell` tool's description lists every
  command for the role named at deploy, so the agent sees them up front, as it would see MCP tools.
- **Calls go to the env as the agent.** A command's call goes to the fronted env's MCP endpoint with the agent's
  `AgentEnv-Role`, which the agent sends with every call. So the env sees the calls an MCP agent of that role would
  make: its tool locks, triggers and logs all apply, and each role gets its own workspace.
- **Where it runs.** The env is an MCP server env whose image (Python, Node 24 and Mirage's npm packages) the bundle
  builds from `envs/agentvfs`. It reaches the fronted env at the address agent-env gives for its sandbox.

## Not covered yet

- An agent with more than one env (`wrap` fronts one).
- Python in the workspace (Mirage can run it in Pyodide); the shell has coreutils, `jq` and the env's commands.
- Mounting an env's data as files: reads are commands, as writes are.
- Waiting for the world: a shell-only agent waits with `sleep`.
- Runs on remote sandbox providers have not been tried.

## Development

```bash
uv venv && uv pip install -e . 'agentenv-framework>=0.9.1637' pytest
.venv/bin/python -m pytest tests            # unit tests
PATH=.venv/bin:$PATH tests/integration/run.sh  # the server image against a plain MCP server (Docker)
```

## License

Apache-2.0. Parts are adapted from agentvfs/vfs-bench (MIT); see [NOTICE](NOTICE).
