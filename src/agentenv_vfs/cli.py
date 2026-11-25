"""``agent-env vfs``: give a bundle's agent the agentvfs interface."""
from __future__ import annotations

import json
from pathlib import Path

import click

from agentenv_vfs.wrap import WrapError, add_env, wrap_bundle


@click.group("vfs")
def vfs() -> None:
    """Work through agentvfs: an env's tools as shell commands in a Mirage workspace."""


@vfs.command("wrap")
@click.argument("bundle", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--out", required=True, type=click.Path(path_type=Path), help="Where to write the wrapped bundle.")
@click.option("--agent", default=None, help="The agent to wrap (default: the one the tasks prompt).")
@click.option("--keep-builtins", is_flag=True, help="Keep the agent's built-in tools.")
@click.option("--no-note", is_flag=True, help="Leave the agent's system prompt as it is.")
@click.option("--no-call-log", is_flag=True, help="Don't copy the agent's shell lines and tool calls into the run.")
@click.option("--build", is_flag=True, help="Build the agentvfs image from the bundle instead of pulling it.")
@click.option("--for-agent", default=None,
              help="The agent the run will use (as `agent-env run --agent` names it), when not the task's own; it "
                   "decides whether the agent's built-in tools can be turned off.")
def wrap(bundle: Path, out: Path, agent: str | None, keep_builtins: bool, no_note: bool, no_call_log: bool,
         build: bool, for_agent: str | None) -> None:
    """Copy BUNDLE to --out with its agent working through agentvfs in front of its env."""
    try:
        changes = wrap_bundle(bundle, out, agent, keep_builtins, not no_note, not no_call_log, build, for_agent)
    except WrapError as e:
        raise click.ClickException(str(e)) from e
    for c in changes:
        click.echo(json.dumps(c))
    click.echo(f"Wrote {out}; run it with: agent-env run {out}")


@vfs.command("add-env")
@click.argument("bundle", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--build", is_flag=True, help="Build the agentvfs image from the bundle instead of pulling it.")
def add(bundle: Path, build: bool) -> None:
    """Add the agentvfs env (envs/agentvfs) to BUNDLE, for tasks you wire up yourself."""
    click.echo(f"Wrote {add_env(bundle, build)}")
