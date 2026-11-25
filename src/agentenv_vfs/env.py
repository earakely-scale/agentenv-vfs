"""The ``agentvfs`` env type: an MCP server env (its server in ``server/``) that fronts another env of the same run.

A deploy_env names the env it fronts (or a list of them), and optionally the role whose workspace opens at once:

    {"type": "deploy_env", "env_id": "agentvfs", "env_params": {"env": "notes", "role": "alice"}}
    {"type": "deploy_env", "env_id": "agentvfs", "env_params": {"env": ["notes", "crm"]}}

Once deployed, it is handed each fronted env's MCP endpoint, as its own sandbox reaches it, over ``data/add``.
"""
from __future__ import annotations

from typing import ClassVar, Optional

from agent_env.attribution import Attribution
from agent_env.env.env import DeployedEnv
from agent_env.env.envs.mcp_server import MCPServerEnv
from agent_env.env.gateway import GatewayMode
from agent_env.providers.sandbox_providers.sandbox_provider import reachable_url
from agentenv_protocol import DataPart
from agentenv_protocol import client as protocol

PARAMS = ("env", "role", "headers")


class AgentVfsEnv(MCPServerEnv):
    type: ClassVar[str] = "agentvfs"
    description = "Another env's MCP tools as shell commands in a Mirage workspace (agentenv-vfs)"

    def deploy_refusal(self, *, sandbox_type: Optional[str] = None, env_params: Optional[dict] = None,
                       **options) -> Optional[str]:
        names = env_params.get("env") if isinstance(env_params, dict) else None
        if not (isinstance(names, str) or (isinstance(names, list) and names and all(isinstance(n, str) for n in names))):
            return 'agentvfs envs take env_params {"env": <the env id it fronts, or a list of them>, "role"?, "headers"?}'
        if unknown := sorted(set(env_params) - set(PARAMS)):
            return f"agentvfs env_params take {', '.join(PARAMS)}, not {', '.join(unknown)}"
        return super().deploy_refusal(sandbox_type=sandbox_type, **options)

    async def run_env_params(self, env_params: Optional[dict], deployed_envs: list[DeployedEnv]) -> Optional[dict]:
        """Each fronted env's deployment, found among the run's by its id (a bundle's ids end in ``/<name>``)."""
        names = env_params["env"] if isinstance(env_params["env"], list) else [env_params["env"]]
        fronted = []
        for name in names:
            found = [d for d in deployed_envs if d.env_id == name or d.env_id.endswith(f"/{name}")]
            if len(found) != 1:
                raise ValueError(f"agentvfs: {len(found)} deployed envs match '{name}'; deploy it first, once")
            if not found[0].mcp_url:
                raise ValueError(f"agentvfs: env '{name}' serves no MCP endpoint")
            fronted.append({"name": name, "mcp_url": found[0].mcp_url,
                            "sandbox_type": getattr(found[0], "sandbox_type", None)})
        return {**env_params, "fronted": fronted}

    # MCPServerEnv.deploy's own signature, whose defaults deploy_env reads, plus env_params.
    async def deploy(self, ttl_seconds: int = 10800, disk_size_gb: float = 10,
                     gateway_mode: GatewayMode = GatewayMode.PERFORMANCE, cpu: float | None = None,
                     memory_mb: int | None = None, sandbox_type: str | None = None, env_state_type: str | None = None,
                     env_state_instance_id: str | None = None, artifact_id: str | None = None,
                     artifact_version: int | None = None, *, attribution: Optional[Attribution] = None,
                     env_params: Optional[dict] = None) -> DeployedEnv:
        deployed = await super().deploy(
            ttl_seconds=ttl_seconds, disk_size_gb=disk_size_gb, gateway_mode=gateway_mode, cpu=cpu,
            memory_mb=memory_mb, sandbox_type=sandbox_type, env_state_type=env_state_type,
            env_state_instance_id=env_state_instance_id, artifact_id=artifact_id, artifact_version=artifact_version,
            attribution=attribution)
        if env_params:
            # Each fronted env's MCP endpoint as this env's sandbox reaches it.
            envs = [{"name": f["name"], "mcp_url": reachable_url(f["mcp_url"], from_sandbox_type=f["sandbox_type"],
                                                                 to_sandbox_type=getattr(deployed, "sandbox_type", None))}
                    for f in env_params["fronted"]]
            data = {"envs": envs, **{k: env_params[k] for k in ("role", "headers") if env_params.get(k)}}
            await protocol.add_data(deployed.environment_url, [DataPart(data=data)], timeout=600,
                                    headers=deployed.control_headers)
        return deployed
