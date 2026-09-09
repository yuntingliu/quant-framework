import { randomUUID } from 'node:crypto'
import { promises as fsp } from 'node:fs'
import { dirname, extname, isAbsolute, join, relative, resolve } from 'node:path'

export interface BackedWorkspaceNode {
  id: string
  type?: string
  parentId?: string
  data?: Record<string, unknown>
  [key: string]: unknown
}

export interface NodeBackingStoreOptions {
  /** Root used to resolve persisted project-relative backingPath values. */
  root: string
  /** Directory below root that owns all node backing content. Defaults to workspace. */
  directory?: string
  /** Optional source project used when copying assets into another isolated workspace. */
  sourceRoot?: string
  /** Logical browser-visible alias for sourceRoot. Defaults to /project. */
  logicalSourceRoot?: string
  /** Desktop may import arbitrary local files; Web stores must leave this disabled. */
  allowExternalSources?: boolean
}

const APP_FIELDS = ['code', 'props', 'state', 'dependencies'] as const
const AGENT_FIELDS = [
  'objective', 'systemPrompt', 'toolNames', 'summary', 'completionGaps',
  'toolCounts', 'lastError'
] as const
const LEGACY_AGENT_REVISION_FIELD = '_connectedNodeRevisions'
const TOOL_FIELDS = [
  'toolName', 'description', 'runtime', 'exposeAsTool', 'input', 'inputRefs', 'inputSchema',
  'outputSchema', 'output', 'code', 'timeoutMs', 'dependencies', 'permissions', 'sideEffects'
] as const
const CUSTOM_FIELDS = ['customType', 'content', 'data'] as const
const DATASOURCE_FIELDS = [
  'adapter', 'connectionUrl', 'username', 'database', 'exposeSqlTool', 'allowWrites', 'schemaSummary'
] as const
const HARNESS_FIELDS = ['summary', 'template', 'defaultExposureId'] as const
const NODE_OWNED_BACKING_TYPES = new Set([
  'agent',
  'note',
  'document',
  'app',
  'tool',
  'custom',
  'datasource',
  'harness'
])

const importCache = new Map<string, { source: string; destination: string }>()
const backingContentCache = new Map<string, string>()

function text(value: unknown): string {
  return typeof value === 'string' ? value.trim() : ''
}

function hasErrorCode(error: unknown, code: string): boolean {
  return Boolean(error && typeof error === 'object' && 'code' in error && error.code === code)
}

function safeSegment(value: string, fallback: string): string {
  const cleaned = value
    .normalize('NFKC')
    .replace(/[<>:"/\\|?*\u0000-\u001f]/g, '-')
    .replace(/\s+/g, '-')
    .replace(/-+/g, '-')
    .replace(/^[.\s-]+|[.\s-]+$/g, '')
  return (cleaned || fallback).slice(0, 80)
}

function shortHash(value: string): string {
  let hash = 0x811c9dc5
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index)
    hash = Math.imul(hash, 0x01000193)
  }
  return (hash >>> 0).toString(16).padStart(8, '0')
}

function nodeStem(node: BackedWorkspaceNode): string {
  const label = text(node.data?.label) || node.type || 'node'
  return `${safeSegment(label, node.type || 'node')}-${shortHash(node.id)}`
}

function isInside(parent: string, candidate: string): boolean {
  const value = relative(resolve(parent), resolve(candidate))
  return value === '' || (!value.startsWith('..') && !isAbsolute(value))
}

function pathKey(value: string): string {
  const normalized = resolve(value)
  return process.platform === 'win32' ? normalized.toLowerCase() : normalized
}

function nodeRecords(value: unknown): BackedWorkspaceNode[] {
  if (!Array.isArray(value)) return []
  return value.filter((node): node is BackedWorkspaceNode =>
    Boolean(node && typeof node === 'object' && typeof node.id === 'string'))
}

function selectFields(data: Record<string, unknown>, fields: readonly string[]): Record<string, unknown> {
  return Object.fromEntries(fields.filter((field) => field in data).map((field) => [field, data[field]]))
}

function omitFields(data: Record<string, unknown>, fields: readonly string[]): Record<string, unknown> {
  const result = { ...data }
  for (const field of fields) delete result[field]
  return result
}

async function pathKind(path: string): Promise<'file' | 'directory' | null> {
  try {
    const stat = await fsp.stat(path)
    if (stat.isFile()) return 'file'
    if (stat.isDirectory()) return 'directory'
    return null
  } catch {
    return null
  }
}

async function writeText(path: string, content: string): Promise<void> {
  await fsp.mkdir(dirname(path), { recursive: true })
  const cached = backingContentCache.get(path)
  if (cached === content && await pathKind(path) === 'file') return
  try {
    if (await fsp.readFile(path, 'utf8') === content) {
      backingContentCache.set(path, content)
      return
    }
  } catch (error) {
    if (!hasErrorCode(error, 'ENOENT')) throw error
  }
  const temporary = `${path}.${process.pid}.${randomUUID()}.tmp`
  try {
    await fsp.writeFile(temporary, content, { encoding: 'utf8', flag: 'wx', mode: 0o600 })
    await fsp.rename(temporary, path)
    backingContentCache.set(path, content)
  } catch (error) {
    await fsp.rm(temporary, { force: true }).catch(() => undefined)
    throw error
  }
}

export class NodeBackingStore {
  readonly root: string
  readonly contentRoot: string
  private readonly sourceRoot: string
  private readonly logicalSourceRoot: string
  private readonly allowExternalSources: boolean

  constructor(options: NodeBackingStoreOptions) {
    if (!options.root.trim()) throw new Error('Node backing root is required.')
    const directory = text(options.directory) || 'workspace'
    if (isAbsolute(directory) || directory.split(/[\\/]+/).some((segment) => segment === '..')) {
      throw new Error('Node backing directory must be relative to its root.')
    }
    this.root = resolve(options.root)
    this.contentRoot = resolve(this.root, directory)
    if (!isInside(this.root, this.contentRoot) || this.contentRoot === this.root) {
      throw new Error('Node backing directory must remain below its root.')
    }
    this.sourceRoot = resolve(options.sourceRoot ?? this.root)
    this.logicalSourceRoot = (text(options.logicalSourceRoot) || '/project').replace(/\/$/, '')
    this.allowExternalSources = options.allowExternalSources === true
  }

  async ensureStructure(): Promise<void> {
    await fsp.mkdir(this.root, { recursive: true })
    await fsp.mkdir(this.contentRoot, { recursive: true })
    const [realRoot, realContentRoot] = await Promise.all([
      fsp.realpath(this.root),
      fsp.realpath(this.contentRoot)
    ])
    if (realRoot === realContentRoot || !isInside(realRoot, realContentRoot)) {
      throw new Error('Node backing directory resolves outside its root.')
    }
    const directories = await Promise.all(
      ['assets', 'documents', 'agents', 'apps', 'tools', 'data', 'files', 'harnesses']
        .map(async (directory) => {
          const candidate = resolve(this.contentRoot, directory)
          await fsp.mkdir(candidate, { recursive: true })
          return fsp.realpath(candidate)
        })
    )
    if (directories.some((directory) => !isInside(realContentRoot, directory))) {
      throw new Error('A node backing content directory resolves outside its workspace.')
    }
  }

  async materializeNodes(value: unknown): Promise<unknown> {
    if (!Array.isArray(value)) return value
    await this.ensureStructure()
    const nodes = nodeRecords(value)
    const nodesById = new Map(nodes.map((node) => [node.id, node]))
    const harnessDirectories = new Map<string, string>()
    const result: BackedWorkspaceNode[] = []
    for (const node of nodes) {
      result.push(await this.materializeNode(node, nodesById, harnessDirectories))
    }
    return result
  }

  async hydrateNodes(value: unknown): Promise<unknown> {
    if (!Array.isArray(value)) return value
    return Promise.all(value.map((node) => this.hydrateNode(node)))
  }

  requiresMaterialization(value: unknown): boolean {
    for (const node of nodeRecords(value)) {
      const data = node.data ?? {}
      if (node.type === 'agent' && LEGACY_AGENT_REVISION_FIELD in data) return true
      const fields = node.type === 'agent' ? AGENT_FIELDS
        : node.type === 'note' || node.type === 'document' ? ['content'] as const
          : node.type === 'app' ? APP_FIELDS
            : node.type === 'tool' ? TOOL_FIELDS
              : node.type === 'custom' ? CUSTOM_FIELDS
                : node.type === 'datasource' ? DATASOURCE_FIELDS
                  : node.type === 'harness' ? HARNESS_FIELDS
                    : undefined
      if (fields) {
        if (!this.backingPath(data) || fields.some((field) => field in data)) return true
        continue
      }
      if ((node.type === 'image' || node.type === 'files') && !this.backingPath(data)) {
        const source = node.type === 'image' ? text(data.path) || text(data.src) : text(data.path)
        if (node.type === 'files' && source === this.logicalSourceRoot) continue
        if (!source || !/^(?:https?:|data:|blob:)/i.test(source)) return true
      }
    }
    return false
  }

  async hydrateNode<T>(rawNode: T): Promise<T> {
    if (!rawNode || typeof rawNode !== 'object' || Array.isArray(rawNode)) return rawNode
    const node = rawNode as unknown as BackedWorkspaceNode
    const data = { ...(node.data ?? {}) }
    if (node.type === 'agent') delete data[LEGACY_AGENT_REVISION_FIELD]
    const path = this.backingPath(data)
    if (!path || node.type === 'image' || node.type === 'files') return rawNode
    let raw: string
    try {
      const realPath = await fsp.realpath(path)
      if (!isInside(this.contentRoot, realPath)) return rawNode
      raw = await fsp.readFile(realPath, 'utf8')
    } catch (error) {
      if (hasErrorCode(error, 'ENOENT')) return rawNode
      throw error
    }
    backingContentCache.set(path, raw)
    if (node.type === 'note' || node.type === 'document') {
      return {
        ...node,
        data: {
          ...data,
          content: raw
        }
      } as unknown as T
    }
    let payload: unknown
    try {
      payload = JSON.parse(raw)
    } catch (error) {
      throw new Error(`Node backing file is not valid JSON: ${this.storedPath(path)}`, { cause: error })
    }
    if (!payload || typeof payload !== 'object' || Array.isArray(payload)) return rawNode
    const backedData = { ...(payload as Record<string, unknown>) }
    if (node.type === 'agent') delete backedData[LEGACY_AGENT_REVISION_FIELD]
    return {
      ...node,
      data: { ...data, ...backedData }
    } as unknown as T
  }

  async prune(previousValue: unknown, nextValue: unknown): Promise<void> {
    const previousNodes = nodeRecords(previousValue)
    const nextNodes = nodeRecords(nextValue)
    const nextNodeIds = new Set(nextNodes.map((node) => node.id))
    const retainedPaths = new Set(nextNodes
      .map((node) => this.backingPath(node.data ?? {}))
      .filter((path): path is string => Boolean(path))
      .map(pathKey))
    for (const node of previousNodes) {
      if (!nextNodeIds.has(node.id)) importCache.delete(this.importKey(node.id))
      if (!node.type || !NODE_OWNED_BACKING_TYPES.has(node.type)) continue
      const path = this.backingPath(node.data ?? {})
      if (!path || retainedPaths.has(pathKey(path))) continue
      try {
        const [stat, realPath] = await Promise.all([fsp.lstat(path), fsp.realpath(path)])
        if ((!stat.isFile() && !stat.isSymbolicLink()) || !isInside(this.contentRoot, realPath)) continue
        await fsp.unlink(path)
        backingContentCache.delete(path)
      } catch (error) {
        if (!hasErrorCode(error, 'ENOENT')) throw error
        backingContentCache.delete(path)
      }
    }
  }

  private importKey(nodeId: string): string {
    return `${this.root}\0${nodeId}`
  }

  private storedPath(path: string): string {
    return relative(this.root, path).replace(/\\/g, '/')
  }

  private backingPath(data: Record<string, unknown>): string | null {
    const value = text(data.backingPath)
    if (!value || value.includes('\0')) return null
    const normalized = value.replace(/\\/g, '/')
    const candidate = isAbsolute(normalized)
      ? resolve(normalized)
      : resolve(this.root, ...normalized.replace(/^\/+/, '').split('/').filter(Boolean))
    if (candidate === this.contentRoot || !isInside(this.contentRoot, candidate)) return null
    return candidate
  }

  private existingBackingPath(data: Record<string, unknown>): string | null {
    return this.backingPath(data)
  }

  private async sourcePath(value: string): Promise<string | null> {
    const normalized = value.trim().replace(/\\/g, '/')
    if (!normalized || /^(?:https?:|data:|blob:)/i.test(normalized) || normalized.includes('\0')) return null
    if (normalized === this.logicalSourceRoot || normalized.startsWith(`${this.logicalSourceRoot}/`)) {
      const suffix = normalized.slice(this.logicalSourceRoot.length).replace(/^\/+/, '')
      const candidate = resolve(this.sourceRoot, ...suffix.split('/').filter(Boolean))
      if (!isInside(this.sourceRoot, candidate)) return null
      return this.safeRealSource(candidate)
    }
    if (isAbsolute(normalized)) {
      const candidate = resolve(normalized)
      if (isInside(this.root, candidate) || isInside(this.sourceRoot, candidate) || this.allowExternalSources) {
        return this.safeRealSource(candidate)
      }
      return null
    }
    const segments = normalized.replace(/^\/+/, '').split('/').filter(Boolean)
    const local = resolve(this.root, ...segments)
    if (isInside(this.contentRoot, local)) return this.safeRealSource(local)
    const source = resolve(this.sourceRoot, ...segments)
    return isInside(this.sourceRoot, source) ? this.safeRealSource(source) : null
  }

  private async safeRealSource(candidate: string): Promise<string | null> {
    let realPath: string
    try {
      realPath = await fsp.realpath(candidate)
    } catch (error) {
      if (hasErrorCode(error, 'ENOENT')) return null
      throw error
    }
    if (!this.allowExternalSources
      && !isInside(this.root, realPath)
      && !isInside(this.sourceRoot, realPath)) return null
    return realPath
  }

  private async prepareDestination(candidate: string): Promise<void> {
    if (candidate === this.contentRoot || !isInside(this.contentRoot, candidate)) {
      throw new Error('Node backing destination escapes its workspace.')
    }
    const realContentRoot = await fsp.realpath(this.contentRoot)
    let ancestor = dirname(candidate)
    while (true) {
      try {
        const realAncestor = await fsp.realpath(ancestor)
        if (!isInside(realContentRoot, realAncestor)) {
          throw new Error('Node backing destination resolves outside its workspace.')
        }
        break
      } catch (error) {
        if (!hasErrorCode(error, 'ENOENT')) throw error
        const parent = dirname(ancestor)
        if (parent === ancestor) throw error
        ancestor = parent
      }
    }
    await fsp.mkdir(dirname(candidate), { recursive: true })
    if (!isInside(realContentRoot, await fsp.realpath(dirname(candidate)))) {
      throw new Error('Node backing destination resolves outside its workspace.')
    }
    try {
      if ((await fsp.lstat(candidate)).isSymbolicLink()) {
        throw new Error('Node backing destination must not be a symbolic link.')
      }
    } catch (error) {
      if (!hasErrorCode(error, 'ENOENT')) throw error
    }
  }

  private async writeBackingText(path: string, content: string): Promise<void> {
    await this.prepareDestination(path)
    await writeText(path, content)
  }

  private harnessParentId(node: BackedWorkspaceNode): string {
    return text(node.parentId) || text(node.data?.harnessNodeId)
  }

  private async harnessDirectory(
    node: BackedWorkspaceNode,
    nodesById: Map<string, BackedWorkspaceNode>,
    memo: Map<string, string>
  ): Promise<string> {
    const memoized = memo.get(node.id)
    if (memoized) return memoized
    const existing = this.existingBackingPath(node.data ?? {})
    const parent = nodesById.get(this.harnessParentId(node))
    const parentDirectory = parent?.type === 'harness'
      ? join(await this.harnessDirectory(parent, nodesById, memo), 'harnesses')
      : join(this.contentRoot, 'harnesses')
    if (existing && resolve(dirname(dirname(existing))) === resolve(parentDirectory)) {
      const directory = dirname(existing)
      memo.set(node.id, directory)
      return directory
    }
    const directory = join(parentDirectory, nodeStem(node))
    memo.set(node.id, directory)
    return directory
  }

  private async scopeDirectory(
    node: BackedWorkspaceNode,
    category: string,
    nodesById: Map<string, BackedWorkspaceNode>,
    harnessDirectories: Map<string, string>
  ): Promise<string> {
    const harness = nodesById.get(this.harnessParentId(node))
    return harness?.type === 'harness'
      ? join(await this.harnessDirectory(harness, nodesById, harnessDirectories), category)
      : join(this.contentRoot, category)
  }

  private async scopedBackingFile(params: {
    node: BackedWorkspaceNode
    category: string
    suffix: string
    existing: string | null
    nodesById: Map<string, BackedWorkspaceNode>
    harnessDirectories: Map<string, string>
  }): Promise<string> {
    const directory = await this.scopeDirectory(
      params.node,
      params.category,
      params.nodesById,
      params.harnessDirectories
    )
    if (params.existing && resolve(dirname(params.existing)) === resolve(directory)) return params.existing
    return join(directory, `${nodeStem(params.node)}${params.suffix}`)
  }

  private async copyIntoContent(node: BackedWorkspaceNode, source: string, destination: string): Promise<string | null> {
    const sourcePath = await this.sourcePath(source)
    if (!sourcePath || resolve(sourcePath) === resolve(destination)) return sourcePath ? this.storedPath(sourcePath) : null
    const kind = await pathKind(sourcePath)
    if (!kind || (kind === 'directory' && isInside(sourcePath, destination))) return null
    const key = this.importKey(node.id)
    const cached = importCache.get(key)
    if (cached?.source === sourcePath && cached.destination === destination && await pathKind(destination)) {
      return this.storedPath(destination)
    }
    await this.prepareDestination(destination)
    if (kind === 'directory') await fsp.cp(sourcePath, destination, { recursive: true, force: true })
    else await fsp.copyFile(sourcePath, destination)
    importCache.set(key, { source: sourcePath, destination })
    return this.storedPath(destination)
  }

  private async materializeNode(
    node: BackedWorkspaceNode,
    nodesById: Map<string, BackedWorkspaceNode>,
    harnessDirectories: Map<string, string>
  ): Promise<BackedWorkspaceNode> {
    let data = { ...(node.data ?? {}) }
    if (node.type === 'agent') delete data[LEGACY_AGENT_REVISION_FIELD]
    const existing = this.existingBackingPath(data)
    if (node.type === 'agent') {
      const path = await this.scopedBackingFile({ node, category: 'agents', suffix: '.agent.json', existing, nodesById, harnessDirectories })
      await this.writeBackingText(path, `${JSON.stringify(selectFields(data, AGENT_FIELDS), null, 2)}\n`)
      data = { ...omitFields(data, AGENT_FIELDS), backingPath: this.storedPath(path) }
    } else if (node.type === 'note' || node.type === 'document') {
      const path = await this.scopedBackingFile({ node, category: 'documents', suffix: '.md', existing, nodesById, harnessDirectories })
      await this.writeBackingText(path, typeof data.content === 'string' ? data.content : '')
      data = { ...omitFields(data, ['content']), backingPath: this.storedPath(path), format: 'markdown' }
    } else if (node.type === 'app') {
      const path = await this.scopedBackingFile({ node, category: 'apps', suffix: '.app.json', existing, nodesById, harnessDirectories })
      await this.writeBackingText(path, `${JSON.stringify(selectFields(data, APP_FIELDS), null, 2)}\n`)
      data = { ...omitFields(data, APP_FIELDS), backingPath: this.storedPath(path) }
    } else if (node.type === 'tool') {
      const path = await this.scopedBackingFile({ node, category: 'tools', suffix: '.tool.json', existing, nodesById, harnessDirectories })
      await this.writeBackingText(path, `${JSON.stringify(selectFields(data, TOOL_FIELDS), null, 2)}\n`)
      data = { ...omitFields(data, TOOL_FIELDS), backingPath: this.storedPath(path) }
    } else if (node.type === 'custom') {
      const path = await this.scopedBackingFile({ node, category: 'data', suffix: '.custom.json', existing, nodesById, harnessDirectories })
      await this.writeBackingText(path, `${JSON.stringify(selectFields(data, CUSTOM_FIELDS), null, 2)}\n`)
      data = { ...omitFields(data, CUSTOM_FIELDS), backingPath: this.storedPath(path) }
    } else if (node.type === 'datasource') {
      const path = await this.scopedBackingFile({ node, category: 'data', suffix: '.datasource.json', existing, nodesById, harnessDirectories })
      await this.writeBackingText(path, `${JSON.stringify(selectFields(data, DATASOURCE_FIELDS), null, 2)}\n`)
      data = { ...omitFields(data, DATASOURCE_FIELDS), backingPath: this.storedPath(path) }
    } else if (node.type === 'harness') {
      const directory = await this.harnessDirectory(node, nodesById, harnessDirectories)
      const path = join(directory, 'harness.json')
      await this.writeBackingText(path, `${JSON.stringify(selectFields(data, HARNESS_FIELDS), null, 2)}\n`)
      data = { ...omitFields(data, HARNESS_FIELDS), backingPath: this.storedPath(path) }
    } else if (node.type === 'image') {
      const source = text(data.path) || text(data.src)
      if (source && !/^(?:https?:|data:|blob:)/i.test(source)) {
        const sourcePath = await this.sourcePath(source)
        const extension = extname(source) || '.png'
        const destination = await this.scopedBackingFile({
          node,
          category: 'assets',
          suffix: extension,
          existing: existing && extname(existing).toLowerCase() === extension.toLowerCase() ? existing : null,
          nodesById,
          harnessDirectories
        })
        const stored = await this.copyIntoContent(node, source, destination)
        if (stored) {
          data = { ...data, path: stored, src: stored, backingPath: stored }
          if (!text(data.originalSource) && sourcePath && resolve(sourcePath) !== resolve(destination)) {
            data.originalSource = source
          }
        }
      }
    } else if (node.type === 'files') {
      const source = text(data.path)
      const directory = await this.scopeDirectory(node, 'files', nodesById, harnessDirectories)
      if (!source) {
        const destination = existing ?? join(directory, nodeStem(node))
        await this.prepareDestination(destination)
        await fsp.mkdir(destination, { recursive: true })
        const stored = this.storedPath(destination)
        data = { ...data, path: stored, backingPath: stored, status: 'ready' }
      } else {
        const sourcePath = await this.sourcePath(source)
        if (sourcePath && resolve(sourcePath) !== resolve(this.sourceRoot)) {
          const kind = await pathKind(sourcePath)
          const extension = kind === 'file' ? extname(sourcePath) : ''
          const existingKind = existing ? await pathKind(existing) : null
          const destination = existing && existingKind === kind && resolve(dirname(existing)) === resolve(directory)
            ? existing
            : join(directory, `${nodeStem(node)}${extension}`)
          const stored = await this.copyIntoContent(node, source, destination)
          if (stored) {
            data = { ...data, path: stored, backingPath: stored, status: 'ready' }
            if (!text(data.originalSource) && resolve(sourcePath) !== resolve(destination)) {
              data.originalSource = source
            }
          }
        }
      }
    }
    return { ...node, data }
  }
}
