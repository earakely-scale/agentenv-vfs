#!/bin/bash
# Integration test (needs Docker): the agentvfs server image fronting a plain MCP server, driven over its MCP and
# data planes, with a role (two roles, one workspace each) and without one. Run from the repo root with agent-env's
# Python on PATH (it brings agentenv_protocol and mcp).
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd); root=$(cd "$here/../.." && pwd)
docker build -q -t agentenv-vfs:test "$root/src/agentenv_vfs/server" >/dev/null
docker build -q -t agentenv-vfs-fake:test -f "$here/Dockerfile.fake" "$here" >/dev/null
docker network create agentenv-vfs-test >/dev/null 2>&1 || true
trap 'docker rm -f agentenv-vfs-fake agentenv-vfs-srv >/dev/null 2>&1' EXIT
for run in role norole; do
  docker rm -f agentenv-vfs-fake agentenv-vfs-srv >/dev/null 2>&1 || true
  docker run -d --name agentenv-vfs-fake --network agentenv-vfs-test agentenv-vfs-fake:test >/dev/null
  docker run -d --name agentenv-vfs-srv --network agentenv-vfs-test -p 127.0.0.1:38765:18765 agentenv-vfs:test >/dev/null
  sleep 6
  script=$([ $run = role ] && echo drive.py || echo drive_norole.py)
  python "$here/$script" http://127.0.0.1:38765 http://agentenv-vfs-fake:18765/mcp 2>&1 | grep -v 'Session termination'
done
