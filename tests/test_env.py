import inspect

from agent_env.env.envs.mcp_server import MCPServerEnv

from agentenv_vfs.env import AgentVfsEnv


def test_deploy_keeps_the_base_signature():
    """deploy_env reads option defaults off the env's deploy(): every base parameter, same default, plus env_params."""
    base = inspect.signature(MCPServerEnv.deploy).parameters
    ours = inspect.signature(AgentVfsEnv.deploy).parameters
    for name, p in base.items():
        assert name in ours and ours[name].default == p.default, name
    assert "env_params" in ours
    assert not any(p.kind is inspect.Parameter.VAR_KEYWORD for p in ours.values())


def test_refusals():
    env = AgentVfsEnv.__new__(AgentVfsEnv)
    assert "env_params" in env.deploy_refusal(env_params=None)
    assert "not other" in env.deploy_refusal(env_params={"env": "x", "other": 1})
    assert "env_params" in env.deploy_refusal(env_params={"env": []})
