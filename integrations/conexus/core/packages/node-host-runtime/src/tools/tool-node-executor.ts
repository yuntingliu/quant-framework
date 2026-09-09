import { existsSync, mkdirSync, readdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import type { NodeShellRuntime, ShellKind } from '../shell/node-shell-runtime.js'

export type ToolNodeRuntime = 'node' | 'python' | 'powershell' | 'bash' | 'shell'

export interface ToolNodeExecutionParams {
  shellRuntime: NodeShellRuntime
  nodeId: string
  runtime: ToolNodeRuntime
  code: string
  args: Record<string, unknown>
  context: Record<string, unknown>
  cwd?: string
  timeoutMs?: number
  signal?: AbortSignal
  sessionPrefix?: string
  callNode?: (request: { nodeId: string; args?: Record<string, unknown> }) => Promise<unknown>
}

export interface ToolNodeExecutionResult {
  success: boolean
  runtime: ToolNodeRuntime
  stdout: string
  stderr: string
  exit_code: number | null
  timed_out: boolean
  duration_ms: number
  result?: unknown
}

const RESULT_PREFIX = '__CONEXUS_TOOL_RESULT__'

function ensureDir(directory: string): void {
  if (!existsSync(directory)) mkdirSync(directory, { recursive: true })
}

function toolTempDir(): string {
  const directory = join(tmpdir(), 'conexus-tool-node')
  ensureDir(directory)
  return directory
}

function safeNodeId(nodeId: string): string {
  return nodeId.replace(/[^a-zA-Z0-9_-]/g, '_')
}

function tempPath(runtime: ToolNodeRuntime, nodeId: string): string {
  const extension = runtime === 'node' ? 'mjs' : runtime === 'python' ? 'py' : runtime === 'powershell' ? 'ps1' : 'sh'
  return join(toolTempDir(), `${safeNodeId(nodeId)}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}.${extension}`)
}

function tempDataPath(nodeId: string, name: string): string {
  return join(toolTempDir(), `${safeNodeId(nodeId)}-${name}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}.json`)
}

function tempCallDir(nodeId: string): string {
  const directory = join(toolTempDir(), `${safeNodeId(nodeId)}-calls-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`)
  ensureDir(directory)
  return directory
}

function quotePowerShell(value: string): string {
  return `'${value.replace(/'/g, "''")}'`
}

function quoteBash(value: string): string {
  return `'${value.replace(/'/g, "'\\''")}'`
}

function shellForRuntime(runtime: ToolNodeRuntime): ShellKind {
  if (runtime === 'powershell') return 'powershell'
  if (runtime === 'bash') return 'bash'
  return process.platform === 'win32' ? 'powershell' : 'bash'
}

function commandForScript(runtime: ToolNodeRuntime, path: string, shell: ShellKind): string {
  const quoted = shell === 'powershell' ? quotePowerShell(path) : quoteBash(path)
  if (runtime === 'node') return `node ${quoted}`
  if (runtime === 'python') return shell === 'powershell' ? `python ${quoted}` : `python3 ${quoted}`
  return `. ${quoted}`
}

function nodeWrapper(code: string): string {
  return [
    "const { existsSync: __conexusExistsSync, readFileSync: __conexusReadFileSync, writeFileSync: __conexusWriteFileSync } = await import('node:fs')",
    "const { join: __conexusJoin } = await import('node:path')",
    "const { setTimeout: __conexusSleep } = await import('node:timers/promises')",
    "function __conexusJsonEnv(name) {",
    "  const file = process.env[`${name}_FILE`]",
    "  if (file) return JSON.parse(__conexusReadFileSync(file, 'utf8'))",
    "  return JSON.parse(process.env[name] || '{}')",
    "}",
    "const input = __conexusJsonEnv('CONEXUS_TOOL_INPUT')",
    "const context = __conexusJsonEnv('CONEXUS_TOOL_CONTEXT')",
    "context.runNode = async function runNode(nodeId, args = {}) {",
    "  const dir = process.env.CONEXUS_TOOL_CALL_DIR",
    "  if (!dir) throw new Error('context.runNode is unavailable for this tool run')",
    "  if (typeof nodeId !== 'string' || !nodeId.trim()) throw new Error('context.runNode requires nodeId')",
    "  const id = `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`",
    "  const requestPath = __conexusJoin(dir, `${id}.request.json`)",
    "  const responsePath = __conexusJoin(dir, `${id}.response.json`)",
    "  __conexusWriteFileSync(requestPath, JSON.stringify({ id, nodeId: nodeId.trim(), args }), 'utf8')",
    "  for (;;) {",
    "    if (__conexusExistsSync(responsePath)) {",
    "      const response = JSON.parse(__conexusReadFileSync(responsePath, 'utf8'))",
    "      if (response && response.error) throw new Error(String(response.error))",
    "      return response ? response.result : undefined",
    "    }",
    "    await __conexusSleep(50)",
    "  }",
    "}",
    "context.callNode = context.runNode",
    `const __conexusResultPrefix = ${JSON.stringify(RESULT_PREFIX)}`,
    code,
    '',
    "if (typeof run === 'function') {",
    '  try {',
    '    const result = await run(input, context)',
    '    if (result !== undefined) console.log(`${__conexusResultPrefix}${JSON.stringify(result)}`)',
    '  } catch (error) {',
    "    console.error(error && error.stack ? error.stack : String(error))",
    '    process.exitCode = 1',
    '  }',
    '}'
  ].join('\n')
}

function sleep(milliseconds: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, milliseconds))
}

function asRecord(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : undefined
}

async function monitorToolNodeCalls(params: {
  dir: string
  callNode: NonNullable<ToolNodeExecutionParams['callNode']>
  stopped: () => boolean
}): Promise<void> {
  const pending = new Set<string>()
  while (!params.stopped()) {
    let files: string[] = []
    try {
      files = readdirSync(params.dir).filter((file) => file.endsWith('.request.json'))
    } catch {
      files = []
    }
    for (const file of files) {
      const requestPath = join(params.dir, file)
      if (pending.has(requestPath)) continue
      let request: Record<string, unknown>
      try {
        request = JSON.parse(readFileSync(requestPath, 'utf8')) as Record<string, unknown>
      } catch {
        continue
      }
      pending.add(requestPath)
      void (async () => {
        const id = typeof request.id === 'string' && request.id ? request.id : file.replace(/\.request\.json$/, '')
        const responsePath = join(params.dir, `${id}.response.json`)
        try {
          const nodeId = typeof request.nodeId === 'string' ? request.nodeId.trim() : ''
          if (!nodeId) throw new Error('nodeId is required')
          const result = await params.callNode({ nodeId, args: asRecord(request.args) })
          writeFileSync(responsePath, JSON.stringify({ result }), 'utf8')
        } catch (error) {
          writeFileSync(responsePath, JSON.stringify({ error: String(error) }), 'utf8')
        }
      })()
    }
    await sleep(50)
  }
}

function pythonWrapper(code: string): string {
  return [
    'import asyncio',
    'import inspect',
    'import json',
    'import os',
    'import sys',
    'import time',
    'import uuid',
    '',
    'def __conexus_json_env(name):',
    "    file = os.environ.get(name + '_FILE')",
    '    if file:',
    "        with open(file, 'r', encoding='utf-8') as handle:",
    '            return json.load(handle)',
    "    return json.loads(os.environ.get(name, '{}'))",
    '',
    "input = __conexus_json_env('CONEXUS_TOOL_INPUT')",
    "context = __conexus_json_env('CONEXUS_TOOL_CONTEXT')",
    '',
    'async def __conexus_run_node(node_id, args=None):',
    "    call_dir = os.environ.get('CONEXUS_TOOL_CALL_DIR')",
    '    if not call_dir:',
    "        raise RuntimeError('context.runNode is unavailable for this tool run')",
    '    if not isinstance(node_id, str) or not node_id.strip():',
    "        raise RuntimeError('context.runNode requires nodeId')",
    "    request_id = str(int(time.time() * 1000)) + '-' + uuid.uuid4().hex[:8]",
    "    request_path = os.path.join(call_dir, request_id + '.request.json')",
    "    response_path = os.path.join(call_dir, request_id + '.response.json')",
    "    with open(request_path, 'w', encoding='utf-8') as handle:",
    "        json.dump({'id': request_id, 'nodeId': node_id.strip(), 'args': args or {}}, handle)",
    '    while True:',
    '        if os.path.exists(response_path):',
    "            with open(response_path, 'r', encoding='utf-8') as handle:",
    '                response = json.load(handle)',
    "            if response and response.get('error'):",
    "                raise RuntimeError(str(response.get('error')))",
    "            return response.get('result') if response else None",
    '        await asyncio.sleep(0.05)',
    '',
    "context['runNode'] = __conexus_run_node",
    "context['callNode'] = __conexus_run_node",
    code,
    '',
    "if 'run' in globals() and callable(run):",
    '    try:',
    '        result = run(input, context)',
    '        if inspect.isawaitable(result):',
    '            result = asyncio.run(result)',
    '        if result is not None:',
    `            print('${RESULT_PREFIX}' + json.dumps(result, ensure_ascii=False))`,
    '    except Exception as error:',
    '        print(str(error), file=sys.stderr)',
    '        sys.exit(1)'
  ].join('\n')
}

function scriptBody(runtime: ToolNodeRuntime, code: string): string {
  if (runtime === 'node') return nodeWrapper(code)
  if (runtime === 'python') return pythonWrapper(code)
  return code
}

function parseMarkedResult(stdout: string): { stdout: string; result?: unknown } {
  let result: unknown
  const visibleLines: string[] = []
  for (const line of stdout.split(/\r?\n/)) {
    if (line.startsWith(RESULT_PREFIX)) {
      const raw = line.slice(RESULT_PREFIX.length)
      try {
        result = JSON.parse(raw) as unknown
      } catch {
        result = raw
      }
      continue
    }
    visibleLines.push(line)
  }
  return {
    stdout: visibleLines.join('\n').trimEnd(),
    ...(result !== undefined ? { result } : {})
  }
}

export async function executeToolNode(params: ToolNodeExecutionParams): Promise<ToolNodeExecutionResult> {
  const shell = shellForRuntime(params.runtime)
  const path = tempPath(params.runtime, params.nodeId)
  const inputPath = tempDataPath(params.nodeId, 'input')
  const contextPath = tempDataPath(params.nodeId, 'context')
  const callDir = params.callNode ? tempCallDir(params.nodeId) : undefined
  const inputJson = JSON.stringify(params.args)
  const contextJson = JSON.stringify(params.context)
  writeFileSync(path, scriptBody(params.runtime, params.code), 'utf-8')
  writeFileSync(inputPath, inputJson, 'utf-8')
  writeFileSync(contextPath, contextJson, 'utf-8')

  try {
    let stopped = false
    const monitor = callDir && params.callNode
      ? monitorToolNodeCalls({ dir: callDir, callNode: params.callNode, stopped: () => stopped })
      : Promise.resolve()
    try {
      const shellResult = await params.shellRuntime.execute({
        cliNodeId: `${params.sessionPrefix ? `${params.sessionPrefix}:` : ''}tool:${params.nodeId}`,
        command: commandForScript(params.runtime, path, shell),
        shell,
        cwd: params.cwd,
        env: {
          CONEXUS_TOOL_INPUT: inputJson.length <= 8_000 ? inputJson : '{}',
          CONEXUS_TOOL_INPUT_FILE: inputPath,
          CONEXUS_TOOL_CONTEXT: contextJson.length <= 8_000 ? contextJson : '{}',
          CONEXUS_TOOL_CONTEXT_FILE: contextPath,
          ...(callDir ? { CONEXUS_TOOL_CALL_DIR: callDir } : {})
        },
        timeoutMs: params.timeoutMs,
        signal: params.signal
      })
      const parsed = parseMarkedResult(shellResult.stdout)
      return {
        success: shellResult.exit_code === 0 && !shellResult.timed_out,
        runtime: params.runtime,
        stdout: parsed.stdout,
        stderr: shellResult.stderr,
        exit_code: shellResult.exit_code,
        timed_out: shellResult.timed_out,
        duration_ms: shellResult.duration_ms,
        ...(parsed.result !== undefined ? { result: parsed.result } : {})
      }
    } finally {
      stopped = true
      await monitor
    }
  } finally {
    try {
      rmSync(path, { force: true })
      rmSync(inputPath, { force: true })
      rmSync(contextPath, { force: true })
      if (callDir) rmSync(callDir, { recursive: true, force: true })
    } catch {
      // Best-effort cleanup only.
    }
  }
}
