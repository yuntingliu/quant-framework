import { execFile } from 'node:child_process'
import { existsSync } from 'node:fs'
import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { pathToFileURL } from 'node:url'
import { promisify } from 'node:util'
import postcss from 'postcss'
import tailwindcss from 'tailwindcss'

const execFileAsync = promisify(execFile)
const BUILT_IN_PACKAGES = new Set(['react', 'react-dom'])
const MAX_APP_CODE_CHARACTERS = 2_000_000
const MAX_APP_DEPENDENCIES = 64
const OPACITY_SCALE = Object.fromEntries(
  Array.from({ length: 101 }, (_value, index) => [String(index), `${index / 100}`])
)

export interface AppBundleInput {
  code?: unknown
  dependencies?: unknown
}

export interface AppBundleResult {
  ok: boolean
  js?: string
  css?: string
  error?: string
  installed?: string[]
}

export interface AppBundlerOptions {
  dependencyRoot: string
  packageRoots: string[]
  /** Physical module entry when the host stores its dependencies in an archive. */
  esbuildModulePath?: string
  npmCommand?: string
  npmCache?: string
  installTimeoutMs?: number
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function packageBase(specifier: string): string {
  if (specifier.startsWith('@')) return specifier.split('/').slice(0, 2).join('/')
  return specifier.split('/')[0] ?? specifier
}

function isBareSpecifier(specifier: string): boolean {
  return Boolean(specifier)
    && !specifier.startsWith('.')
    && !specifier.startsWith('/')
    && !specifier.startsWith('node:')
    && !specifier.includes(':')
}

function validPackageName(value: string): boolean {
  return /^(?:@[a-z0-9][a-z0-9._-]*\/[a-z0-9][a-z0-9._-]*|[a-z0-9][a-z0-9._-]*)$/i.test(value)
}

function findBarePackages(source: string): string[] {
  const packages = new Set<string>()
  const patterns = [
    /\bimport\s+(?:type\s+)?(?:[^'"]*?\s+from\s+)?['"]([^'"]+)['"]/g,
    /\bexport\s+(?:type\s+)?[^'"]*?\s+from\s+['"]([^'"]+)['"]/g,
    /\bimport\s*\(\s*['"]([^'"]+)['"]\s*\)/g,
    /\brequire\s*\(\s*['"]([^'"]+)['"]\s*\)/g
  ]
  for (const pattern of patterns) {
    let match: RegExpExecArray | null
    while ((match = pattern.exec(source))) {
      const specifier = match[1] ?? ''
      if (!isBareSpecifier(specifier)) continue
      const name = packageBase(specifier)
      if (!validPackageName(name)) throw new Error(`Invalid App dependency name: ${name}`)
      packages.add(name)
    }
  }
  return [...packages]
}

function dependencyMap(value: unknown): Record<string, string> {
  if (!isRecord(value)) return {}
  const entries = Object.entries(value)
  if (entries.length > MAX_APP_DEPENDENCIES) {
    throw new Error(`App dependencies must not exceed ${MAX_APP_DEPENDENCIES} packages.`)
  }
  const result: Record<string, string> = {}
  for (const [rawName, rawVersion] of entries) {
    const name = packageBase(rawName.trim())
    if (!validPackageName(name)) throw new Error(`Invalid App dependency name: ${rawName}`)
    if (typeof rawVersion !== 'string' || !rawVersion.trim() || rawVersion.length > 200) {
      throw new Error(`Invalid App dependency version for ${name}.`)
    }
    result[name] = rawVersion.trim()
  }
  return result
}

function packageJsonPath(root: string, packageName: string): string {
  return join(root, 'node_modules', ...packageName.split('/'), 'package.json')
}

function hasPackage(root: string, packageName: string): boolean {
  return existsSync(packageJsonPath(root, packageName))
}

function installSpec(packageName: string, dependencies: Record<string, string>): string {
  const version = dependencies[packageName]
  return version ? `${packageName}@${version}` : packageName
}

function runtimeSource(): string {
  return `
import React from 'react'
import { createRoot } from 'react-dom/client'
import UserApp from './user-app'

const initial = window.__CONEXUS_INITIAL__ || { props: {}, state: {} }
const pendingRequests = new Map()

function post(type, payload) {
  window.parent.postMessage({ source: 'conexus-app', type, ...(payload || {}) }, '*')
}

function request(action, payload) {
  const requestId = (globalThis.crypto && globalThis.crypto.randomUUID)
    ? globalThis.crypto.randomUUID()
    : String(Date.now()) + '-' + Math.random().toString(36).slice(2)
  post('sdk_request', { requestId, action, payload })
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => {
      pendingRequests.delete(requestId)
      reject(new Error('Conexus SDK request timed out.'))
    }, 10 * 60 * 1000)
    pendingRequests.set(requestId, { resolve, reject, timeout })
  })
}

window.addEventListener('message', (event) => {
  const message = event.data
  if (!message || message.source !== 'conexus-host' || message.type !== 'sdk_response') return
  const requestId = typeof message.requestId === 'string' ? message.requestId : ''
  const pending = pendingRequests.get(requestId)
  if (!pending) return
  pendingRequests.delete(requestId)
  clearTimeout(pending.timeout)
  if (message.ok) pending.resolve(message.result)
  else pending.reject(new Error(typeof message.error === 'string' ? message.error : 'Conexus SDK request failed.'))
})

class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props)
    this.state = { error: null }
  }
  static getDerivedStateFromError(error) { return { error } }
  componentDidCatch(error) { post('error', { error: error instanceof Error ? error.message : String(error) }) }
  render() {
    if (!this.state.error) return this.props.children
    const message = this.state.error instanceof Error ? this.state.error.message : String(this.state.error)
    return React.createElement('pre', { className: 'runtime-error' }, message)
  }
}

function Root() {
  const [state, setLocalState] = React.useState(() => initial.state || {})
  const stateRef = React.useRef(state)
  React.useEffect(() => { stateRef.current = state }, [state])
  const setState = React.useCallback((patch) => {
    setLocalState((previous) => {
      const nextPatch = typeof patch === 'function' ? patch(previous) : patch
      if (!nextPatch || typeof nextPatch !== 'object' || Array.isArray(nextPatch)) return previous
      const next = { ...previous, ...nextPatch }
      post('state', { state: next })
      return next
    })
  }, [])
  const submit = React.useCallback((data) => post('submit', { data, state: stateRef.current }), [])
  React.useEffect(() => { post('ready', {}) }, [])
  return React.createElement(UserApp, {
    props: initial.props || {},
    state,
    setState,
    submit,
    conexus: { setState, submit, agent: { run: (params) => request('agent.run', params) } }
  })
}

const root = document.getElementById('root')
if (!root) throw new Error('App root element was not found.')
createRoot(root).render(React.createElement(ErrorBoundary, null, React.createElement(Root)))
`
}

async function buildTailwindCss(source: string): Promise<string> {
  const result = await postcss([
    tailwindcss({
      content: [{ raw: source, extension: 'tsx' }],
      corePlugins: { preflight: false },
      theme: {
        extend: {
          opacity: OPACITY_SCALE,
          colors: {
            surface: { 0: '#08080d', 1: '#0f0f17', 2: '#0a0a12', 3: '#141420' }
          }
        }
      },
      plugins: []
    })
  ]).process('@tailwind utilities;', { from: undefined })
  return result.css
}

export class AppBundler {
  private installQueue: Promise<void> = Promise.resolve()

  constructor(private readonly options: AppBundlerOptions) {}

  async bundle(input: AppBundleInput): Promise<AppBundleResult> {
    try {
      return await this.bundleUnchecked(isRecord(input) ? input : {})
    } catch (error) {
      return { ok: false, error: error instanceof Error ? error.message : String(error) }
    }
  }

  private async ensureDependencyRoot(): Promise<void> {
    await mkdir(this.options.dependencyRoot, { recursive: true })
    const packageJson = join(this.options.dependencyRoot, 'package.json')
    if (!existsSync(packageJson)) {
      await writeFile(packageJson, JSON.stringify({
        private: true,
        name: 'conexus-app-deps',
        version: '0.0.0'
      }, null, 2), 'utf-8')
    }
  }

  private async ensurePackages(packages: string[], dependencies: Record<string, string>): Promise<string[]> {
    const operation = this.installQueue.then(async () => {
      await this.ensureDependencyRoot()
      const installed: string[] = []
      for (const packageName of packages) {
        if (BUILT_IN_PACKAGES.has(packageName)) continue
        if (hasPackage(this.options.dependencyRoot, packageName)
          || this.options.packageRoots.some((root) => hasPackage(root, packageName))) continue
        await execFileAsync(this.options.npmCommand ?? (process.platform === 'win32' ? 'npm.cmd' : 'npm'), [
          'install',
          '--prefix',
          this.options.dependencyRoot,
          '--ignore-scripts',
          '--no-audit',
          '--no-fund',
          ...(this.options.npmCache ? ['--cache', this.options.npmCache] : []),
          installSpec(packageName, dependencies)
        ], {
          windowsHide: true,
          timeout: this.options.installTimeoutMs ?? 120_000
        })
        installed.push(packageName)
      }
      return installed
    })
    this.installQueue = operation.then(() => undefined, () => undefined)
    return operation
  }

  private async bundleUnchecked(input: AppBundleInput): Promise<AppBundleResult> {
    const code = typeof input.code === 'string' ? input.code : ''
    if (!code.trim()) return { ok: false, error: 'App code is empty.' }
    if (code.length > MAX_APP_CODE_CHARACTERS) {
      return { ok: false, error: `App code must not exceed ${MAX_APP_CODE_CHARACTERS} characters.` }
    }
    const dependencies = dependencyMap(input.dependencies)
    const packages = [...new Set([
      ...Object.keys(dependencies).map(packageBase),
      ...findBarePackages(code),
      'react',
      'react-dom'
    ])]
    if (packages.length > MAX_APP_DEPENDENCIES) {
      return { ok: false, error: `App dependencies must not exceed ${MAX_APP_DEPENDENCIES} packages.` }
    }
    const installed = await this.ensurePackages(packages, dependencies)
    const css = await buildTailwindCss(code)
    const temporary = await mkdtemp(join(tmpdir(), 'conexus-app-'))
    try {
      const runtimePath = join(temporary, 'runtime.tsx')
      const userPath = join(temporary, 'user-app.tsx')
      await writeFile(runtimePath, runtimeSource(), 'utf-8')
      await writeFile(userPath, code, 'utf-8')
      const { build } = this.options.esbuildModulePath
        ? await import(pathToFileURL(this.options.esbuildModulePath).href) as typeof import('esbuild')
        : await import('esbuild')
      const result = await build({
        absWorkingDir: temporary,
        entryPoints: [runtimePath],
        bundle: true,
        format: 'iife',
        platform: 'browser',
        target: ['chrome120'],
        jsx: 'automatic',
        write: false,
        sourcemap: 'inline',
        logLevel: 'silent',
        nodePaths: [
          join(this.options.dependencyRoot, 'node_modules'),
          ...this.options.packageRoots.map((root) => join(root, 'node_modules'))
        ],
        define: { 'process.env.NODE_ENV': JSON.stringify('production') }
      })
      return {
        ok: true,
        js: result.outputFiles[0]?.text ?? '',
        css,
        installed
      }
    } finally {
      await rm(temporary, { recursive: true, force: true })
    }
  }
}

export function createAppBundler(options: AppBundlerOptions): AppBundler {
  return new AppBundler(options)
}
