export interface ConexusStatus {
  available: boolean
  mode?: "published_harness" | "local_harness" | "not_configured"
  model_configured?: boolean
  publication?: string
  error?: string
}

export type PublishedHarnessExposureSurface = "agent_tool" | "page" | "api"

export interface HostedHarnessExposure {
  id: string
  nodeType: string
  surfaces: PublishedHarnessExposureSurface[]
}

export interface HostedHarnessManifest {
  identityPolicy?: "enterprise"
  billingPolicy?: "publisher" | "consumer"
  exposures: HostedHarnessExposure[]
  defaultExposureId?: string
}

export type PublishedHarnessArtifact = {
  id: string
  runId: string
  title: string
  createdAt: string
  producerNodeId?: string
  outputKey?: string
} & (
  | { kind: "document"; content: { markdown: string } }
  | { kind: "image"; content: { src: string; caption?: string } }
  | { kind: "file"; content: { name: string; path?: string; url?: string } }
  | { kind: "json"; content: { value: unknown } }
  | {
      kind: "node"
      content: {
        node: {
          id: string
          type: string
          label: string
          description?: string
          values: Record<string, unknown>
        }
      }
    }
)

export interface AgentConversationMessage {
  id: string
  role: "user" | "assistant"
  content: string
  createdAt: string
  runId: string
  artifacts?: PublishedHarnessArtifact[]
  error?: boolean
}

export interface AgentConversation {
  id: string
  title: string
  createdAt: string
  updatedAt: string
  messages: AgentConversationMessage[]
  researchCheckpoint?: AgentResearchCheckpoint
}

export interface AgentResearchCheckpoint {
  version: 1
  runId: string
  updatedAt: string
  decisionNotebook?: Record<string, unknown>
  workspaceResult?: Record<string, unknown>
}

export type PublishedHarnessRunStatus =
  | "queued"
  | "running"
  | "completed"
  | "blocked"
  | "failed"
  | "cancelled"

export interface PublishedHarnessInteraction {
  id: string
  kind: "question"
  ownerNodeId: string
  question: string
  choices?: string[]
}

export interface PublishedHarnessRun {
  id: string
  slug: string
  version: number
  status: PublishedHarnessRunStatus
  createdAt: string
  startedAt?: string
  completedAt?: string
  summary?: string
  result?: unknown
  workspaceRevision?: number
  nodeChanges?: { created: string[]; updated: string[]; deleted: string[] }
  error?: { code: string; message: string }
  pendingInteraction?: PublishedHarnessInteraction
}

export interface PublishedHarnessWorkspaceOutput {
  id: string
  type: string
  label: string
  description?: string
  values: Record<string, unknown>
}

export interface PublishedHarnessWorkspaceNode extends PublishedHarnessWorkspaceOutput {
  createdAt: string
  updatedAt: string
  createdByRunId: string
  updatedByRunId: string
}

export interface PublishedHarnessWorkspaceSnapshot {
  outputValidation?: {
    status: "succeeded" | "failed"
    error_code?: string
    error_summary?: string
    errors?: string[]
  }
  workspaceId: string
  slug: string
  releaseChecksum?: string
  revision: number
  updatedAt?: string
  nodes: PublishedHarnessWorkspaceNode[]
}

export interface PublishedHarnessToolCall {
  id: string
  type: "function"
  function: { name: string; arguments: string }
}

export interface PublishedHarnessTranscriptMessage {
  role: "assistant" | "tool"
  content: string
  tool_calls?: PublishedHarnessToolCall[]
  tool_call_id?: string
  name?: string
}

export interface PublishedHarnessRunEvent {
  sequence: number
  runId: string
  at: string
  type:
    | "run.queued"
    | "run.started"
    | "run.message"
    | "run.completed"
    | "run.blocked"
    | "run.failed"
    | "run.cancelled"
    | "run.interaction_requested"
    | "run.interaction_resolved"
  status: PublishedHarnessRunStatus
  ownerNodeId?: string
  message?: PublishedHarnessTranscriptMessage
  interaction?: PublishedHarnessInteraction
}

export interface AgentToolActivity {
  callId: string
  name: string
  ownerNodeId: string
  success?: boolean
  message?: string
  state: "running" | "completed"
  at: string
}
