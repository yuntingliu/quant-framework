export type AgentSqlMode = 'read' | 'write'

export interface AgentSqlResultLike {
  success: boolean
  rows?: unknown[]
  rowCount?: number | null
  row_count?: number | null
  error?: string
}

export interface AgentExecutableToolResultLike {
  success: boolean
  result?: unknown
  output?: unknown
  stdout?: string
  stderr?: string
  exitCode?: number | null
  exit_code?: number | null
  timedOut?: boolean
  timed_out?: boolean
}

function meaningfulToolOutput(result: AgentExecutableToolResultLike): unknown {
  if (result.result !== undefined) return result.result
  if (result.output !== undefined) {
    if (
      result.output
      && typeof result.output === 'object'
      && !Array.isArray(result.output)
      && Object.keys(result.output).length === 1
      && 'stdout' in result.output
    ) {
      return result.stdout?.trim() ? result.stdout : undefined
    }
    return result.output
  }
  return result.stdout?.trim() ? result.stdout : undefined
}

/** Removes runtime, node, process, and timing metadata from executable Tool results. */
export function executableAgentToolResult(result: AgentExecutableToolResultLike): Record<string, unknown> {
  const output = meaningfulToolOutput(result)
  const timedOut = result.timedOut === true || result.timed_out === true
  const exitCode = typeof result.exitCode === 'number' ? result.exitCode : result.exit_code
  const error = result.success
    ? undefined
    : result.stderr?.trim()
      || (timedOut
        ? 'Tool execution timed out.'
        : `Tool exited with code ${String(exitCode ?? 'unknown')}.`)
  return {
    success: result.success,
    ...(output === undefined ? {} : { output }),
    ...(timedOut ? { timed_out: true } : {}),
    ...(error ? { error } : {})
  }
}

/** Removes driver and Data Source metadata from a model-visible SQL result. */
export function sqlAgentToolResult(
  mode: AgentSqlMode,
  result: AgentSqlResultLike
): Record<string, unknown> {
  if (!result.success) {
    return {
      success: false,
      error: typeof result.error === 'string' && result.error.trim()
        ? result.error.trim()
        : 'SQL execution failed.'
    }
  }
  const count = typeof result.row_count === 'number'
    ? result.row_count
    : typeof result.rowCount === 'number'
      ? result.rowCount
      : 0
  return mode === 'read'
    ? {
        success: true,
        rows: Array.isArray(result.rows) ? result.rows : [],
        row_count: count
      }
    : { success: true, affected_rows: count }
}
