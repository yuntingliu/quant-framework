import { createHash, randomUUID } from 'node:crypto'
import { promises as fsp } from 'node:fs'
import { dirname, resolve } from 'node:path'
import {
  isCanvasAgentObservation,
  PROJECT_NODE_HISTORY_WORKSPACE_ID
} from '@conexus/runtime-protocol'
import type {
  AgentTranscriptMessage,
  CreateNodeHistorySessionCommand,
  NodeHistoryMessage,
  NodeHistoryRevision,
  NodeHistoryRun,
  NodeHistoryRunStatus,
  NodeHistorySession,
  NodeHistorySessionSummary,
  NodeHistorySnapshot,
  SaveNodeHistoryRunCommand,
  SaveNodeHistorySessionCommand
} from '@conexus/runtime-protocol'

export const NODE_HISTORY_DOCUMENT_SCHEMA = 'conexus.node-history' as const
export const NODE_HISTORY_DOCUMENT_VERSION = 2 as const

const HISTORY_PATH = '.conexus/history/node-history.v2.json'
const LEGACY_HISTORY_PATH = '.conexus/history/node-history.v1.json'
const MAX_TEXT_CHARACTERS = 100_000

interface NodeHistoryDocument {
  schema: typeof NODE_HISTORY_DOCUMENT_SCHEMA
  version: typeof NODE_HISTORY_DOCUMENT_VERSION
  sessions: NodeHistorySession[]
  runs: NodeHistoryRun[]
  revisions: NodeHistoryRevision[]
}

interface CanvasNodeProjection {
  id: string
  type?: string
  data?: Record<string, unknown>
  [key: string]: unknown
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function text(value: unknown, maximum = MAX_TEXT_CHARACTERS): string {
  return typeof value === 'string' ? value.trim().slice(0, maximum) : ''
}

function timestamp(value: unknown, fallback: string): string {
  const normalized = text(value, 100)
  return normalized && Number.isFinite(Date.parse(normalized)) ? normalized : fallback
}

function runStatus(value: unknown): NodeHistoryRunStatus {
  if (
    value === 'queued'
    || value === 'starting'
    || value === 'running'
    || value === 'thinking'
    || value === 'waiting'
    || value === 'done'
    || value === 'blocked'
    || value === 'error'
    || value === 'aborted'
  ) return value
  if (value === 'completed') return 'done'
  return 'done'
}

function checksum(value: unknown): string {
  return createHash('sha256').update(JSON.stringify(value), 'utf8').digest('hex')
}

function nodeRecords(value: unknown): CanvasNodeProjection[] {
  return Array.isArray(value)
    ? value.filter((node): node is CanvasNodeProjection =>
        isRecord(node) && typeof node.id === 'string' && node.id.trim().length > 0)
    : []
}

function migratedCliHistoryRecord(value: unknown): Record<string, unknown> | null {
  if (!isRecord(value)
    || typeof value.id !== 'string'
    || !value.id.trim()
    || typeof value.command !== 'string') return null
  return {
    ...structuredClone(value),
    shell: typeof value.shell === 'string' ? value.shell : '',
    cwd: typeof value.cwd === 'string' ? value.cwd : '',
    stdout: typeof value.stdout === 'string' ? value.stdout : '',
    stderr: typeof value.stderr === 'string' ? value.stderr : '',
    exitCode: typeof value.exitCode === 'number' && Number.isFinite(value.exitCode)
      ? value.exitCode
      : null
  }
}

function transcript(value: unknown): AgentTranscriptMessage[] {
  if (!Array.isArray(value)) return []
  return value.flatMap((item): AgentTranscriptMessage[] => {
    if (!isRecord(item)) return []
    if (
      item.role !== 'user'
      && item.role !== 'assistant'
      && item.role !== 'tool'
      && item.role !== 'thinking'
    ) return []
    if (item.content !== null && typeof item.content !== 'string') return []
    return [{
      role: item.role,
      content: typeof item.content === 'string'
        ? item.content.slice(0, MAX_TEXT_CHARACTERS)
        : null,
      ...(item.error === true ? { error: true } : {}),
      ...(Array.isArray(item.tool_calls) ? { tool_calls: structuredClone(item.tool_calls) as AgentTranscriptMessage['tool_calls'] } : {}),
      ...(typeof item.tool_call_id === 'string' ? { tool_call_id: item.tool_call_id } : {}),
      ...(typeof item.name === 'string' ? { name: item.name } : {})
    }]
  })
}

function messageFingerprint(message: AgentTranscriptMessage): string {
  return checksum({
    role: message.role,
    content: message.content,
    error: message.error,
    tool_calls: message.tool_calls,
    tool_call_id: message.tool_call_id,
    name: message.name
  })
}

function sessionSummary(session: NodeHistorySession): NodeHistorySessionSummary {
  const { canvasObservation: _canvasObservation, messages: _messages, ...summary } = session
  return structuredClone(summary)
}

function blankDocument(): NodeHistoryDocument {
  return {
    schema: NODE_HISTORY_DOCUMENT_SCHEMA,
    version: NODE_HISTORY_DOCUMENT_VERSION,
    sessions: [],
    runs: [],
    revisions: []
  }
}

function historyMessage(value: unknown): value is NodeHistoryMessage {
  return isRecord(value)
    && typeof value.id === 'string'
    && typeof value.workspaceId === 'string'
    && typeof value.nodeId === 'string'
    && typeof value.sessionId === 'string'
    && (value.role === 'user' || value.role === 'assistant' || value.role === 'tool' || value.role === 'thinking')
    && (typeof value.content === 'string' || value.content === null)
    && (value.visibility === 'public' || value.visibility === 'internal')
    && typeof value.createdAt === 'string'
}

function historySession(value: unknown): value is NodeHistorySession {
  return isRecord(value)
    && typeof value.id === 'string'
    && typeof value.workspaceId === 'string'
    && typeof value.nodeId === 'string'
    && typeof value.nodeType === 'string'
    && typeof value.title === 'string'
    && typeof value.createdAt === 'string'
    && typeof value.updatedAt === 'string'
    && typeof value.messageCount === 'number'
    && (value.canvasObservation === undefined || isCanvasAgentObservation(value.canvasObservation))
    && Array.isArray(value.messages)
    && value.messages.every(historyMessage)
}

function historyRun(value: unknown): value is NodeHistoryRun {
  return isRecord(value)
    && typeof value.id === 'string'
    && typeof value.workspaceId === 'string'
    && typeof value.nodeId === 'string'
    && typeof value.nodeType === 'string'
    && typeof value.rootRunId === 'string'
    && runStatus(value.status) === value.status
    && typeof value.startedAt === 'string'
    && typeof value.updatedAt === 'string'
}

function historyRevision(value: unknown): value is NodeHistoryRevision {
  return isRecord(value)
    && typeof value.id === 'string'
    && typeof value.workspaceId === 'string'
    && typeof value.nodeId === 'string'
    && typeof value.nodeType === 'string'
    && (value.kind === 'content' || value.kind === 'state' || value.kind === 'asset')
    && typeof value.checksum === 'string'
    && typeof value.createdAt === 'string'
    && Object.prototype.hasOwnProperty.call(value, 'value')
}

function isHistoryDocument(value: unknown): value is NodeHistoryDocument {
  return isRecord(value)
    && value.schema === NODE_HISTORY_DOCUMENT_SCHEMA
    && value.version === NODE_HISTORY_DOCUMENT_VERSION
    && Array.isArray(value.sessions)
    && Array.isArray(value.runs)
    && Array.isArray(value.revisions)
    && value.sessions.every(historySession)
    && value.runs.every(historyRun)
    && value.revisions.every(historyRevision)
}

function migrateLegacyHistoryDocument(value: unknown): NodeHistoryDocument | null {
  if (!isRecord(value)
    || value.schema !== NODE_HISTORY_DOCUMENT_SCHEMA
    || value.version !== 1
    || !Array.isArray(value.sessions)
    || !Array.isArray(value.runs)
    || !Array.isArray(value.revisions)) return null
  const migrated: NodeHistoryDocument = {
    schema: NODE_HISTORY_DOCUMENT_SCHEMA,
    version: NODE_HISTORY_DOCUMENT_VERSION,
    sessions: value.sessions.map((raw) => {
      if (!isRecord(raw)) return raw as NodeHistorySession
      return {
        ...raw,
        workspaceId: PROJECT_NODE_HISTORY_WORKSPACE_ID,
        messages: Array.isArray(raw.messages)
          ? raw.messages.map((message) => isRecord(message)
              ? { ...message, workspaceId: PROJECT_NODE_HISTORY_WORKSPACE_ID }
              : message)
          : raw.messages
      } as NodeHistorySession
    }),
    runs: value.runs.map((run) => isRecord(run)
      ? { ...run, workspaceId: PROJECT_NODE_HISTORY_WORKSPACE_ID } as NodeHistoryRun
      : run as NodeHistoryRun),
    revisions: value.revisions.map((revision) => isRecord(revision)
      ? { ...revision, workspaceId: PROJECT_NODE_HISTORY_WORKSPACE_ID } as NodeHistoryRevision
      : revision as NodeHistoryRevision)
  }
  return isHistoryDocument(migrated) ? migrated : null
}

function inferredTitle(messages: readonly AgentTranscriptMessage[], fallback: string): string {
  const first = messages.find((message) => message.role === 'user' && text(message.content))
  const value = text(first?.content, 120)
  return value || fallback
}

function sessionTitle(
  requested: string | undefined,
  messages: readonly AgentTranscriptMessage[],
  current: string
): string {
  const explicit = text(requested, 120)
  if (explicit) return explicit
  if (current && current !== 'New session' && current !== 'New conversation') return current
  return inferredTitle(messages, current || 'New session')
}

function projectionRun(
  workspaceId: string,
  node: CanvasNodeProjection,
  value: Record<string, unknown>,
  now: string
): NodeHistoryRun | null {
  const nodeType = text(node.type, 120) || 'unknown'
  const explicitId = text(value.id, 240)
  const rawStartedAt = value.startedAt ?? value.lastRunStartedAt ?? value.createdAt
  const startedAt = timestamp(rawStartedAt, now)
  const output = value.output ?? value.result ?? value.lastResult
  const summary = text(value.summary ?? value.command, 20_000)
  const hasRunData = Boolean(
    explicitId
    || summary
    || value.startedAt !== undefined
    || value.lastRunStartedAt !== undefined
    || output !== undefined
    || value.lastStdout !== undefined
    || value.lastStderr !== undefined
  )
  if (!hasRunData) return null
  const id = explicitId || `projection-${checksum(
    rawStartedAt === undefined
      ? [node.id, summary, output, value.lastStdout, value.lastStderr]
      : [node.id, startedAt]
  ).slice(0, 32)}`
  const completedAt = timestamp(value.completedAt ?? value.lastRunCompletedAt, '')
  const projectedOutput = output !== undefined
    ? structuredClone(output)
    : {
        ...(value.lastStdout !== undefined ? { stdout: value.lastStdout } : {}),
        ...(value.lastStderr !== undefined ? { stderr: value.lastStderr } : {}),
        ...(value.lastExitCode !== undefined ? { exitCode: value.lastExitCode } : {}),
        ...(value.lastDurationMs !== undefined ? { durationMs: value.lastDurationMs } : {})
      }
  return {
    id,
    workspaceId,
    nodeId: node.id,
    nodeType,
    rootRunId: text(value.rootRunId, 240) || id,
    ...(text(value.parentRunId, 240) ? { parentRunId: text(value.parentRunId, 240) } : {}),
    ...(text(value.sessionId, 240) ? { sessionId: text(value.sessionId, 240) } : {}),
    status: runStatus(value.status),
    startedAt,
    updatedAt: completedAt || now,
    ...(completedAt ? { completedAt } : {}),
    ...(value.input !== undefined ? { input: structuredClone(value.input) } : {}),
    ...(Object.keys(isRecord(projectedOutput) ? projectedOutput : { value: projectedOutput }).length > 0
      ? { output: projectedOutput }
      : {}),
    ...(summary ? { summary } : {}),
    ...(text(value.error ?? value.lastError, 20_000)
      ? { error: text(value.error ?? value.lastError, 20_000) }
      : {})
  }
}

export function stripNodeHistoryProjections(value: unknown): unknown {
  if (!Array.isArray(value)) return value
  return value.map((raw) => {
    if (!isRecord(raw) || !isRecord(raw.data)) return raw
    const data = { ...raw.data }
    if (raw.type === 'agent') delete data.messages
    if (raw.type === 'cli') delete data.history
    if (raw.type === 'harness') delete data.invocations
    if (raw.type === 'note' || raw.type === 'document') delete data.snapshots
    delete data.taskLog
    delete data.historySessionId
    return { ...raw, data }
  })
}

export class JsonNodeHistoryStore {
  private readonly path: string
  private readonly legacyPath: string
  private loaded?: Promise<NodeHistoryDocument>
  private queue: Promise<unknown> = Promise.resolve()

  constructor(
    projectRoot: string,
    private readonly clock: () => Date = () => new Date()
  ) {
    if (!projectRoot.trim()) throw new Error('projectRoot is required for node history.')
    this.path = resolve(projectRoot, HISTORY_PATH)
    this.legacyPath = resolve(projectRoot, LEGACY_HISTORY_PATH)
  }

  async initialize(): Promise<void> {
    await this.document()
  }

  async snapshot(workspaceId: string, nodeId: string): Promise<NodeHistorySnapshot> {
    const scopeId = this.required(workspaceId, 'workspaceId')
    const id = this.required(nodeId, 'nodeId')
    const document = await this.document()
    return {
      workspaceId: scopeId,
      nodeId: id,
      sessions: document.sessions
        .filter((session) => session.workspaceId === scopeId && session.nodeId === id)
        .sort((left, right) => right.updatedAt.localeCompare(left.updatedAt))
        .map(sessionSummary),
      runs: document.runs
        .filter((run) => run.workspaceId === scopeId && run.nodeId === id)
        .sort((left, right) => right.updatedAt.localeCompare(left.updatedAt))
        .map((run) => structuredClone(run)),
      revisions: document.revisions
        .filter((revision) => revision.workspaceId === scopeId && revision.nodeId === id)
        .sort((left, right) => right.createdAt.localeCompare(left.createdAt))
        .map((revision) => structuredClone(revision))
    }
  }

  async getSession(
    workspaceId: string,
    nodeId: string,
    sessionId: string
  ): Promise<NodeHistorySession | null> {
    const scopeId = this.required(workspaceId, 'workspaceId')
    const id = this.required(nodeId, 'nodeId')
    const session = (await this.document()).sessions.find((candidate) =>
      candidate.workspaceId === scopeId && candidate.nodeId === id && candidate.id === sessionId)
    return session ? structuredClone(session) : null
  }

  async createSession(command: CreateNodeHistorySessionCommand): Promise<NodeHistorySession> {
    return this.mutate((document) => {
      const now = this.clock().toISOString()
      const workspaceId = this.required(command.workspaceId, 'workspaceId')
      const nodeId = this.required(command.nodeId, 'nodeId')
      const nodeType = this.required(command.nodeType, 'nodeType')
      const session: NodeHistorySession = {
        id: randomUUID(),
        workspaceId,
        nodeId,
        nodeType,
        title: text(command.title, 120) || 'New session',
        createdAt: now,
        updatedAt: now,
        messageCount: 0,
        messages: []
      }
      document.sessions.push(session)
      return structuredClone(session)
    })
  }

  async saveSession(command: SaveNodeHistorySessionCommand): Promise<NodeHistorySession> {
    return this.mutate((document) => {
      const now = this.clock().toISOString()
      const workspaceId = this.required(command.workspaceId, 'workspaceId')
      const nodeId = this.required(command.nodeId, 'nodeId')
      const nodeType = this.required(command.nodeType, 'nodeType')
      const sessionId = this.required(command.sessionId, 'sessionId')
      let session = document.sessions.find((candidate) =>
        candidate.workspaceId === workspaceId
        && candidate.nodeId === nodeId
        && candidate.id === sessionId)
      if (!session) {
        session = {
          id: sessionId,
          workspaceId,
          nodeId,
          nodeType,
          title: text(command.title, 120) || 'New session',
          createdAt: now,
          updatedAt: now,
          messageCount: 0,
          messages: [],
          ...(text(command.conversationId, 240) ? { conversationId: text(command.conversationId, 240) } : {}),
          ...(text(command.subjectUserId, 500) ? { subjectUserId: text(command.subjectUserId, 500) } : {}),
          ...(text(command.exposureId, 500) ? { exposureId: text(command.exposureId, 500) } : {}),
          ...(text(command.releaseChecksum, 240) ? { releaseChecksum: text(command.releaseChecksum, 240) } : {})
        }
        document.sessions.push(session)
      }
      const messages = transcript(command.messages)
      const existingByFingerprint = new Map<string, NodeHistoryMessage[]>()
      for (const message of session.messages) {
        const key = messageFingerprint(message)
        const values = existingByFingerprint.get(key) ?? []
        values.push(message)
        existingByFingerprint.set(key, values)
      }
      const nextMessages = messages.map((message): NodeHistoryMessage => {
        const key = messageFingerprint(message)
        const existing = existingByFingerprint.get(key)?.shift()
        return existing
          ? { ...structuredClone(existing), ...structuredClone(message) }
          : {
              ...structuredClone(message),
              id: randomUUID(),
              workspaceId,
              nodeId,
              sessionId,
              ...(command.run?.id ? { runId: command.run.id } : {}),
              ...(text(command.conversationId, 240) ? { conversationId: text(command.conversationId, 240) } : {}),
              visibility: message.role === 'tool' || message.role === 'thinking' ? 'internal' : 'public',
              createdAt: now
            }
      })
      session.nodeType = nodeType
      session.messages = nextMessages
      session.messageCount = nextMessages.length
      session.updatedAt = now
      session.title = sessionTitle(command.title, messages, session.title)
      if (text(command.conversationId, 240)) session.conversationId = text(command.conversationId, 240)
      if (text(command.subjectUserId, 500)) session.subjectUserId = text(command.subjectUserId, 500)
      if (text(command.exposureId, 500)) session.exposureId = text(command.exposureId, 500)
      if (text(command.releaseChecksum, 240)) session.releaseChecksum = text(command.releaseChecksum, 240)
      if (command.canvasObservation !== undefined) {
        if (!isCanvasAgentObservation(command.canvasObservation)) {
          throw new Error('canvasObservation is invalid.')
        }
        session.canvasObservation = structuredClone(command.canvasObservation)
      }
      if (command.run) {
        session.lastRunId = command.run.id
        const existingRun = document.runs.find((candidate) =>
          candidate.workspaceId === workspaceId && candidate.id === command.run!.id)
        const startedAt = timestamp(command.run.startedAt, existingRun?.startedAt ?? now)
        const completedAt = timestamp(command.run.completedAt, existingRun?.completedAt ?? '')
        const run: NodeHistoryRun = {
          id: command.run.id,
          workspaceId,
          nodeId,
          nodeType,
          rootRunId: command.run.rootRunId || existingRun?.rootRunId || command.run.id,
          ...((command.run.parentRunId || existingRun?.parentRunId)
            ? { parentRunId: command.run.parentRunId || existingRun?.parentRunId }
            : {}),
          sessionId,
          ...(text(command.conversationId, 240)
            ? { conversationId: text(command.conversationId, 240) }
            : existingRun?.conversationId
              ? { conversationId: existingRun.conversationId }
              : {}),
          status: runStatus(command.run.status),
          startedAt,
          updatedAt: completedAt || now,
          ...(completedAt ? { completedAt } : {}),
          ...((command.run.definitionChecksum || existingRun?.definitionChecksum)
            ? { definitionChecksum: command.run.definitionChecksum || existingRun?.definitionChecksum }
            : {}),
          ...(command.run.input !== undefined
            ? { input: structuredClone(command.run.input) }
            : existingRun?.input !== undefined
              ? { input: structuredClone(existingRun.input) }
              : {}),
          ...(command.run.output !== undefined
            ? { output: structuredClone(command.run.output) }
            : existingRun?.output !== undefined
              ? { output: structuredClone(existingRun.output) }
              : {}),
          ...((command.run.summary || existingRun?.summary)
            ? { summary: command.run.summary || existingRun?.summary }
            : {}),
          ...((command.run.error || existingRun?.error)
            ? { error: command.run.error || existingRun?.error }
            : {})
        }
        const runIndex = document.runs.findIndex((candidate) =>
          candidate.workspaceId === workspaceId && candidate.id === run.id)
        if (runIndex >= 0) document.runs[runIndex] = run
        else document.runs.push(run)
      }
      return structuredClone(session)
    })
  }

  async saveRun(command: SaveNodeHistoryRunCommand): Promise<NodeHistoryRun> {
    return this.mutate((document) => {
      const now = this.clock().toISOString()
      const id = this.required(command.id, 'run id')
      const workspaceId = this.required(command.workspaceId, 'workspaceId')
      const nodeId = this.required(command.nodeId, 'nodeId')
      const nodeType = this.required(command.nodeType, 'nodeType')
      const existing = document.runs.find((candidate) =>
        candidate.workspaceId === workspaceId && candidate.id === id)
      const startedAt = timestamp(command.startedAt, existing?.startedAt ?? now)
      const completedAt = timestamp(command.completedAt, existing?.completedAt ?? '')
      const run: NodeHistoryRun = {
        id,
        workspaceId,
        nodeId,
        nodeType,
        rootRunId: text(command.rootRunId, 240) || existing?.rootRunId || id,
        ...((text(command.parentRunId, 240) || existing?.parentRunId)
          ? { parentRunId: text(command.parentRunId, 240) || existing?.parentRunId! }
          : {}),
        ...((text(command.sessionId, 240) || existing?.sessionId)
          ? { sessionId: text(command.sessionId, 240) || existing?.sessionId! }
          : {}),
        ...((text(command.conversationId, 240) || existing?.conversationId)
          ? { conversationId: text(command.conversationId, 240) || existing?.conversationId! }
          : {}),
        status: runStatus(command.status),
        startedAt,
        updatedAt: completedAt || now,
        ...(completedAt ? { completedAt } : {}),
        ...((text(command.definitionChecksum, 240) || existing?.definitionChecksum)
          ? { definitionChecksum: text(command.definitionChecksum, 240) || existing?.definitionChecksum! }
          : {}),
        ...(command.input !== undefined
          ? { input: structuredClone(command.input) }
          : existing?.input !== undefined
            ? { input: structuredClone(existing.input) }
            : {}),
        ...(command.output !== undefined
          ? { output: structuredClone(command.output) }
          : existing?.output !== undefined
            ? { output: structuredClone(existing.output) }
            : {}),
        ...((text(command.summary, 20_000) || existing?.summary)
          ? { summary: text(command.summary, 20_000) || existing?.summary! }
          : {}),
        ...((text(command.error, 20_000) || existing?.error)
          ? { error: text(command.error, 20_000) || existing?.error! }
          : {})
      }
      const index = document.runs.findIndex((candidate) =>
        candidate.workspaceId === workspaceId && candidate.id === id)
      if (index >= 0) document.runs[index] = run
      else document.runs.push(run)
      return structuredClone(run)
    })
  }

  async deleteSession(workspaceId: string, nodeId: string, sessionId: string): Promise<boolean> {
    return this.mutate((document) => {
      const scopeId = this.required(workspaceId, 'workspaceId')
      const ownerId = this.required(nodeId, 'nodeId')
      const id = this.required(sessionId, 'sessionId')
      const index = document.sessions.findIndex((session) =>
        session.workspaceId === scopeId && session.nodeId === ownerId && session.id === id)
      if (index < 0) return false
      document.sessions.splice(index, 1)
      document.runs = document.runs.filter((run) =>
        run.workspaceId !== scopeId || run.nodeId !== ownerId || run.sessionId !== id)
      return true
    })
  }

  async deleteNode(workspaceId: string, nodeId: string): Promise<boolean> {
    return this.mutate((document) => {
      const scopeId = this.required(workspaceId, 'workspaceId')
      const id = this.required(nodeId, 'nodeId')
      const sessionCount = document.sessions.length
      const runCount = document.runs.length
      const revisionCount = document.revisions.length
      document.sessions = document.sessions.filter((session) =>
        session.workspaceId !== scopeId || session.nodeId !== id)
      document.runs = document.runs.filter((run) =>
        run.workspaceId !== scopeId || run.nodeId !== id)
      document.revisions = document.revisions.filter((revision) =>
        revision.workspaceId !== scopeId || revision.nodeId !== id)
      return sessionCount !== document.sessions.length
        || runCount !== document.runs.length
        || revisionCount !== document.revisions.length
    })
  }

  async captureCanvasNodes(value: unknown): Promise<void> {
    await this.captureWorkspaceNodes(PROJECT_NODE_HISTORY_WORKSPACE_ID, value)
  }

  async captureWorkspaceNodes(workspaceId: string, value: unknown): Promise<void> {
    const scopeId = this.required(workspaceId, 'workspaceId')
    const nodes = nodeRecords(value)
    if (nodes.length === 0) return
    await this.mutate((document) => {
      const now = this.clock().toISOString()
      for (const node of nodes) {
        const data = isRecord(node.data) ? node.data : {}
        const nodeType = text(node.type, 120) || 'unknown'
        if (nodeType === 'agent' && Array.isArray(data.messages)) {
          const messages = transcript(data.messages)
          if (messages.length > 0) {
            let session = document.sessions
              .filter((candidate) =>
                candidate.workspaceId === scopeId && candidate.nodeId === node.id)
              .sort((left, right) => right.updatedAt.localeCompare(left.updatedAt))[0]
            if (!session) {
              session = {
                id: randomUUID(),
                workspaceId: scopeId,
                nodeId: node.id,
                nodeType,
                title: inferredTitle(messages, 'Imported session'),
                createdAt: now,
                updatedAt: now,
                messageCount: 0,
                messages: []
              }
              document.sessions.push(session)
            }
            if (session.messages.length === 0) {
              session.messages = messages.map((message) => ({
                ...structuredClone(message),
                id: randomUUID(),
                workspaceId: scopeId,
                nodeId: node.id,
                sessionId: session!.id,
                visibility: message.role === 'tool' || message.role === 'thinking' ? 'internal' : 'public',
                createdAt: now
              }))
              session.messageCount = session.messages.length
              session.updatedAt = now
            }
          }
        }
        const projectionRecords = nodeType === 'agent'
          ? []
          : nodeType === 'cli'
            ? Array.isArray(data.history)
              ? data.history.flatMap((value) => {
                  const record = migratedCliHistoryRecord(value)
                  return record ? [record] : []
                })
              : []
            : nodeType === 'harness' && Array.isArray(data.invocations)
              ? data.invocations.filter(isRecord)
              : [data]
        for (const projection of projectionRecords) {
          const run = projectionRun(
            scopeId,
            node,
            nodeType === 'cli' || nodeType === 'harness'
              ? { ...projection, output: projection }
              : projection,
            now
          )
          if (!run) continue
          const existingRunIndex = document.runs.findIndex((candidate) =>
            candidate.workspaceId === scopeId && candidate.id === run.id)
          if (existingRunIndex >= 0) {
            const existingRun = document.runs[existingRunIndex]!
            document.runs[existingRunIndex] = {
              ...existingRun,
              ...run,
              startedAt: existingRun.startedAt
            }
          } else {
            document.runs.push(run)
          }
        }
        const revisionValue = nodeType === 'note' || nodeType === 'document'
          ? data.content
          : nodeType === 'image'
            ? data.path !== undefined || data.src !== undefined || data.prompt !== undefined
              ? { path: data.path, src: data.src, prompt: data.prompt }
              : undefined
            : nodeType === 'app'
              ? data.code !== undefined || data.props !== undefined || data.state !== undefined || data.dependencies !== undefined
                ? { code: data.code, props: data.props, state: data.state, dependencies: data.dependencies }
                : undefined
              : nodeType === 'files'
                ? data.path !== undefined ? { path: data.path } : undefined
                : undefined
        if ((nodeType === 'note' || nodeType === 'document') && Array.isArray(data.snapshots)) {
          for (const snapshot of data.snapshots.filter(isRecord)) {
            if (typeof snapshot.content !== 'string') continue
            const valueChecksum = checksum(snapshot.content)
            const latest = document.revisions
              .filter((revision) =>
                revision.workspaceId === scopeId && revision.nodeId === node.id)
              .sort((left, right) => right.createdAt.localeCompare(left.createdAt))[0]
            if (latest?.checksum === valueChecksum) continue
            const requestedId = text(snapshot.id, 240)
            document.revisions.push({
              id: requestedId && !document.revisions.some((revision) => revision.id === requestedId)
                ? requestedId
                : randomUUID(),
              workspaceId: scopeId,
              nodeId: node.id,
              nodeType,
              kind: 'content',
              checksum: valueChecksum,
              createdAt: timestamp(snapshot.createdAt, now),
              ...(latest ? { parentRevisionId: latest.id } : {}),
              value: snapshot.content
            })
          }
        }
        if (revisionValue !== undefined) {
          const valueChecksum = checksum(revisionValue)
          const latest = document.revisions
            .filter((revision) =>
              revision.workspaceId === scopeId && revision.nodeId === node.id)
            .sort((left, right) => right.createdAt.localeCompare(left.createdAt))[0]
          if (latest?.checksum !== valueChecksum) {
            document.revisions.push({
              id: randomUUID(),
              workspaceId: scopeId,
              nodeId: node.id,
              nodeType,
              kind: nodeType === 'image' || nodeType === 'files' ? 'asset' : 'content',
              checksum: valueChecksum,
              createdAt: now,
              ...(latest ? { parentRevisionId: latest.id } : {}),
              value: structuredClone(revisionValue)
            })
          }
        }
      }
    })
  }

  async projectCanvasNodes(value: unknown): Promise<unknown> {
    if (!Array.isArray(value)) return value
    const document = await this.document()
    return value.map((raw) => {
      if (!isRecord(raw) || typeof raw.id !== 'string') return raw
      const data = isRecord(raw.data) ? { ...raw.data } : {}
      if (raw.type === 'agent') {
        const latest = document.sessions
          .filter((session) =>
            session.workspaceId === PROJECT_NODE_HISTORY_WORKSPACE_ID && session.nodeId === raw.id)
          .sort((left, right) => right.updatedAt.localeCompare(left.updatedAt))[0]
        if (latest) {
          data.messages = latest.messages.map(({
            id: _id,
            workspaceId: _workspaceId,
            nodeId: _nodeId,
            sessionId: _sessionId,
            runId: _runId,
            conversationId: _conversationId,
            visibility: _visibility,
            createdAt: _createdAt,
            ...message
          }) => message)
          data.historySessionId = latest.id
        }
      }
      if (raw.type === 'cli') {
        const records = document.runs
          .filter((run) =>
            run.workspaceId === PROJECT_NODE_HISTORY_WORKSPACE_ID
            && run.nodeId === raw.id)
          .sort((left, right) => left.startedAt.localeCompare(right.startedAt))
          .flatMap((run) => {
            const record = migratedCliHistoryRecord(run.output)
            return record ? [record] : []
          })
        if (records.length > 0) data.history = records
      }
      if (raw.type === 'harness') {
        const records = document.runs
          .filter((run) =>
            run.workspaceId === PROJECT_NODE_HISTORY_WORKSPACE_ID
            && run.nodeId === raw.id
            && isRecord(run.output))
          .sort((left, right) => left.startedAt.localeCompare(right.startedAt))
          .map((run) => structuredClone(run.output))
        if (records.length > 0) data.invocations = records
      }
      return { ...raw, data }
    })
  }

  private required(value: unknown, label: string): string {
    const normalized = text(value, 240)
    if (!normalized) throw new Error(`${label} is required.`)
    return normalized
  }

  private async document(): Promise<NodeHistoryDocument> {
    if (!this.loaded) {
      this.loaded = (async () => {
        try {
          const parsed = JSON.parse(await fsp.readFile(this.path, 'utf8')) as unknown
          if (!isHistoryDocument(parsed)) {
            throw new Error(`Node history is invalid or uses an unsupported schema: ${this.path}`)
          }
          return structuredClone(parsed)
        } catch (error) {
          if (isRecord(error) && error.code !== 'ENOENT') throw error
          try {
            const legacy = JSON.parse(await fsp.readFile(this.legacyPath, 'utf8')) as unknown
            const migrated = migrateLegacyHistoryDocument(legacy)
            if (!migrated) {
              throw new Error(`Node history is invalid or uses an unsupported schema: ${this.legacyPath}`)
            }
            await this.write(migrated)
            return migrated
          } catch (legacyError) {
            if (isRecord(legacyError) && legacyError.code !== 'ENOENT') throw legacyError
            return blankDocument()
          }
        }
      })().catch((error) => {
        this.loaded = undefined
        throw error
      })
    }
    return this.loaded
  }

  private async mutate<T>(operation: (document: NodeHistoryDocument) => T | Promise<T>): Promise<T> {
    const queued = this.queue.then(async () => {
      const document = await this.document()
      const result = await operation(document)
      await this.write(document)
      return result
    })
    this.queue = queued.then(() => undefined, () => undefined)
    return queued
  }

  private async write(document: NodeHistoryDocument): Promise<void> {
    await fsp.mkdir(dirname(this.path), { recursive: true, mode: 0o700 })
    const temporary = `${this.path}.${randomUUID()}.tmp`
    await fsp.writeFile(temporary, `${JSON.stringify(document, null, 2)}\n`, {
      encoding: 'utf8',
      mode: 0o600
    })
    await fsp.rename(temporary, this.path)
  }
}
