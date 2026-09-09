import { createNodeShellRuntime, executeToolNode } from '@conexus/node-host-runtime'

/** OS execution only. Agent loops, tools, graph mutations and completion stay in Runtime Core. */
export function createLocalRuntime({ projectRoot, toolEnvironment = {}, capabilities = {} }) {
  const shell = createNodeShellRuntime({ defaultCwd: () => projectRoot, baseEnv: () => ({
    ...Object.fromEntries(['PATH', 'Path', 'SystemRoot', 'ComSpec', 'TEMP', 'TMP', 'HOME', 'USERPROFILE', 'PATHEXT']
      .filter(key => process.env[key]).map(key => [key, process.env[key]])),
    ...toolEnvironment,
  }) })
  const toolRuntimes = process.platform === 'win32' ? ['node', 'python', 'powershell', 'shell'] : ['node', 'python', 'bash', 'shell']
  const adapter = {
    profile: {
      name: 'conexus.local-node-host.v1', nodeTypes: ['tool', 'cli', 'files'],
      toolRuntimes, permissions: ['filesystem', 'network', 'shell'], isolation: ['shell'],
      capabilityIds: Object.keys(capabilities), runNodeTypes: ['tool'],
    },
    async executeShell(params) {
      const result = await shell.execute({ ...params, cliNodeId: `${params.runId}:${params.cliNodeId}`, initialCwd: projectRoot })
      return { success: result.exit_code === 0 && !result.timed_out, cliNodeId: params.cliNodeId,
        shell: result.shell, cwd: result.cwd, stdout: result.stdout, stderr: result.stderr,
        exitCode: result.exit_code, timedOut: result.timed_out, durationMs: result.duration_ms }
    },
    async executeTool(params) {
      const runtime = params.definition.runtime ?? 'node'
      if (!toolRuntimes.includes(runtime) || !params.definition.code?.trim()) throw new Error('Unsupported or empty local Tool.')
      const result = await executeToolNode({ shellRuntime: shell,
        nodeId: params.definition.nodeId ?? params.definition.name, runtime, code: params.definition.code,
        args: params.args, context: { ...params.context, projectPath: projectRoot }, cwd: projectRoot,
        signal: params.signal, sessionPrefix: params.runId, callNode: params.callNode,
      })
      return { success: result.success, runtime, stdout: result.stdout, stderr: result.stderr,
        exitCode: result.exit_code, timedOut: result.timed_out, durationMs: result.duration_ms, result: result.result }
    },
    async executeNodeCapability(params) {
      const execute = capabilities[params.capability]
      return execute ? execute(params) : { result: { success: false, error: 'Capability is unavailable on this host.' } }
    },
    async disposeRun() { shell.disposeAll() },
  }
  return { adapter, close: () => shell.disposeAll() }
}
