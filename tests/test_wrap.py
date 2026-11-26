import json
from pathlib import Path

import agent_env
import pytest

from agentenv_vfs.wrap import ENV_NAME, NOTE, TAKES_BUILTIN_TOOLS, WrapError, wrap_bundle, wrap_steps


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
    [change] = wrap_steps(steps)
    assert change["agent"] is None and change["env"] == "notes"
    front = next(s for s in steps if s.get("env_id") == ENV_NAME)
    assert front["env_params"] == {"env": "notes"}
    agent = next(s for s in steps if s["id"] == "agent")
    assert agent["env_ids"] == [ENV_NAME] and agent["builtin_tools"] == {"disable": "*"}
    assert deps(agent) == ["env", front["id"]] and isinstance(agent["depends_on"][-1], str)
    assert next(s for s in steps if s["id"] == "solve")["system_prompt"] == NOTE


def test_role_locks_and_order():
    steps = with_role_and_locks()
    [change] = wrap_steps(steps)
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


def test_several_envs():
    steps = with_role_and_locks()
    steps.insert(1, {"id": "deploy-crm", "type": "deploy_env", "env_id": "crm", "ttl_seconds": 900, "depends_on": []})
    steps.append({"id": "crm-lock", "type": "modify_env_tool_access", "env_id": "crm", "role": "pat@example.com",
                  "action": "disable", "tools": ["wipe"], "depends_on": [{"task_step_id": "deploy-crm"}]})
    next(s for s in steps if s["id"] == "deploy-solver")["env_ids"] = ["world", "crm"]
    [change] = wrap_steps(steps)
    front = next(s for s in steps if s.get("env_id") == ENV_NAME)
    assert front["env_params"] == {"env": ["world", "crm"], "role": "pat@example.com"} and change["env"] == ["world", "crm"]
    assert set(deps(front)) == {"deploy-env", "deploy-crm", "reset", "solver-lock", "crm-lock"}
    assert front["ttl_seconds"] == 900
    position = {i: n for n, i in enumerate(ids(steps))}
    assert all(position[d] < position[front["id"]] for d in deps(front))


def test_call_log_step():
    steps = with_role_and_locks()
    wrap_steps(steps)
    log = next(s for s in steps if s["id"] == "deploy-solver-agentvfs-calls")
    assert log["type"] == "run_code" and log["env_id"] == ENV_NAME and log["result_id"] == "agentvfs_calls"
    assert deps(log) == ["prompt"] and not log["fail_task_on_error"]
    assert ids(steps).index("prompt") < ids(steps).index(log["id"])
    steps = short_form()
    wrap_steps(steps, call_log=False)
    assert not any(s["id"].endswith("-agentvfs-calls") for s in steps)


def test_builtin_tools_follow_the_agent():
    steps = with_role_and_locks()
    [change] = wrap_steps(steps, for_agent="gemini")
    solver = next(s for s in steps if s["id"] == "deploy-solver")
    assert "builtin_tools" not in solver and change["builtin_tools"].startswith("kept: gemini")
    steps = with_role_and_locks()
    assert wrap_steps(steps, for_agent="@local/agentenv-framework/agents/codex")[0]["builtin_tools"] == "off"
    steps = short_form()
    steps[1]["a2a_agent_id"] = "pydantic-ai"
    assert wrap_steps(steps)[0]["runs_as"] == "pydantic-ai"


def test_agents_that_take_builtin_tools():
    """The table matches the installed framework: an agent takes builtin_tools when its sources handle them. An older
    framework may lack an agent the table names, but every agent it has is in the table exactly when it takes them."""
    agents = Path(agent_env.__file__).parent / "agents" / "agents"
    present = {d.name for d in agents.iterdir() if d.is_dir()}
    taking = {d for d in present if any("builtin_tools" in f.read_text(errors="ignore") for f in (agents / d).rglob("*.py"))}
    assert taking == TAKES_BUILTIN_TOOLS & present


def test_options_and_refusals():
    steps = with_role_and_locks()
    wrap_steps(steps, agents=["solver"], keep_builtins=True, note=False)
    solver = next(s for s in steps if s["id"] == "deploy-solver")
    assert solver["builtin_tools"] == {"disable": ["WebFetch"]}
    assert next(s for s in steps if s["id"] == "prompt")["system_prompt"] == "Use send_message."
    with pytest.raises(WrapError, match="already works through agentvfs"):
        wrap_steps(steps, agents=["solver"])
    two = short_form() + [{"id": "p2", "type": "prompt_agent", "agent_name": "other", "prompt": "x"}]
    with pytest.raises(WrapError, match="name the agents"):
        wrap_steps(two)
    with pytest.raises(WrapError, match="named twice"):
        wrap_steps(with_role_and_locks(), agents=["solver", "solver"])


def judged_task():
    """A task with a prompted solver, a trigger-driven colleague, two judges of one role that verifiers
    drive (judge also runs validity), and a final aggregate."""
    dep = lambda *ids: [{"task_step_id": i} for i in ids]
    return with_role_and_locks()[:-1] + [
        {"id": "prompt", "type": "prompt_agent", "agent_name": "solver", "system_prompt": "Use send_message.",
         "prompt": "go", "depends_on": dep("deploy-solver", "solver-lock")},
        {"id": "freeze", "type": "modify_env_tool_access", "env_id": "world", "role": "alex@example.com",
         "action": "disable", "tools": ["*"], "depends_on": dep("prompt")},
        {"id": "judge-lock", "type": "modify_env_tool_access", "env_id": "world", "role": "grader",
         "action": "disable", "tools": ["send_message"], "depends_on": dep("reset")},
        {"id": "deploy-judge", "type": "deploy_agent", "agent_name": "judge", "env_ids": ["world"], "role": "grader",
         "system_prompt": None, "depends_on": dep("freeze", "judge-lock")},
        {"id": "rubric-judge", "type": "rubrics_verifier", "agent_name": "judge", "grading_policy_prompt": "Grade it.",
         "depends_on": dep("deploy-judge")},
        {"id": "deploy-judge-b", "type": "deploy_agent", "agent_name": "judge-b", "env_ids": ["world"],
         "role": "grader", "depends_on": dep("freeze", "judge-lock")},
        {"id": "rubric-judge-b", "type": "rubrics_verifier", "agent_name": "judge-b", "grading_policy_prompt": "Grade it.",
         "depends_on": dep("deploy-judge-b")},
        {"id": "validity", "type": "rubrics_verifier", "agent_name": "judge", "grading_policy_prompt": "Valid?",
         "depends_on": dep("rubric-judge")},
        {"id": "final", "type": "aggregate_verifiers", "depends_on": dep("validity")},
    ]


def test_judges_only():
    steps = judged_task()
    changes = wrap_steps(steps, agents=["judge", "judge-b"])
    assert [c["vfs_env"] for c in changes] == ["agentvfs-judge", "agentvfs-judge-b"]
    assert [c["call_log"] for c in changes] == ["agentvfs_calls_judge", "agentvfs_calls_judge_b"]
    by = {s["id"]: s for s in steps}
    for name in ("judge", "judge-b"):  # each judge its own env, deployed when the judge would be, after its role's locks
        front = by[f"deploy-{name}-agentvfs"]
        assert front["env_id"] == f"agentvfs-{name}" and front["env_params"] == {"env": "world", "role": "grader"}
        assert set(deps(front)) == {"deploy-env", "reset", "judge-lock", "freeze"}
        assert by[f"deploy-{name}"]["env_ids"] == [f"agentvfs-{name}"]
    # The note goes in the grading policy, never the judge's system prompt (which would replace the agent's own).
    assert by["rubric-judge"]["grading_policy_prompt"] == f"Grade it.\n\n{NOTE}"
    assert by["validity"]["grading_policy_prompt"].endswith(NOTE) and by["deploy-judge"]["system_prompt"] is None
    assert changes[0]["note"] == "grading_policy_prompt of 2 verifier(s)"
    assert set(deps(by["deploy-judge-agentvfs-calls"])) == {"rubric-judge", "validity"}
    assert deps(by["deploy-judge-b-agentvfs-calls"]) == ["rubric-judge-b"]
    assert by["deploy-solver"]["env_ids"] == ["world"] and by["prompt"]["system_prompt"] == "Use send_message."
    position = {i: n for n, i in enumerate(ids(steps))}
    assert all(position[d] < position[s["id"]] for s in steps for d in deps(s))


def test_one_judge_keeps_the_plain_names():
    steps = judged_task()
    [change] = wrap_steps(steps, agents=["judge-b"])
    assert change["vfs_env"] == ENV_NAME and change["call_log"] == "agentvfs_calls"
    with pytest.raises(WrapError, match="already deploys env 'agentvfs'"):
        wrap_steps(steps, agents=["judge"])


def test_colleague_and_solver():
    steps = judged_task()
    next(s for s in steps if s["id"] == "deploy-colleague")["system_prompt"] = "You are Alex."
    # As in a task whose world waits for its colleagues: the prompt waits for the colleague's deploy, and the colleague
    # is frozen after the prompt (a lock on its role that the colleague's env must not wait for).
    next(s for s in steps if s["id"] == "prompt")["depends_on"].append({"task_step_id": "deploy-colleague"})
    changes = wrap_steps(steps, agents=["solver", "colleague"])
    by = {s["id"]: s for s in steps}
    assert set(deps(by["deploy-colleague-agentvfs"])) == {"deploy-env", "reset", "colleague-lock"}
    assert changes[1]["waits_for_locks"] == ["reset", "colleague-lock"]
    assert by["deploy-colleague"]["system_prompt"] == f"You are Alex.\n\n{NOTE}"
    assert changes[1]["note"] == "its deploy_agent system_prompt"
    # No step drives a colleague: its log is copied at the end of the run.
    assert set(deps(by["deploy-colleague-agentvfs-calls"])) == {"rubric-judge-b", "final"}
    assert deps(by["deploy-solver-agentvfs-calls"]) == ["prompt"] and changes[0]["call_log"] == "agentvfs_calls_solver"


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
    toml = (env / "env.toml").read_text()
    assert 'type = "agentvfs"' in toml and 'ref = "ghcr.io/earakely-scale/agentenv-vfs:' in toml
    assert 'dockerfile = "Dockerfile"' in toml
    assert {"Dockerfile", "server.py", "listing.py", "preload.ts", "cli.ts", "package-lock.json"} <= {
        p.name for p in env.iterdir()}
    assert (out / "artifacts" / "agentvfs-calls" / "calls.py").exists()
    with pytest.raises(WrapError, match="exists"):
        wrap_bundle(src, out)
    wrap_bundle(src, tmp_path / "built", build=True)
    (src / "tasks" / "t.json").write_text(json.dumps(judged_task()))
    wrap_bundle(src, tmp_path / "judges", agents=["judge", "judge-b"])
    assert {p.name for p in (tmp_path / "judges" / "envs").iterdir()} == {"agentvfs-judge", "agentvfs-judge-b"}
    assert "ref =" not in (tmp_path / "built" / "envs" / ENV_NAME / "env.toml").read_text()
