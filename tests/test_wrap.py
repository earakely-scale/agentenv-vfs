import json

import pytest

from agentenv_vfs.wrap import ENV_NAME, NOTE, WrapError, wrap_bundle, wrap_steps


def short_form():
    return [
        {"id": "env", "type": "deploy_env", "env_id": "notes"},
        {"id": "agent", "type": "deploy_agent", "env_ids": ["notes"], "depends_on": ["env"]},
        {"id": "solve", "type": "prompt_agent", "prompt_id": "solve", "prompt": "hi", "depends_on": ["agent"]},
    ]


def with_role_and_locks():
    dep = lambda *ids: [{"task_step_id": i} for i in ids]
    return [
        {"id": "deploy-env", "type": "deploy_env", "env_id": "world", "ttl_seconds": 600, "depends_on": []},
        {"id": "deploy-solver", "type": "deploy_agent", "agent_name": "solver", "env_ids": ["world"],
         "role": "pat@example.com", "builtin_tools": {"disable": ["WebFetch"]}, "depends_on": dep("deploy-env")},
        {"id": "deploy-colleague", "type": "deploy_agent", "agent_name": "colleague", "env_ids": ["world"],
         "role": "alex@example.com", "depends_on": dep("deploy-env")},
        {"id": "reset", "type": "modify_env_tool_access", "env_id": "world", "role": "*", "action": "enable",
         "tools": ["*"], "depends_on": dep("deploy-env")},
        {"id": "solver-lock", "type": "modify_env_tool_access", "env_id": "world", "role": "pat@example.com",
         "action": "disable", "tools": ["delete_all"], "depends_on": dep("reset")},
        {"id": "colleague-lock", "type": "modify_env_tool_access", "env_id": "world", "role": "alex@example.com",
         "action": "disable", "tools": ["*"], "depends_on": dep("reset")},
        {"id": "prompt", "type": "prompt_agent", "agent_name": "solver", "system_prompt": "Use send_message.",
         "prompt": "go", "depends_on": dep("deploy-solver", "solver-lock")},
    ]


def ids(steps):
    return [s["id"] for s in steps]


def deps(step):
    return [d if isinstance(d, str) else d["task_step_id"] for d in step.get("depends_on") or []]


def test_short_form_default_agent():
    steps = short_form()
    change = wrap_steps(steps)
    assert change["agent"] is None and change["env"] == "notes"
    front = next(s for s in steps if s.get("env_id") == ENV_NAME)
    assert front["env_params"] == {"env": "notes"}
    agent = next(s for s in steps if s["id"] == "agent")
    assert agent["env_ids"] == [ENV_NAME] and agent["builtin_tools"] == {"disable": "*"}
    assert deps(agent) == ["env", front["id"]] and isinstance(agent["depends_on"][-1], str)
    assert next(s for s in steps if s["id"] == "solve")["system_prompt"] == NOTE


def test_role_locks_and_order():
    steps = with_role_and_locks()
    change = wrap_steps(steps)
    assert change["role"] == "pat@example.com" and change["waits_for_locks"] == ["reset", "solver-lock"]
    front = next(s for s in steps if s.get("env_id") == ENV_NAME)
    assert front["env_params"] == {"env": "world", "role": "pat@example.com"}
    assert deps(front) == ["deploy-env", "reset", "solver-lock"] and front["ttl_seconds"] == 600
    position = {i: n for n, i in enumerate(ids(steps))}
    for s in steps:  # every step after the steps it depends on
        assert all(position[d] < position[s["id"]] for d in deps(s))
    colleague = next(s for s in steps if s["id"] == "deploy-colleague")
    assert colleague["env_ids"] == ["world"]  # only the prompted agent changes
    assert next(s for s in steps if s["id"] == "prompt")["system_prompt"].startswith("Use send_message.\n\n")


def test_options_and_refusals():
    steps = with_role_and_locks()
    wrap_steps(steps, agent="solver", keep_builtins=True, note=False)
    solver = next(s for s in steps if s["id"] == "deploy-solver")
    assert solver["builtin_tools"] == {"disable": ["WebFetch"]}
    assert next(s for s in steps if s["id"] == "prompt")["system_prompt"] == "Use send_message."
    with pytest.raises(WrapError, match="already works through agentvfs"):
        wrap_steps(steps, agent="solver")
    two = short_form() + [{"id": "p2", "type": "prompt_agent", "agent_name": "other", "prompt": "x"}]
    with pytest.raises(WrapError, match="name the agent"):
        wrap_steps(two)


def test_wrap_bundle(tmp_path):
    src = tmp_path / "b"
    (src / "tasks").mkdir(parents=True)
    (src / "tasks" / "t.json").write_text(json.dumps(short_form()))
    (src / "bundle.toml").write_text('requires = ["other"]\n')
    changes = wrap_bundle(src, tmp_path / "out")
    assert changes[0]["task"] == "t.json"
    out = tmp_path / "out"
    assert 'requires = ["agentenv-vfs", "other"]' in (out / "bundle.toml").read_text()
    env = out / "envs" / ENV_NAME
    assert 'type = "agentvfs"' in (env / "env.toml").read_text()
    assert {"Dockerfile", "server.py", "listing.py", "preload.ts", "cli.ts", "package-lock.json"} <= {
        p.name for p in env.iterdir()}
    with pytest.raises(WrapError, match="exists"):
        wrap_bundle(src, out)
