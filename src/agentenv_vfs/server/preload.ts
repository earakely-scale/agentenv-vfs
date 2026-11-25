/**
 * Loaded into the Mirage daemon before it starts (`node --import ./preload.ts <daemon>`).
 *
 *     AGENTENV_VFS_TOOLS=<tools.json>  AGENTENV_VFS_BRIDGE=<the env server's bridge URL>
 *
 * Registers one CLI per tool in tools.json under the tool's own name (cli.ts). A call posts `{role, tool, arguments}`
 * to the bridge, which makes it at the fronted env's MCP endpoint as that role. The daemon's fetch reaches only the
 * bridge: every other host fails as an unreachable one does. Adapted from agentvfs/vfs-bench's workspace/preload.ts
 * and tools.ts (MIT).
 */
import { readFileSync } from 'node:fs'

import { registerHostFetch } from '@struktoai/mirage-core/commands/builtin/utils/http'
import { registerCliSpec } from '@struktoai/mirage-core/commands/cli/specs'
// Registers Node's host fetch when loaded, so it loads first and the registration below replaces it.
import '@struktoai/mirage-node/commands/builtin/utils/http'

import { type Reply, type Send, type Tool, toolCli } from './cli.ts'

const bridge = new URL(process.env.AGENTENV_VFS_BRIDGE ?? 'http://invalid').origin
const send = globalThis.fetch

const routed = ((input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
  const url = new URL(input instanceof Request ? input.url : String(input))
  if (url.origin !== bridge) {
    return Promise.reject(new TypeError('fetch failed', { cause: new Error(`connect ECONNREFUSED ${url.host}`) }))
  }
  return send(input, init)
}) as typeof fetch
globalThis.fetch = routed
registerHostFetch((url, init) => routed(url, init))

const call: Send = async (tool, args, { url, role }) => {
  try {
    const reply = await fetch(`${url}/call`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ role, tool: tool.name, arguments: args }),
    })
    return (await reply.json()) as Reply
  } catch (error) {
    return { ok: false, error: String(error) }
  }
}

for (const tool of JSON.parse(readFileSync(process.env.AGENTENV_VFS_TOOLS!, 'utf8')) as Tool[]) registerCliSpec(toolCli(tool, call))

process.stdin.on('close', () => process.kill(process.pid, 'SIGTERM'))
process.stdin.resume()
