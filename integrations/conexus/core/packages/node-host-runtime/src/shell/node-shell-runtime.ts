import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process'
import { existsSync, mkdirSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

export type ShellKind = 'powershell' | 'bash'
export const DEFAULT_SHELL_TIMEOUT_MS = 120_000

export interface ShellExecParams {
  cliNodeId: string
  command: string
  shell?: ShellKind
  cwd?: string
  initialCwd?: string
  env?: Record<string, string>
  timeoutMs?: number
  signal?: AbortSignal
}

export interface ShellExecResult {
  cli_node_id: string
  command: string
  shell: ShellKind
  cwd: string
  stdout: string
  stderr: string
  exit_code: number | null
  signal: NodeJS.Signals | null
  duration_ms: number
  timed_out: boolean
}

export interface NodeShellRuntimeOptions {
  defaultCwd?: () => string | undefined
  baseEnv?: () => NodeJS.ProcessEnv
  tempRoot?: () => string
}

interface ShellRunState {
  command: string
  stdout: string
  stderr: string
  started: number
  timedOut: boolean
  resultPrefix: string
  cwdPrefix: string
  cleanupPaths: string[]
  timer: NodeJS.Timeout
  abortSignal?: AbortSignal
  abortListener?: () => void
  resolve: (result: ShellExecResult) => void
}

interface ShellSession {
  cliNodeId: string
  shell: ShellKind
  cwd: string
  envSignature: string
  proc: ChildProcessWithoutNullStreams
  current: ShellRunState | null
  chain: Promise<void>
}

function envSignature(env?: Record<string, string>): string {
  if (!env) return ''
  return JSON.stringify(Object.entries(env).sort(([left], [right]) => left.localeCompare(right)))
}

function sessionSpec(shell: ShellKind): { exe: string; args: string[] } {
  if (shell === 'powershell') {
    if (process.platform !== 'win32') {
      return {
        exe: 'pwsh',
        args: ['-NoProfile', '-NoLogo', '-NoExit', '-Command', '-']
      }
    }
    return {
      exe: process.env.ComSpec?.replace(/cmd\.exe$/i, 'WindowsPowerShell\\v1.0\\powershell.exe') ?? 'powershell.exe',
      args: [
        '-NoProfile',
        '-ExecutionPolicy',
        'Bypass',
        '-NoLogo',
        '-NoExit',
        '-Command',
        '[Console]::InputEncoding=[Text.Encoding]::UTF8; [Console]::OutputEncoding=[Text.Encoding]::UTF8; $OutputEncoding=[Text.Encoding]::UTF8; $PSDefaultParameterValues["Get-Content:Encoding"]="UTF8"'
      ]
    }
  }
  return { exe: 'bash', args: ['--noprofile', '--norc'] }
}

function psSingleQuoted(value: string): string {
  return `'${value.replace(/'/g, "''")}'`
}

function bashSingleQuoted(value: string): string {
  return `'${value.replace(/'/g, "'\\''")}'`
}

function cleanupRunFiles(paths: string[]): void {
  for (const path of paths) {
    try {
      rmSync(path, { force: true })
    } catch {
      // Best-effort cleanup only.
    }
  }
}

function stripRuntimeLines(text: string, prefixes: string[], hiddenSubstrings: string[] = []): string {
  return text
    .split(/\r?\n/)
    .filter((line) => !prefixes.some((prefix) => line.includes(prefix))
      && !hiddenSubstrings.some((substring) => line.includes(substring)))
    .join('\n')
    .trimEnd()
}

function readMarker(text: string, prefix: string): string | null {
  const index = text.indexOf(prefix)
  if (index < 0) return null
  const rest = text.slice(index + prefix.length)
  const lineEnd = rest.search(/\r?\n/)
  return (lineEnd < 0 ? rest : rest.slice(0, lineEnd)).trim()
}

function parseExitCode(value: string | null): number | null {
  if (value === null) return null
  const parsed = Number.parseInt(value, 10)
  return Number.isFinite(parsed) ? parsed : null
}

export class NodeShellRuntime {
  private readonly sessions = new Map<string, ShellSession>()

  constructor(private readonly options: NodeShellRuntimeOptions = {}) {}

  resolveShellKind(value: unknown): ShellKind {
    if (process.platform === 'win32') return value === 'bash' ? 'bash' : 'powershell'
    return value === 'powershell' ? 'powershell' : 'bash'
  }

  execute(params: ShellExecParams): Promise<ShellExecResult> {
    const shell = this.resolveShellKind(params.shell)
    const timeoutMs = Math.max(1_000, Math.min(params.timeoutMs ?? DEFAULT_SHELL_TIMEOUT_MS, 10 * 60 * 1_000))
    const session = this.getSession(params, shell)
    const run = session.chain.then(() => {
      if (params.signal?.aborted) throw params.signal.reason ?? new Error('Shell command was aborted.')
      return new Promise<ShellExecResult>((resolve) => {
        const runId = `${Date.now()}_${Math.random().toString(36).slice(2, 8)}`
        const input = this.commandInput(shell, params.command, runId)
        const current: ShellRunState = {
          command: params.command,
          stdout: '',
          stderr: '',
          started: Date.now(),
          timedOut: false,
          resultPrefix: input.resultPrefix,
          cwdPrefix: input.cwdPrefix,
          cleanupPaths: input.cleanupPaths,
          resolve,
          timer: setTimeout(() => {
            current.timedOut = true
            this.disposeSession(session)
          }, timeoutMs)
        }
        session.current = current
        if (params.signal) {
          current.abortSignal = params.signal
          current.abortListener = () => this.disposeSession(session)
          params.signal.addEventListener('abort', current.abortListener, { once: true })
          if (params.signal.aborted) {
            current.abortListener()
            return
          }
        }
        try {
          session.proc.stdin.write(input.text)
        } catch (error) {
          clearTimeout(current.timer)
          if (current.abortSignal && current.abortListener) {
            current.abortSignal.removeEventListener('abort', current.abortListener)
          }
          session.current = null
          current.stderr = String(error)
          current.resolve({
            cli_node_id: session.cliNodeId,
            command: current.command,
            shell: session.shell,
            cwd: session.cwd,
            stdout: current.stdout,
            stderr: current.stderr,
            exit_code: null,
            signal: null,
            duration_ms: Date.now() - current.started,
            timed_out: false
          })
        }
      })
    })
    session.chain = run.then(() => undefined).catch(() => undefined)
    return run
  }

  disposeAll(): void {
    for (const session of this.sessions.values()) this.disposeSession(session)
  }

  private resolveStartCwd(params: ShellExecParams, existing?: ShellSession): string {
    const requested = params.cwd?.trim()
    if (requested) return requested
    return existing?.cwd || params.initialCwd?.trim() || this.options.defaultCwd?.() || process.cwd()
  }

  private shellTempDir(): string {
    const directory = join(this.options.tempRoot?.() ?? tmpdir(), 'conexus-shell')
    if (!existsSync(directory)) mkdirSync(directory, { recursive: true })
    return directory
  }

  private tempScriptPath(shell: ShellKind, runId: string, user = false): string {
    const extension = shell === 'powershell' ? 'ps1' : 'sh'
    return join(this.shellTempDir(), `${runId}${user ? '_user' : ''}.${extension}`)
  }

  private finalizeCurrent(session: ShellSession, exitCode: number | null, signal: NodeJS.Signals | null): void {
    const current = session.current
    if (!current) return
    clearTimeout(current.timer)
    if (current.abortSignal && current.abortListener) {
      current.abortSignal.removeEventListener('abort', current.abortListener)
    }
    cleanupRunFiles(current.cleanupPaths)
    session.current = null
    const markerCwd = readMarker(current.stdout, current.cwdPrefix)
    if (markerCwd) session.cwd = markerCwd
    current.resolve({
      cli_node_id: session.cliNodeId,
      command: current.command,
      shell: session.shell,
      cwd: session.cwd,
      stdout: stripRuntimeLines(current.stdout, [current.resultPrefix, current.cwdPrefix], current.cleanupPaths),
      stderr: current.stderr.trimEnd(),
      exit_code: exitCode,
      signal,
      duration_ms: Date.now() - current.started,
      timed_out: current.timedOut
    })
  }

  private maybeFinalizeFromStdout(session: ShellSession): void {
    const current = session.current
    if (!current) return
    const exitCode = parseExitCode(readMarker(current.stdout, current.resultPrefix))
    const markerCwd = readMarker(current.stdout, current.cwdPrefix)
    if (exitCode === null || markerCwd === null) return
    session.cwd = markerCwd
    this.finalizeCurrent(session, exitCode, null)
  }

  private disposeSession(session: ShellSession): void {
    if (this.sessions.get(session.cliNodeId) === session) this.sessions.delete(session.cliNodeId)
    if (!session.proc.killed) session.proc.kill()
  }

  private createSession(params: ShellExecParams, shell: ShellKind, cwd: string, signature: string): ShellSession {
    const { exe, args } = sessionSpec(shell)
    const baseEnv = this.options.baseEnv?.() ?? process.env
    const proc = spawn(exe, args, {
      cwd,
      env: {
        ...baseEnv,
        PYTHONIOENCODING: 'utf-8',
        PYTHONUTF8: '1',
        NODE_DISABLE_COLORS: baseEnv.NODE_DISABLE_COLORS ?? '1',
        ...(params.env ?? {})
      },
      windowsHide: true
    })
    const session: ShellSession = {
      cliNodeId: params.cliNodeId,
      shell,
      cwd,
      envSignature: signature,
      proc,
      current: null,
      chain: Promise.resolve()
    }
    proc.stdout.on('data', (chunk: Buffer) => {
      if (!session.current) return
      session.current.stdout += chunk.toString()
      this.maybeFinalizeFromStdout(session)
    })
    proc.stderr.on('data', (chunk: Buffer) => {
      if (session.current) session.current.stderr += chunk.toString()
    })
    proc.on('error', (error) => {
      if (!session.current) return
      session.current.stderr += session.current.stderr ? `\n${error.message}` : error.message
    })
    proc.on('close', (code, signal) => {
      this.finalizeCurrent(session, code, signal)
      if (this.sessions.get(session.cliNodeId) === session) this.sessions.delete(session.cliNodeId)
    })
    this.sessions.set(params.cliNodeId, session)
    return session
  }

  private getSession(params: ShellExecParams, shell: ShellKind): ShellSession {
    const existing = this.sessions.get(params.cliNodeId)
    const cwd = this.resolveStartCwd(params, existing)
    const signature = envSignature(params.env)
    if (existing
      && existing.shell === shell
      && existing.envSignature === signature
      && (!params.cwd?.trim() || existing.cwd === cwd)
      && !existing.proc.killed) {
      return existing
    }
    if (existing) this.disposeSession(existing)
    return this.createSession(params, shell, cwd, signature)
  }

  private commandInput(shell: ShellKind, command: string, runId: string): {
    text: string
    resultPrefix: string
    cwdPrefix: string
    cleanupPaths: string[]
  } {
    const resultPrefix = `__CONEXUS_RESULT_${runId}__`
    const cwdPrefix = `__CONEXUS_CWD_${runId}__`
    const scriptPath = this.tempScriptPath(shell, runId)
    if (shell === 'powershell') {
      const userScriptPath = this.tempScriptPath(shell, runId, true)
      writeFileSync(userScriptPath, `\uFEFF${command}\n`, 'utf8')
      writeFileSync(scriptPath, [
        '\uFEFF$__conexus_previous_eap = $ErrorActionPreference',
        '$ErrorActionPreference = "Stop"',
        '$global:LASTEXITCODE = $null',
        '$__conexus_success = $false',
        'try {',
        `. ${psSingleQuoted(userScriptPath)}`,
        '$__conexus_success = $?',
        '} catch {',
        '[Console]::Error.WriteLine($_.ToString())',
        '$__conexus_success = $false',
        '} finally {',
        '$__conexus_last = $global:LASTEXITCODE',
        '$__conexus_ec = if ($__conexus_success -and ($null -eq $__conexus_last -or $__conexus_last -eq 0)) { 0 } elseif ($null -ne $__conexus_last -and $__conexus_last -ne 0) { $__conexus_last } else { 1 }',
        '$ErrorActionPreference = $__conexus_previous_eap',
        `[Console]::Out.WriteLine("${resultPrefix}$__conexus_ec")`,
        `[Console]::Out.WriteLine("${cwdPrefix}$((Get-Location).Path)")`,
        '}'
      ].join('\n') + '\n', 'utf8')
      return {
        resultPrefix,
        cwdPrefix,
        cleanupPaths: [scriptPath, userScriptPath],
        text: `$__conexus_script = ${psSingleQuoted(scriptPath)}; . $__conexus_script\n`
      }
    }
    writeFileSync(scriptPath, [
      command,
      '__conexus_ec=$?',
      `printf '%s\\n' '${resultPrefix}'"$__conexus_ec"`,
      `printf '%s\\n' '${cwdPrefix}'"$(pwd)"`
    ].join('\n') + '\n', 'utf8')
    return {
      resultPrefix,
      cwdPrefix,
      cleanupPaths: [scriptPath],
      text: `. ${bashSingleQuoted(scriptPath)}\n`
    }
  }
}

export function createNodeShellRuntime(options: NodeShellRuntimeOptions = {}): NodeShellRuntime {
  return new NodeShellRuntime(options)
}
