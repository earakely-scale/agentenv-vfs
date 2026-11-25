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
def wrap(bundle: Path, out: Path, agent: str | None, keep_builtins: bool, no_note: bool) -> None:
    """Copy BUNDLE to --out with its agent working through agentvfs in front of its env."""
    try:
        changes = wrap_bundle(bundle, out, agent, keep_builtins, not no_note)
    except WrapError as e:
        raise click.ClickException(str(e)) from e
    for c in changes:
        click.echo(json.dumps(c))
    click.echo(f"Wrote {out}; run it with: agent-env run {out}")


@vfs.command("add-env")
@click.argument("bundle", type=click.Path(exists=True, file_okay=False, path_type=Path))
def add(bundle: Path) -> None:
    """Add the agentvfs env (envs/agentvfs) to BUNDLE, for tasks you wire up yourself."""
    click.echo(f"Wrote {add_env(bundle)}")
