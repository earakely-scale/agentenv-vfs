/**
 * One Mirage CLI per tool, named as the tool is (`send_message`), each JSON-schema parameter a flag of the same name
 * (`--channel_id`, with `--channel-id` as an alias). Adapted from agentvfs/vfs-bench's workspace/cli.ts (MIT), which
 * nests `<server> <tool>`; here a command is the tool's own name, so text that names a tool names its command.
 *
 * Parameter types map to flags as:
 * - string, integer, number: a value
 * - boolean: `--flag true|false`
 * - array of scalars: a repeatable flag, or one JSON array (`--ids '["a","b"]'`)
 * - anything else (objects, arrays of objects): one JSON value
 *
 * Values go loose, as a model's tool call reaches the server: the server checks them, as it does over MCP.
 */
import { Argument, CLI, CLIHandler, CommandSpec, type CommandFnResult, type Context, FlagView, IOResult, z } from '@struktoai/mirage-node'
import type { NOOPAccessor } from '@struktoai/mirage-core/accessor/base'

export interface JsonSchema {
  type?: string | string[]
  description?: string
  enum?: unknown[]
  items?: JsonSchema
  properties?: Record<string, JsonSchema>
  required?: string[]
  anyOf?: JsonSchema[]
  oneOf?: JsonSchema[]
  default?: unknown
  nullable?: boolean
}

export interface Tool {
  name: string
  description?: string
  parameters?: JsonSchema
}

// Where a call goes (the env server's bridge) and the role it is made as.
const ConfigSchema = z.strictObject({
  url: z.string(),
  role: z.string(),
})
export type Config = z.infer<typeof ConfigSchema>

const encoder = new TextEncoder()

type Kind = 'str' | 'int' | 'float' | 'bool' | 'list' | 'json'

function concrete(schema: JsonSchema): JsonSchema {
  const union = schema.anyOf ?? schema.oneOf
  if (union !== undefined) {
    const real = union.filter((s) => s.type !== 'null')
    if (real.length === 1) return { ...concrete(real[0]!), description: schema.description ?? real[0]!.description }
    return { ...schema, type: 'object' }
  }
  if (Array.isArray(schema.type)) {
    const real = schema.type.filter((t) => t !== 'null')
    return { ...schema, type: real.length === 1 ? real[0] : 'object' }
  }
  return schema
}

function kindOf(schema: JsonSchema): Kind {
  switch (schema.type) {
    case 'string':
      return 'str'
    case 'integer':
      return 'int'
    case 'number':
      return 'float'
    case 'boolean':
      return 'bool'
    case 'array': {
      const item = schema.items === undefined ? 'str' : kindOf(concrete(schema.items))
      return item === 'str' || item === 'int' || item === 'float' ? 'list' : 'json'
    }
    case undefined:
      return schema.enum !== undefined ? 'str' : 'json'
    default:
      return 'json'
  }
}

/** A JSON value's shape, compactly: `[{description: string, amount?: number}]`. */
export function shape(raw: JsonSchema, depth = 0): string {
  const schema = concrete(raw)
  if (depth > 4) return 'any'
  if (schema.enum !== undefined) return schema.enum.map((v) => JSON.stringify(v)).join('|')
  switch (schema.type) {
    case 'array':
      return `[${schema.items === undefined ? 'any' : shape(schema.items, depth + 1)}]`
    case 'object':
    case undefined: {
      if (schema.properties === undefined) return schema.type === undefined ? 'any' : 'object'
      const need = new Set(schema.required ?? [])
      const fields = Object.entries(schema.properties).map(([k, v]) => `${k}${need.has(k) ? '' : '?'}: ${shape(v, depth + 1)}`)
      return `{${fields.join(', ')}}`
    }
    default:
      return String(schema.type)
  }
}

interface Param {
  name: string
  key: string
  kind: Kind
  item: Kind
}

function nullable(schema: JsonSchema): boolean {
  const union = schema.anyOf ?? schema.oneOf ?? []
  return schema.nullable === true || union.some((s) => s.type === 'null') || (Array.isArray(schema.type) && schema.type.includes('null'))
}

/** A flag's names: the parameter's own, and its dashed spelling when that differs. */
function namesOf(param: string): string[] {
  const dashed = param.replace(/_/g, '-')
  return dashed === param ? [`--${param}`] : [`--${param}`, `--${dashed}`]
}

function argumentFor(name: string, raw: JsonSchema, listed: boolean): [Argument, Param] {
  const schema = concrete(raw)
  const kind = kindOf(schema)
  const needed = listed && raw.default === undefined && !nullable(raw)
  const item = kind === 'list' ? kindOf(concrete(schema.items ?? {})) : kind
  const help = [
    schema.description,
    kind === 'json' ? shape(schema) : '',
    schema.default !== undefined ? `default ${JSON.stringify(schema.default)}` : '',
    schema.enum !== undefined ? `one of ${schema.enum.map(String).join(', ')}` : '',
    needed ? 'required' : '',
  ]
    .filter(Boolean)
    .join('; ')
  const init: ConstructorParameters<typeof Argument>[1] = { required: false, help: help === '' ? null : help }
  if (kind === 'list') init.action = 'append'
  else if (kind === 'json') init.metavar = 'JSON'
  const argument = new Argument(namesOf(name), init)
  return [argument, { name, key: name.replace(/-/g, '_'), kind, item }]
}

/** A flag's value as the schema's type where it reads as one, else as it is. */
function loose(text: string, kind: Kind): unknown {
  if (kind === 'int') return /^[+-]?\d+$/.test(text.trim()) ? Number(text) : text
  if (kind === 'float') return text.trim() !== '' && Number.isFinite(Number(text)) ? Number(text) : text
  if (kind === 'bool') return text === 'true' ? true : text === 'false' ? false : text
  if (kind === 'json') {
    try {
      return JSON.parse(text)
    } catch {
      return text
    }
  }
  return text
}

function jsonArray(text: string): unknown[] | undefined {
  if (!text.trimStart().startsWith('[')) return undefined
  try {
    const value: unknown = JSON.parse(text)
    return Array.isArray(value) ? value : undefined
  } catch {
    return undefined
  }
}

function argumentsFrom(params: Param[], ctx: Context): Record<string, unknown> {
  const flags = new FlagView(ctx.flags, ctx.spec)
  const out: Record<string, unknown> = {}
  for (const p of params) {
    if (!(p.key in (ctx.flags ?? {}))) continue
    if (p.kind === 'list') {
      const values = flags.asList(p.key)
      const array = values.length === 1 ? jsonArray(values[0]!) : undefined
      out[p.name] = array ?? values.map((v) => loose(v, p.item))
    } else if (p.kind === 'str') out[p.name] = flags.asStr(p.key)
    else out[p.name] = loose(flags.asStr(p.key) ?? '', p.kind)
  }
  return out
}

function render(result: unknown): string {
  const text = typeof result === 'string' ? result : JSON.stringify(result, null, 2)
  return text.endsWith('\n') ? text : `${text}\n`
}

export interface Reply {
  ok: boolean
  result?: unknown
  error?: string
}

export type Send = (tool: Tool, args: Record<string, unknown>, config: Config) => Promise<Reply>

/** The CLI for one tool: a root command under the tool's name whose flags are its parameters. */
export function toolCli(tool: Tool, send: Send): CLI {
  const props = tool.parameters?.properties ?? {}
  const required = new Set(tool.parameters?.required ?? [])
  const pairs = Object.entries(props).map(([param, schema]) => argumentFor(param, schema, required.has(param)))
  const params = pairs.map(([, p]) => p)
  const handler = new CLIHandler({
    fn: async (accessor: NOOPAccessor, _paths, _texts, ctx: Context): Promise<CommandFnResult> => {
      let args: Record<string, unknown>
      try {
        args = argumentsFrom(params, ctx)
      } catch (error) {
        return [null, new IOResult({ exitCode: 2, stderr: encoder.encode(`${(error as Error).message}\n`) })]
      }
      const reply = await send(tool, args, accessor.config as Config)
      if (!reply.ok) return [null, new IOResult({ exitCode: 1, stderr: encoder.encode(render(reply.error ?? 'failed')) })]
      return [encoder.encode(render(reply.result)), new IOResult()]
    },
    write: true,
  })
  return new CLI({
    spec: new CommandSpec({ name: tool.name, description: tool.description ?? null, arguments: pairs.map(([a]) => a) }),
    handlers: { '': handler },
    configModel: ConfigSchema,
  })
}
