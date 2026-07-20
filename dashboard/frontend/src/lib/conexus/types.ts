export interface ConexusStatus {
  available: boolean
  mode?: "published_harness" | "not_configured"
  publication?: string
  error?: string
}

export interface PublishedHarnessInput {
  key: string
  label: string
  description: string
  type: "string" | "text" | "number" | "boolean" | "file" | "url" | "json" | "asset"
  required: boolean
  defaultValue?: unknown
}

export interface PublishedHarnessManifest {
  accessPolicy: "anonymous" | "conexus_account"
  billingPolicy: "publisher" | "consumer"
  inputs: PublishedHarnessInput[]
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

export interface LocalAgentConversationMessage {
  id: string
  role: "user" | "assistant"
  content: string
  createdAt: string
  runId: string
  artifacts?: PublishedHarnessArtifact[]
  error?: boolean
}

export interface LocalAgentConversation {
  id: string
  title: string
  createdAt: string
  updatedAt: string
  messages: LocalAgentConversationMessage[]
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

export interface PublishedHarnessToolActivity {
  callId: string
  name: string
  ownerNodeId: string
  success?: boolean
  message?: string
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
  output?: Record<string, unknown>
  artifacts?: PublishedHarnessArtifact[]
  error?: { code: string; message: string }
  pendingInteraction?: PublishedHarnessInteraction
}

export interface PublishedHarnessRunEvent {
  sequence: number
  runId: string
  at: string
  type:
    | "run.queued"
    | "run.started"
    | "run.tool_started"
    | "run.tool_completed"
    | "run.completed"
    | "run.blocked"
    | "run.failed"
    | "run.cancelled"
    | "run.interaction_requested"
    | "run.interaction_resolved"
  status: PublishedHarnessRunStatus
  interaction?: PublishedHarnessInteraction
  tool?: PublishedHarnessToolActivity
}

export interface AgentToolActivity extends PublishedHarnessToolActivity {
  state: "running" | "completed"
  at: string
}
